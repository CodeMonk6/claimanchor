"""Scholarly-data clients for ClaimAnchor (stdlib only — no third-party HTTP dep).

Every function is a thin, defensive wrapper around a *free, public* scholarly API
and returns a JSON-able dict. These functions are the **only** source of factual
bibliographic data the agent is allowed to cite — the LLM may never invent a DOI,
title, or finding from memory (see ``prompts_biomed.py``). Each function:

* returns real retrieved records only,
* never raises (network/parse errors come back as ``{"ok": False, "error": ...}``),
* is synchronous and blocking — the handler wraps calls in ``asyncio.to_thread``
  so the A2A heartbeat is never frozen.

Data sources (all keyless unless noted):
  * Europe PMC  — biomedical search + abstracts + open-access flags (primary)
  * Crossref    — DOI resolution / verification (the DOI truth source)
  * Unpaywall   — legal open-access full-text links (needs a contact email param)
  * OpenAlex    — metadata fallback (a free key raises limits as of Feb 2026;
                  we degrade gracefully to Crossref if it is unavailable)
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# --- Configuration --------------------------------------------------------
DEFAULT_TIMEOUT = 20.0            # seconds per HTTP call
MAX_RESULTS_CAP = 25             # never fetch more than this many records at once
_ABSTRACT_MAX = 4000            # trim very long abstracts before returning

# Polite-pool contact. Public APIs ask for a contact email; we read it from the
# environment so nothing is hard-coded. Falls back to a neutral placeholder.
def _contact_email() -> str:
    return (
        os.getenv("UNPAYWALL_EMAIL")
        or os.getenv("CROSSREF_MAILTO")
        or "claimanchor@example.org"
    )


def _user_agent() -> str:
    return f"ClaimAnchor/0.1 (biomedical citation verification; mailto:{_contact_email()})"


# --- Minimal, polite HTTP core -------------------------------------------
_MIN_INTERVAL = {           # min seconds between calls per host (rate-limit safety)
    "www.ebi.ac.uk": 0.25,
    "api.crossref.org": 0.4,
    "api.unpaywall.org": 0.2,
    "api.openalex.org": 0.15,
    "eutils.ncbi.nlm.nih.gov": 0.34,
}
_last_call: dict[str, float] = {}
_cache: dict[str, Any] = {}     # per-process URL cache (dedupes calls within a session)


def _throttle(host: str) -> None:
    interval = _MIN_INTERVAL.get(host, 0.0)
    if not interval:
        return
    last = _last_call.get(host)
    now = time.monotonic()
    if last is not None:
        wait = interval - (now - last)
        if wait > 0:
            time.sleep(wait)
    _last_call[host] = time.monotonic()


def _get_json(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """GET a URL and parse JSON. Returns {"ok": True, "status": .., "data": ..}
    or {"ok": False, "status": .., "error": ..}. Never raises."""
    if url in _cache:
        return _cache[url]
    host = urllib.parse.urlparse(url).hostname or ""
    _throttle(host)
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent(), "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted scholarly hosts)
            status = resp.getcode()
            raw = resp.read()
        data = json.loads(raw.decode("utf-8", errors="replace"))
        out = {"ok": True, "status": status, "data": data}
    except urllib.error.HTTPError as exc:
        out = {"ok": False, "status": exc.code, "error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError) as exc:
        out = {"ok": False, "status": None, "error": f"network error: {exc}"}
    except (ValueError, json.JSONDecodeError) as exc:
        out = {"ok": False, "status": None, "error": f"could not parse response: {exc}"}
    except Exception as exc:  # last-resort guard — a tool must never crash the loop
        out = {"ok": False, "status": None, "error": f"unexpected error: {exc}"}
    _cache[url] = out
    return out


# --- Normalization --------------------------------------------------------
def normalize_doi(doi: str | None) -> str | None:
    """Lower-case, strip URL/`doi:` prefixes and whitespace. Returns None if empty."""
    if not doi:
        return None
    d = str(doi).strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    d = d.strip()
    return d or None


def _clip(text: str | None, limit: int = _ABSTRACT_MAX) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + " …[truncated]"


def _epmc_record(r: dict) -> dict:
    """Normalize one Europe PMC result into a stable record shape."""
    return {
        "title": r.get("title", "").rstrip("."),
        "authors": r.get("authorString", ""),
        "year": r.get("pubYear"),
        "journal": (r.get("journalInfo", {}) or {}).get("journal", {}).get("title")
        or r.get("journalTitle"),
        "doi": normalize_doi(r.get("doi")),
        "pmid": r.get("pmid"),
        "pmcid": r.get("pmcid"),
        "source": r.get("source"),
        "epmc_id": r.get("id"),
        "is_open_access": str(r.get("isOpenAccess", "N")).upper() == "Y",
        "citation_count": r.get("citedByCount"),
        "abstract": _clip(r.get("abstractText")),
        "url": (
            f"https://doi.org/{normalize_doi(r.get('doi'))}" if r.get("doi")
            else (f"https://europepmc.org/article/{r.get('source')}/{r.get('id')}"
                  if r.get("source") and r.get("id") else None)
        ),
    }


# --- Public tools ---------------------------------------------------------
def search_literature(query: str, max_results: int = 8, open_access_only: bool = False) -> dict:
    """Search the biomedical literature (Europe PMC) and return real records.

    Returns {"ok": True, "query": .., "count": N, "results": [record, ...]}.
    Each record carries a resolvable DOI (when one exists), title, authors, year,
    journal, and the abstract text to reason over. Never fabricates results.
    """
    if not query or not str(query).strip():
        return {"ok": False, "error": "empty query", "results": []}
    n = max(1, min(int(max_results or 8), MAX_RESULTS_CAP))
    q = str(query).strip()
    if open_access_only:
        q = f"({q}) AND OPEN_ACCESS:Y"
    # NB: do NOT pass sort=relevance — Europe PMC rejects it and returns 0 hits.
    # Relevance is the default ranking when no `sort` param is supplied.
    params = urllib.parse.urlencode(
        {"query": q, "format": "json", "pageSize": n, "resultType": "core"}
    )
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{params}"
    resp = _get_json(url)
    if not resp["ok"]:
        return {"ok": False, "error": resp["error"], "query": q, "results": []}
    results = (resp["data"].get("resultList", {}) or {}).get("result", []) or []
    records = [_epmc_record(r) for r in results[:n]]
    return {"ok": True, "query": q, "count": len(records), "results": records}


def fetch_paper(identifier: str) -> dict:
    """Fetch one paper's full metadata + abstract by DOI, PMID, PMCID, or title.

    Returns {"ok": True, "record": {...}} or {"ok": True, "found": False, ...}
    when nothing matches. Use this to pull the abstract you need to judge whether
    a source actually supports a claim.
    """
    if not identifier or not str(identifier).strip():
        return {"ok": False, "error": "empty identifier"}
    ident = str(identifier).strip()
    doi = normalize_doi(ident)
    if doi and doi.startswith("10."):
        q = f"DOI:{doi}"
    elif ident.isdigit():
        q = f"EXT_ID:{ident} AND SRC:MED"     # PMID
    elif ident.upper().startswith("PMC"):
        q = f"PMCID:{ident.upper()}"
    else:
        q = ident                              # free-text / title
    params = urllib.parse.urlencode(
        {"query": q, "format": "json", "pageSize": 1, "resultType": "core"}
    )
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{params}"
    resp = _get_json(url)
    if not resp["ok"]:
        return {"ok": False, "error": resp["error"], "identifier": ident}
    results = (resp["data"].get("resultList", {}) or {}).get("result", []) or []
    if not results:
        return {"ok": True, "found": False, "identifier": ident}
    return {"ok": True, "found": True, "record": _epmc_record(results[0])}


def _title_similarity(a: str | None, b: str | None) -> float:
    """Token-set Jaccard similarity of two titles in [0, 1]. Cheap, dependency-free."""
    if not a or not b:
        return 0.0
    ta = {t for t in "".join(c if c.isalnum() else " " for c in a.lower()).split() if len(t) > 2}
    tb = {t for t in "".join(c if c.isalnum() else " " for c in b.lower()).split() if len(t) > 2}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# Update relations that invalidate a source as support for a claim. Crossref carries
# Retraction Watch data on the *retracted* record's ``updated-by`` list.
_BLOCKING_UPDATE_TYPES = {
    "retraction", "partial_retraction", "withdrawal", "removal",
    "expression_of_concern",
}


def _detect_integrity(msg: dict) -> tuple[str, str | None]:
    """Scan a Crossref record's ``updated-by`` relations for integrity problems.

    Crossref (integrated with Retraction Watch) surfaces retraction/withdrawal/
    expression-of-concern notices on the affected record as an ``updated-by`` list;
    each entry has a ``type`` ("retraction", "correction", "expression_of_concern",
    …), a ``label``, and a date. Returns ``(status, detail)`` where status is one of
    ``{"ok", "retracted", "withdrawn", "removed", "concern"}``; ``detail`` is
    human-readable when not ok. Corrections/errata are informational and do NOT block
    a source from supporting a claim.
    """
    blocking: list[tuple[str, str, str | None]] = []
    for u in msg.get("updated-by") or []:
        if not isinstance(u, dict):
            continue
        t = (u.get("type") or "").strip().lower().replace("-", "_")
        if not (t in _BLOCKING_UPDATE_TYPES
                or "retract" in t or "withdraw" in t or "concern" in t or "removal" in t):
            continue
        label = u.get("label") or u.get("type") or "integrity notice"
        parts = (u.get("updated") or {}).get("date-parts", [[None]])
        yr = parts[0][0] if parts and parts[0] else None
        blocking.append((t, f"{label}{f' ({yr})' if yr else ''}", u.get("DOI")))
    if not blocking:
        return "ok", None

    def _status_of(t: str) -> str:
        if "retract" in t:
            return "retracted"
        if "withdraw" in t:
            return "withdrawn"
        if "removal" in t:
            return "removed"
        if "concern" in t:
            return "concern"
        return "retracted"

    # A record can carry several notices (e.g. an expression of concern AND a
    # retraction). Report the most severe so a retracted paper is never merely
    # labeled "concern".
    _severity = {"retracted": 0, "withdrawn": 1, "removed": 2, "concern": 3}
    status = min((_status_of(t) for t, _, _ in blocking), key=lambda s: _severity.get(s, 9))
    detail = "; ".join(f"{lab} — notice {doi}" if doi else lab for _, lab, doi in blocking)
    return status, detail


def verify_doi(doi: str, claimed_title: str | None = None, claimed_year: str | int | None = None) -> dict:
    """Resolve a DOI against Crossref (the DOI truth source) and check it matches.

    This is the anti-fabrication gate: it confirms the DOI *exists* and, when a
    claimed title/year is supplied, whether the real record *matches* it. It also
    reports an integrity signal from Crossref's Retraction Watch data. Returns:
      {"ok": True, "doi": .., "resolves": bool, "title": .., "year": ..,
       "title_match": float(0-1), "year_match": bool|None, "verified": bool,
       "integrity_status": "ok"|"retracted"|"withdrawn"|"removed"|"concern",
       "integrity_detail": str|None}
    ``verified`` is True only when the DOI resolves and (if a title was claimed)
    the title similarity clears a threshold — a mismatched DOI is a red flag.
    ``integrity_status`` flags a resolvable-but-retracted paper so it is never used
    as support.
    """
    d = normalize_doi(doi)
    if not d or not d.startswith("10."):
        return {"ok": True, "doi": doi, "resolves": False, "verified": False,
                "reason": "not a well-formed DOI"}
    url = f"https://api.crossref.org/works/{urllib.parse.quote(d)}?mailto={urllib.parse.quote(_contact_email())}"
    resp = _get_json(url)
    if resp.get("status") == 404:
        return {"ok": True, "doi": d, "resolves": False, "verified": False,
                "reason": "DOI does not resolve on Crossref"}
    if not resp["ok"]:
        return {"ok": False, "doi": d, "resolves": None, "verified": False, "error": resp["error"]}
    msg = resp["data"].get("message", {}) or {}
    title = (msg.get("title") or [None])[0]
    parts = (
        (msg.get("published-print") or msg.get("published-online") or msg.get("issued") or {})
        .get("date-parts", [[None]])
    )
    year = parts[0][0] if parts and parts[0] else None
    title_sim = _title_similarity(claimed_title, title) if claimed_title else None
    year_match = None
    if claimed_year and year:
        try:
            year_match = int(claimed_year) == int(year)
        except (TypeError, ValueError):
            year_match = None
    # Verified = resolves, and if a title was claimed it must be a plausible match.
    verified = True
    if claimed_title is not None:
        verified = (title_sim or 0.0) >= 0.35
    integrity_status, integrity_detail = _detect_integrity(msg)
    return {
        "ok": True,
        "doi": d,
        "resolves": True,
        "verified": verified,
        "title": title,
        "authors": "; ".join(
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in (msg.get("author") or [])[:8]
        ) or None,
        "year": year,
        "journal": (msg.get("container-title") or [None])[0],
        "type": msg.get("type"),
        "title_match": title_sim,
        "year_match": year_match,
        "integrity_status": integrity_status,   # "ok" | "retracted" | "withdrawn" | "removed" | "concern"
        "integrity_detail": integrity_detail,
    }


def find_open_access(doi: str) -> dict:
    """Find a legal open-access full-text link for a DOI via Unpaywall.

    Returns {"ok": True, "doi": .., "is_oa": bool, "oa_url": .. | None, ...}.
    """
    d = normalize_doi(doi)
    if not d or not d.startswith("10."):
        return {"ok": True, "doi": doi, "is_oa": False, "oa_url": None,
                "reason": "not a well-formed DOI"}
    url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(d)}?email={urllib.parse.quote(_contact_email())}"
    resp = _get_json(url)
    if resp.get("status") == 404:
        return {"ok": True, "doi": d, "is_oa": False, "oa_url": None, "reason": "not indexed by Unpaywall"}
    if not resp["ok"]:
        return {"ok": False, "doi": d, "is_oa": None, "oa_url": None, "error": resp["error"]}
    data = resp["data"]
    best = data.get("best_oa_location") or {}
    return {
        "ok": True,
        "doi": d,
        "is_oa": bool(data.get("is_oa")),
        "oa_url": best.get("url_for_pdf") or best.get("url"),
        "oa_status": data.get("oa_status"),
        "title": data.get("title"),
        "year": data.get("year"),
    }


# --- PubMed (NCBI E-utilities) -------------------------------------------
def _pubmed_record(d: dict) -> dict:
    doi = None
    for aid in d.get("articleids", []) or []:
        if aid.get("idtype") == "doi":
            doi = normalize_doi(aid.get("value"))
            break
    pubdate = d.get("pubdate") or d.get("epubdate") or ""
    year = pubdate.split(" ")[0][:4] if pubdate else None
    pmid = str(d.get("uid") or "")
    return {
        "title": (d.get("title") or "").rstrip("."),
        "authors": ", ".join(a.get("name", "") for a in (d.get("authors") or [])[:8]),
        "year": year,
        "journal": d.get("fulljournalname") or d.get("source"),
        "doi": doi,
        "pmid": pmid or None,
        "pmcid": None,
        "source": "MED",
        "is_open_access": None,
        "abstract": "",   # PubMed esummary carries no abstract; use fetch_paper for it
        "url": (f"https://doi.org/{doi}" if doi
                else (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None)),
    }


def search_pubmed(query: str, max_results: int = 8) -> dict:
    """Search PubMed/MEDLINE via NCBI E-utilities (esearch + esummary).

    Complements Europe PMC for coverage/recency. Records carry DOI, title,
    authors, year, journal, and PMID (abstracts come from `fetch_paper`). Returns
    {"ok": True, "count": N, "results": [record, ...]}. Never fabricates results.
    """
    if not query or not str(query).strip():
        return {"ok": False, "error": "empty query", "results": []}
    n = max(1, min(int(max_results or 8), MAX_RESULTS_CAP))
    key = os.getenv("NCBI_API_KEY")
    key_param = f"&api_key={urllib.parse.quote(key)}" if key else ""
    q = urllib.parse.quote(str(query).strip())
    es = _get_json(
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        f"?db=pubmed&retmode=json&sort=relevance&retmax={n}&term={q}{key_param}"
    )
    if not es["ok"]:
        return {"ok": False, "error": es["error"], "query": query, "results": []}
    idlist = (es["data"].get("esearchresult", {}) or {}).get("idlist", []) or []
    if not idlist:
        return {"ok": True, "query": query, "count": 0, "results": []}
    ids = ",".join(idlist[:n])
    su = _get_json(
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        f"?db=pubmed&retmode=json&id={ids}{key_param}"
    )
    if not su["ok"]:
        return {"ok": False, "error": su["error"], "query": query, "results": []}
    result = su["data"].get("result", {}) or {}
    records = [_pubmed_record(result[uid]) for uid in result.get("uids", []) if uid in result]
    return {"ok": True, "query": query, "count": len(records), "results": records}


# --- OpenAlex (metadata + citation context fallback) ---------------------
def _reconstruct_abstract(inverted_index: dict | None) -> str:
    """OpenAlex returns abstracts as an inverted index {word: [positions]}."""
    if not inverted_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted_index.items():
        for i in idxs or []:
            positions.append((i, word))
    positions.sort()
    return _clip(" ".join(w for _, w in positions))


def _openalex_record(w: dict) -> dict:
    oa = w.get("open_access") or {}
    best = w.get("best_oa_location") or {}
    return {
        "title": (w.get("display_name") or w.get("title") or "").rstrip("."),
        "authors": "; ".join(
            (a.get("author") or {}).get("display_name", "")
            for a in (w.get("authorships") or [])[:8]
        ),
        "year": w.get("publication_year"),
        "journal": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
        "doi": normalize_doi(w.get("doi")),
        "openalex_id": w.get("id"),
        "source": "OpenAlex",
        "is_open_access": oa.get("is_oa"),
        "open_access_url": best.get("pdf_url") or oa.get("oa_url"),
        "citation_count": w.get("cited_by_count"),
        "reference_count": len(w.get("referenced_works") or []),
        "abstract": _reconstruct_abstract(w.get("abstract_inverted_index")),
    }


def openalex_lookup(identifier: str) -> dict:
    """Fetch OpenAlex metadata + citation context for a DOI or OpenAlex work id.

    Useful for citation counts, an abstract when Europe PMC lacks one, and
    reference context. OpenAlex became metered in Feb 2026; if it requires a key
    or is rate-limited, this degrades gracefully (``ok: False`` with a reason) so
    the agent falls back to Crossref/Europe PMC. Reads OPENALEX_API_KEY if set.
    """
    if not identifier or not str(identifier).strip():
        return {"ok": False, "error": "empty identifier"}
    ident = str(identifier).strip()
    doi = normalize_doi(ident)
    path = f"doi:{doi}" if doi and doi.startswith("10.") else urllib.parse.quote(ident, safe=":/")
    params = {"mailto": _contact_email()}
    key = os.getenv("OPENALEX_API_KEY")
    if key:
        params["api_key"] = key
    url = f"https://api.openalex.org/works/{path}?{urllib.parse.urlencode(params)}"
    resp = _get_json(url)
    if resp.get("status") in (401, 403):
        return {"ok": False, "identifier": ident,
                "error": "OpenAlex requires an API key (metered since Feb 2026); set OPENALEX_API_KEY"}
    if resp.get("status") == 429:
        return {"ok": False, "identifier": ident, "error": "OpenAlex rate limit reached"}
    if resp.get("status") == 404:
        return {"ok": True, "found": False, "identifier": ident}
    if not resp["ok"]:
        return {"ok": False, "identifier": ident, "error": resp["error"]}
    return {"ok": True, "found": True, "record": _openalex_record(resp["data"])}


# Registry so the handler can build tool schemas + dispatch by name uniformly.
TOOL_FUNCTIONS = {
    "search_literature": search_literature,
    "search_pubmed": search_pubmed,
    "fetch_paper": fetch_paper,
    "verify_doi": verify_doi,
    "find_open_access": find_open_access,
    "openalex_lookup": openalex_lookup,
}


def _reset_caches() -> None:
    """Test/utility helper — clear the per-process URL cache + throttle state."""
    _cache.clear()
    _last_call.clear()
