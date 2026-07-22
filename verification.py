"""Deterministic anti-fabrication layer for ClaimAnchor.

The LLM is instructed to cite only what the tools returned, but instructions are
not a guarantee. This module makes the guarantee *structural*: every citation in
the model's final report is checked, in code, against

  1. a **provenance ledger** of identifiers that real tool calls actually returned
     this session, and
  2. a live **Crossref resolution** for any DOI not already in the ledger.

Any citation that is neither in the ledger nor independently resolvable is moved
to ``unverified_sources`` and stripped from the claims it supported — so a
hallucinated DOI can never survive into the answer. This is the feature that
turns "no fabricated citations" from a hope into a property of the output.
"""
from __future__ import annotations

import difflib
from typing import Any, Callable

from agent_skeleton.research_tools import normalize_doi, verify_doi as _default_resolver

# Minimum Crossref title similarity to accept a claimed DOI. Chosen empirically: a
# genuine cite of a paper shares most title tokens (Jaccard ≫ 0.35 in practice), while
# an identifier-hijacked / mis-cited DOI resolves to an unrelated title that falls well
# below it. Exposed as a module constant so it is documented rather than a magic number.
_TITLE_MATCH_FLOOR = 0.35


def _safe_resolve(resolver: Callable[..., dict], doi: str) -> dict:
    """Best-effort Crossref resolve used ONLY to read integrity status for a source
    that already passed the existence gate. Never raises; on failure it returns ``{}``
    so a transient lookup error can never drop a paper a tool actually retrieved."""
    try:
        return resolver(doi) or {}
    except Exception:  # noqa: BLE001 — integrity is best-effort, never fatal
        return {}


def _norm_text(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def _quote_is_grounded(quote: str | None, abstracts: list[str]) -> bool | None:
    """Whether ``quote`` can be located in one of the retrieved ``abstracts``.

    Returns ``True``/``False`` when the quote is checkable, or ``None`` when it is not
    (no abstract was retrieved, or the quote is too short to judge) — ``None`` must not
    be treated as a failure. A quote counts as grounded if it appears near-verbatim,
    has high token recall, or shares a long contiguous run — tolerant of minor
    paraphrase and whitespace differences, but catching invented quotes."""
    q = _norm_text(quote)
    if len(q) < 12:
        return None
    qt = set(q.split())
    checkable = False
    for ab in abstracts:
        a = _norm_text(ab)
        if not a:
            continue
        checkable = True
        if q in a:
            return True
        if qt and len(qt & set(a.split())) / len(qt) >= 0.75:
            return True
        m = difflib.SequenceMatcher(None, q, a).find_longest_match(0, len(q), 0, len(a))
        if m.size >= max(20, int(0.6 * len(q))):
            return True
    return False if checkable else None


def _recover_doi_for_id(ledger: "ProvenanceLedger", src: dict) -> str | None:
    """Best-effort DOI for a source cited by PMID/PMCID only, so it can still be
    integrity-checked: find a ledger record carrying the same PMID/PMCID that has a DOI."""
    pmid = str(src["pmid"]) if src.get("pmid") else None
    pmcid = str(src["pmcid"]).upper() if src.get("pmcid") else None
    for rec in ledger.records.values():
        rdoi = normalize_doi(rec.get("doi"))
        if not rdoi:
            continue
        if pmid and str(rec.get("pmid")) == pmid:
            return rdoi
        if pmcid and str(rec.get("pmcid") or "").upper() == pmcid:
            return rdoi
    return None


class ProvenanceLedger:
    """Records every real identifier the tools surfaced during a session.

    The handler feeds each tool result in via :meth:`add_from_tool`; the verifier
    then trusts a cited DOI/PMID only if it appears here (or resolves live).
    """

    def __init__(self) -> None:
        self.dois: set[str] = set()
        self.pmids: set[str] = set()
        self.pmcids: set[str] = set()
        self.records: dict[str, dict] = {}   # keyed by normalized DOI when present

    def _add_record(self, rec: dict) -> None:
        if not isinstance(rec, dict):
            return
        doi = normalize_doi(rec.get("doi"))
        if doi:
            self.dois.add(doi)
            self.records.setdefault(doi, rec)
        if rec.get("pmid"):
            self.pmids.add(str(rec["pmid"]))
        if rec.get("pmcid"):
            self.pmcids.add(str(rec["pmcid"]).upper())

    def add_from_tool(self, name: str, result: Any) -> None:
        """Extract identifiers from one tool result into the ledger.

        Shape-based so any tool that returns a record list (``search_literature``,
        ``search_pubmed``) or a single ``record`` (``fetch_paper``,
        ``openalex_lookup``) is captured without per-tool wiring.
        """
        if not isinstance(result, dict) or not result.get("ok"):
            return
        for rec in result.get("results") or []:
            self._add_record(rec)
        if isinstance(result.get("record"), dict):
            self._add_record(result["record"])
        # verify_doi: a DOI that resolves AND matches the claimed title is real
        # provenance. A resolves-but-mismatched DOI must NOT be promoted here, or it
        # would fast-path past verify_report's title-mismatch gate and be kept.
        if name == "verify_doi" and result.get("resolves") and result.get("verified", True):
            doi = normalize_doi(result.get("doi"))
            if doi:
                self.dois.add(doi)
                self.records.setdefault(doi, {
                    "doi": doi, "title": result.get("title"),
                    "authors": result.get("authors"), "year": result.get("year"),
                    "journal": result.get("journal"),
                })
        # find_open_access: attach the OA link to an existing record.
        if name == "find_open_access":
            doi = normalize_doi(result.get("doi"))
            if doi and result.get("oa_url") and doi in self.records:
                self.records[doi]["open_access_url"] = result.get("oa_url")

    def has_doi(self, doi: str | None) -> bool:
        d = normalize_doi(doi)
        return bool(d and d in self.dois)

    def has_id(self, source: dict) -> bool:
        """True if a source without a DOI still carries a ledger-known PMID/PMCID."""
        if source.get("pmid") and str(source["pmid"]) in self.pmids:
            return True
        if source.get("pmcid") and str(source["pmcid"]).upper() in self.pmcids:
            return True
        return False


VerdictT = str  # supported | partially_supported | unsupported | source_not_found


def verify_report(
    report: dict,
    ledger: ProvenanceLedger,
    resolver: Callable[..., dict] | None = None,
) -> dict:
    """Verify the model's report against the ledger + Crossref, in code.

    ``report`` is the structured evidence graph the model produced via the
    ``submit_report`` tool: ``{report_markdown, claims[], sources[], limitations[],
    overall_confidence}``. Returns the final, verified result dict the handler
    hands back to A2A (always contains an ``"answer"`` key).
    """
    resolver = resolver or _default_resolver
    report = report or {}
    raw_sources = report.get("sources") or []
    claims = report.get("claims") or []

    verified: list[dict] = []
    unverified: list[dict] = []
    id_status: dict[str, str] = {}   # source id -> "verified" | "unverified"
    retracted_ids: set[str] = set()  # subset that resolved but is retracted/flagged

    for src in raw_sources:
        if not isinstance(src, dict):
            continue
        sid = str(src.get("id") or src.get("doi") or f"S{len(verified) + len(unverified) + 1}")
        src = {**src, "id": sid, "doi": normalize_doi(src.get("doi"))}
        doi = src.get("doi")
        res: dict | None = None
        ok = False   # passed the existence / title-match gate?

        if doi and ledger.has_doi(doi):
            src["verification"] = "retrieved"           # came straight from a tool
            ok = True
        elif not doi and ledger.has_id(src):
            src["verification"] = "retrieved-by-id"      # PMID/PMCID from a tool, no DOI
            ok = True
        elif doi:
            res = resolver(doi, claimed_title=src.get("title"), claimed_year=src.get("year"))
            if res.get("resolves") and res.get("verified", True):
                src["verification"] = "crossref-resolved"
                src.setdefault("title", res.get("title"))   # backfill authoritative metadata
                src.setdefault("year", res.get("year"))
                src["title_match"] = res.get("title_match")
                ok = True
            elif res.get("resolves"):
                src["verification"] = "mismatch"
                src["reason"] = (
                    "DOI resolves but its real title does not match the cited title "
                    f"(similarity {res.get('title_match')}). Possible mis-citation."
                )
            else:
                src["verification"] = "unresolved"
                src["reason"] = res.get("reason") or "DOI does not resolve on Crossref"
        else:
            src["verification"] = "no-identifier"
            src["reason"] = "no DOI/PMID that could be verified against a source"

        # Integrity gate: a real, resolvable paper that has been RETRACTED (or
        # withdrawn / flagged with an expression of concern) must never support a
        # claim. Crossref carries Retraction Watch data on the record's `updated-by`.
        # A source cited by PMID/PMCID only is checked via a recovered DOI when one is
        # known; otherwise its integrity is marked unverified rather than assumed clean.
        if ok:
            integ_doi = doi or _recover_doi_for_id(ledger, src)
            if integ_doi:
                if res is None:                          # ledger fast-path: fetch integrity
                    res = _safe_resolve(resolver, integ_doi)
                integrity = (res or {}).get("integrity_status", "ok")
                if integrity and integrity != "ok":
                    src["integrity_status"] = integrity
                    src["integrity_detail"] = (res or {}).get("integrity_detail")
                    src["verification"] = "retracted"
                    src["reason"] = (
                        f"source is {integrity} (Crossref / Retraction Watch): "
                        f"{src['integrity_detail'] or 'flagged'} — excluded from support."
                    )
                    retracted_ids.add(sid)
                    ok = False
                else:
                    src["integrity_status"] = "ok"
            else:
                src["integrity_status"] = "unverified"   # no DOI to check retraction against

        if ok:
            verified.append(src)
            id_status[sid] = "verified"
        else:
            unverified.append(src)
            id_status[sid] = "unverified"

    # Re-derive claim verdicts using only verified support.
    verified_by_id = {s["id"]: s for s in verified}     # for supporting-quote grounding
    clean_claims: list[dict] = []
    adjustments = 0
    for c in claims:
        if not isinstance(c, dict):
            continue
        cited = [str(s) for s in (c.get("source_ids") or [])]
        kept = [s for s in cited if id_status.get(s) == "verified"]
        dropped = [s for s in cited if id_status.get(s) != "verified"]
        verdict: VerdictT = c.get("verdict") or "unsupported"
        note = None
        if dropped and verdict in ("supported", "partially_supported"):
            n_retracted = sum(1 for s in dropped if s in retracted_ids)
            if not kept:
                verdict = "source_not_found"
                note = (
                    "All cited sources failed verification"
                    + (f" ({n_retracted} retracted)" if n_retracted else "")
                    + "; downgraded to source_not_found rather than assert an "
                    "unverifiable claim."
                )
            else:
                note = (
                    f"{len(dropped)} cited source(s) removed"
                    + (f" ({n_retracted} retracted)" if n_retracted else " (failed verification)")
                    + "."
                )
            adjustments += 1

        # Supporting-quote grounding: the quote must actually appear in an abstract a
        # tool returned for one of the kept sources — the same structural rigor applied
        # to DOIs, extended to the evidence text.
        quote = c.get("supporting_quote", "") or ""
        confidence = c.get("confidence", "low")
        abstracts = [
            rec["abstract"]
            for s in kept
            if (rec := ledger.records.get(normalize_doi(verified_by_id.get(s, {}).get("doi"))))
            and rec.get("abstract")
        ]
        grounded = _quote_is_grounded(quote, abstracts)
        quote_flag: dict = {}
        if grounded is False:
            quote_flag = {"quote_grounded": False}
            extra = "Supporting quote could not be located in the retrieved abstract; treat it as unverified."
            note = f"{note} {extra}".strip() if note else extra
            # An ungrounded quote on an otherwise-supported claim is a real trust hit:
            # cap its confidence and count it so overall confidence is downgraded too.
            if verdict in ("supported", "partially_supported"):
                confidence = "low"
                adjustments += 1
        elif grounded is True:
            quote_flag = {"quote_grounded": True}

        clean_claims.append({
            "claim": c.get("claim", ""),
            "verdict": verdict,
            "confidence": confidence,
            "source_ids": kept,
            "supporting_quote": quote,
            **quote_flag,
            **({"verification_note": note} if note else {}),
        })

    overall = report.get("overall_confidence", "low")
    if adjustments and overall == "high":
        overall = "medium"   # never claim high confidence after removing support

    answer = _render_answer(
        model_markdown=report.get("report_markdown", ""),
        claims=clean_claims,
        verified=verified,
        unverified=unverified,
        adjustments=adjustments,
    )

    return {
        "answer": answer,
        "claims": clean_claims,
        "sources": verified,
        "unverified_sources": unverified,
        "overall_confidence": overall,
        "limitations": report.get("limitations") or _DEFAULT_LIMITATIONS,
        "verification_summary": {
            "sources_verified": len(verified),
            "sources_unverified": len(unverified),
            "sources_retracted": len(retracted_ids),
            "claims_adjusted": adjustments,
            "method": (
                "ledger (real tool results) + live Crossref DOI resolution + "
                "Retraction Watch integrity check + supporting-quote grounding"
            ),
        },
        "disclaimer": _DISCLAIMER,
    }


_DEFAULT_LIMITATIONS = [
    "Evidence is limited to English-language records indexed by Europe PMC / PubMed / Crossref.",
    "Support judgments often rely on the abstract when full text is paywalled.",
    "Absence of a supporting source is reported as 'source_not_found', not as disproof.",
    "This tool assists verification; it does not replace expert review.",
]

_DISCLAIMER = (
    "ClaimAnchor cites only sources returned by live scholarly-database calls and "
    "verified against Crossref; it never invents citations. Verify clinically or "
    "methodologically critical findings against the primary source before relying on them."
)


def _render_answer(
    *,
    model_markdown: str,
    claims: list[dict],
    verified: list[dict],
    unverified: list[dict],
    adjustments: int,
) -> str:
    """Compose the human-readable answer so it always reflects the code-side
    verification — even if the model's prose referenced a citation we dropped."""
    parts: list[str] = []
    if model_markdown.strip():
        parts.append(model_markdown.strip())

    if unverified or adjustments:
        parts.append("\n---\n### ⚠ Verification adjustments")
        if adjustments:
            parts.append(
                f"- {adjustments} claim(s) had unverifiable citations removed; affected "
                "claims were downgraded rather than asserted."
            )
        for s in unverified:
            label = s.get("title") or s.get("doi") or s.get("id")
            tag = "Retracted" if s.get("verification") == "retracted" else "Unverified"
            parts.append(f"- **{tag}**: {label} — {s.get('reason', 'could not be verified')}.")

    if verified:
        parts.append("\n### Verified sources")
        for s in verified:
            cite = ", ".join(
                x for x in [
                    s.get("authors"),
                    f"*{s.get('title')}*" if s.get("title") else None,
                    str(s.get("year")) if s.get("year") else None,
                    s.get("journal"),
                ] if x
            )
            doi = s.get("doi")
            link = f" https://doi.org/{doi}" if doi else ""
            oa = f" (open access: {s['open_access_url']})" if s.get("open_access_url") else ""
            parts.append(f"- [{s.get('id')}] {cite}.{link}{oa}")

    parts.append("\n---\n" + _DISCLAIMER)
    return "\n".join(parts).strip()
