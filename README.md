# ClaimAnchor — biomedical evidence & citation verification agent

**ClaimAnchor checks whether the research claims in a grant or manuscript are
actually supported by the literature — and whether every cited DOI is real.** Give
it a claim, a paragraph with citations, a reference list, or a research question.
It retrieves the real biomedical literature (Europe PMC, PubMed, Crossref,
Unpaywall), judges each claim, verifies every DOI against Crossref, and returns a
per‑claim verdict with supporting quotes plus a reusable JSON evidence graph. **It
cites only sources returned by live database calls, and never fabricates a
reference.**

Built for **Washington University in St. Louis** researchers on the DTRC
AgenticNetwork / A2A starter template (Path B: a custom `AgentHandler` driving
**Claude Opus 4.8** through the official Anthropic SDK).

---

## Why this, and why now

General chatbots fabricate scientific citations at alarming rates — independent
studies report **40–90%** of generated references are wrong or invented, and the
CiteME benchmark finds frontier LLMs identify the correct paper to cite only
**4–18%** of the time (versus ~70% for humans). Even retrieval‑augmented "deep
research" tools still surface unverifiable DOIs. A researcher can't safely paste
any of that into an R01 or a manuscript.

ClaimAnchor closes that gap by construction: the model is allowed to cite **only**
what the retrieval tools returned this session, and a deterministic pass then
re‑checks every citation against a provenance ledger and a live Crossref lookup,
dropping anything it can't verify. "No fabricated citations" is a property of the
output, not a hope.

Two things set it apart from grounded search tools (Elicit, Consensus, Scite): it
verifies references **you or another agent already have** — the after‑the‑fact check
those answer‑generation tools don't perform — and it flags **retracted** papers via
Crossref/Retraction Watch, which frontier chatbots and "deep research" agents
essentially never catch. And it ships as a network‑callable A2A service, so other
agents can delegate verification to it.

---

## Example

**Request** (A2A message text):

> Verify this sentence before I submit: *"GLP‑1 receptor agonists reduce major
> adverse cardiovascular events in patients with type 2 diabetes."*

**Human‑readable answer** (the A2A text message):

```
GLP-1 receptor agonists reduce major adverse cardiovascular events (MACE) in
type 2 diabetes: SUPPORTED [S1][S2]. Cardiovascular outcome trials and their
meta-analyses show a consistent reduction in MACE...

### Verified sources
- [S1] Sattar N, et al. *Cardiovascular... GLP-1 receptor agonists: a
  meta-analysis*. 2021. Lancet Diabetes Endocrinol. https://doi.org/10.1016/...
- [S2] ...

---
ClaimAnchor cites only sources returned by live scholarly-database calls and
verified against Crossref; it never invents citations. Verify clinically critical
findings against the primary source before relying on them.
```

**Structured evidence graph** (the A2A `DataPart` artifact / `structured_output`),
reusable by another agent or tool:

```jsonc
{
  "answer": "…the human-readable report above…",
  "claims": [
    {
      "claim": "GLP-1 receptor agonists reduce MACE in type 2 diabetes",
      "verdict": "supported",                 // supported | partially_supported | unsupported | source_not_found
      "confidence": "high",                   // high | medium | low
      "source_ids": ["S1", "S2"],
      "supporting_quote": "…a 14% reduction in MACE…",
      "quote_grounded": true                  // quote located in the retrieved abstract
    }
  ],
  "sources": [
    {
      "id": "S1", "doi": "10.1016/s2213-8587(21)00203-5",
      "title": "…", "authors": "Sattar N; …", "year": "2021",
      "journal": "Lancet Diabetes Endocrinol",
      "open_access_url": null,
      "verification": "retrieved",            // retrieved | retrieved-by-id | crossref-resolved | retracted
      "integrity_status": "ok"                // ok | retracted | withdrawn | removed | concern
    }
  ],
  "unverified_sources": [],                    // fabricated / mis-cited / retracted citations (with reasons)
  "overall_confidence": "high",
  "limitations": ["Evidence limited to English-language indexed records", "…"],
  "verification_summary": {
    "sources_verified": 2, "sources_unverified": 0, "sources_retracted": 0, "claims_adjusted": 0,
    "method": "ledger + live Crossref resolution + Retraction Watch integrity check + supporting-quote grounding"
  },
  "disclaimer": "ClaimAnchor cites only sources returned by live scholarly-database calls…"
}
```

If a cited DOI can't be verified, that source is moved to `unverified_sources`
with a reason, and any claim it was the sole support for is downgraded to
`source_not_found` — never silently asserted.

---

## Measured performance (reproducible, no API key)

Trust claims need a number. `scripts/eval.py` runs the deterministic verification
layer over a fixed gold set — real papers, fabricated DOIs, a title‑hijacked DOI, and
real retracted papers (Wakefield; both Surgisphere papers) — with an *empty*
provenance ledger, so the live Crossref + Retraction Watch checks must decide every
case on their own. No language model and no API key are involved.

| Metric | Result |
|---|---|
| Exact‑match accuracy | **8 / 8 (100%)** |
| Bad‑citation recall — fabricated / hijacked / retracted correctly excluded | **100%** |
| Kept‑source precision — kept sources that were genuinely valid | **100%** |

Every fabricated DOI is dropped, every retracted paper is excluded from support (and
its claim downgraded to `source_not_found`), the title‑hijacked DOI is caught, and
genuine papers — including one carrying a non‑blocking *addendum* — are kept.

```bash
python scripts/eval.py            # live Crossref (network, no key)
python scripts/eval.py --offline  # deterministic, no network
```

---

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .                      # import name is agent_skeleton
cp .env.example .env                  # then set ANTHROPIC_API_KEY and UNPAYWALL_EMAIL

# 1) Validate the template's schema/function alignment (stdlib only, no key, no network)
python -m agent_skeleton.serve check

# 2) Run ClaimAnchor over A2A (--card advertises the three real skills)
python -m agent_skeleton.serve serve-handler \
  --file handler.py --class ClaimAnchorHandler --card agent.card.json --port 9110
```

The agent is then reachable over A2A; its card is served at
`/.well-known/agent-card.json`. Set `AGENT_A2A_URL` (or pass `--advertise-url`) to
the publicly reachable JSON‑RPC endpoint so the advertised card points at the
deployment, not `127.0.0.1`.

### Offline tests (no install, no key, no network)

```bash
python tests/test_research_tools.py     # mocked HTTP
python tests/test_verification.py       # anti-fabrication logic
python tests/test_handler.py            # full flow with a fake Claude client
# or, with pytest installed: python -m pytest agent_skeleton/tests -q
```

### Live smoke test (network + real key)

To verify the agent against the real scholarly APIs and the real Claude model:

```bash
export ANTHROPIC_API_KEY=…            # Part B only; Part A runs without a key
export UNPAYWALL_EMAIL=you@wustl.edu
python scripts/smoke_test.py          # checks real APIs, then a full claim-verification
```

---

## What the README must answer (per the hackathon rubric)

**1. What research workflow does it improve?**
The pre‑submission evidence check. Before sending a grant or manuscript, a
researcher pastes their claims, paragraph, or reference list and gets, per claim,
whether the literature supports it, the strongest supporting (and contradicting)
sources, and confirmation that every DOI is real and correctly cited — a task that
is otherwise slow, manual, and error‑prone.

**2. Who at WashU benefits?**
School of Medicine investigators, postdocs, and clinician‑researchers preparing
grants/manuscripts (WashU Medicine is #2 in NIH funding); research librarians
supporting evidence synthesis; and any researcher who needs to trust citations
before submitting. It uses only public APIs, so it serves schools beyond Medicine
too, with no institutional credentials required.

**3. What does it do that a general chatbot would not?**
It cannot fabricate citations. Every reference is drawn from a live database call and
re‑verified against Crossref in code; unverifiable citations are dropped and the
affected claim is downgraded. It also flags **retracted** papers (Crossref /
Retraction Watch) so a discredited study never counts as support, checks that each
supporting quote actually appears in the retrieved abstract, abstains
(`source_not_found`) rather than inventing support, and returns a machine‑reusable
evidence graph — as a network‑callable A2A service other agents can delegate to.

**4. What is it designed to handle well?**
- Verify a single claim or every claim in a paragraph (Skill: `verify-claims`).
- Validate a reference list / DOIs — flag fabricated, mismatched, or retracted
  references (Skill: `validate-citations`).
- Answer a biomedical question with verified citations and surface contradictions
  (Skill: `evidence-synthesis`).
- Ambiguous/missing input (asks for a claim), empty results (abstains), rate
  limits and network errors (degrade gracefully), and attached `.txt`/`.md`/`.bib`
  documents.

**5. What tools, files, APIs, databases, or other agents does it use?**
- **LLM:** Claude Opus 4.8 via the official Anthropic SDK.
- **Retrieval/verification (all free):** Europe PMC (search + abstracts + OA),
  PubMed E‑utilities (coverage), Crossref (DOI truth source), Unpaywall (legal OA
  links); OpenAlex as an optional metadata fallback.
- **A2A:** deploys as a Path‑B `AgentHandler` behind the template's
  `HandlerExecutor`; can also consume another agent's report/claim list as input.

**6. How does it handle uncertainty, privacy, credentials, and limitations?**
- **Uncertainty:** per‑claim confidence + explicit abstention; overall confidence
  is capped after any verification adjustment.
- **Privacy:** no PHI/EHR and no patient data — public bibliographic APIs only.
- **Credentials:** `ANTHROPIC_API_KEY` (and optional keys) are read from the
  deployment's credential configuration or environment; **never committed** (`.env`
  is gitignored; `.env.example` is the template). Secrets are never logged.
- **Limitations:** English‑language and indexing coverage; support judgments often
  rely on the abstract when full text is paywalled; absence of a source is reported
  as `source_not_found`, not disproof. ClaimAnchor assists verification — it does not
  replace expert review. These limitations are also returned in every response.

---

## Architecture

```
A2A request ─▶ HandlerExecutor ─▶ ClaimAnchorHandler.handle_structured()
   (frozen template plumbing)              │
                                           ▼
             Claude Opus 4.8 tool loop  (Anthropic SDK, adaptive thinking, effort=high)
             tools: search_literature · fetch_paper · verify_doi · find_open_access
                                           │  each tool = a real HTTP call; the model
                                           │  may cite ONLY what a tool returned
                                           ▼
             submit_report (terminal tool) ─▶ evidence graph
                                           │
                                           ▼
             verification.verify_report():  provenance ledger + live Crossref check
                                           │  + Retraction Watch integrity + quote grounding
                                           │  → drop/label anything unverifiable or retracted
                                           ▼
             {"answer": <report>, "claims": [...], "sources": [...], "unverified_sources": [...]}
```

Files to read when reviewing the agent: `handler.py` (the agent),
`research_tools.py` (scholarly clients, stdlib‑only), `verification.py` (the
anti‑fabrication layer), `prompts_biomed.py` (system prompt + tool schemas),
`agent.card.json` (identity + skills). Everything else is the frozen template.

### Deployment notes for the AgenticNetwork team

- Import name is `agent_skeleton`; install with `pip install -e .`.
- Provide `ANTHROPIC_API_KEY` via the credential config (declared type
  `anthropic_api_key`) or environment; set `UNPAYWALL_EMAIL` to a real address.
- Keep `a2a-sdk` pinned at `0.3.2` (the card shape targets protocol 0.3.x).
- Set `AGENT_A2A_URL` to the reachable JSON‑RPC URL; optionally `AGENT_MODEL` to
  override the model.
- The agent needs outbound HTTPS to `ebi.ac.uk`, `api.crossref.org`,
  `api.unpaywall.org` (and `eutils.ncbi.nlm.nih.gov` / `api.openalex.org` if used).

See [`FEEDBACK.md`](FEEDBACK.md) for the starter‑repo issues and fixes found while
building this.
