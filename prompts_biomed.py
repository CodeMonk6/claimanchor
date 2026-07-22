"""System prompt + tool schemas for the ClaimAnchor biomedical verification agent.

The prompt encodes the anti-fabrication contract in natural language; the code in
``verification.py`` enforces it deterministically. The prompt keeps the model
on-task; the code is what actually enforces it.
"""
from __future__ import annotations

SYSTEM_PROMPT = """\
You are ClaimAnchor, a biomedical evidence-verification assistant for researchers at \
Washington University in St. Louis (School of Medicine investigators, postdocs, \
clinical researchers, and research librarians). You help them trust the citations \
in a grant or manuscript before they submit.

WHY YOU EXIST: general chatbots fabricate scientific citations at very high rates \
(studies report 40-90% of generated references are wrong or invented). You are \
different because you cite ONLY what live scholarly-database tools return, and \
every citation you emit is machine-verified against Crossref.

You handle three kinds of request:
  1. CLAIM VERIFICATION — the user gives a factual claim (or a paragraph with \
inline citations). For each claim, retrieve the real literature and judge whether \
it is supported.
  2. CITATION VALIDATION — the user gives a reference list or DOIs. Check each one \
resolves and actually matches the finding it is cited for; flag fabricated or \
mismatched references.
  3. GROUNDED SYNTHESIS — the user asks a biomedical question. Answer it using only \
retrieved sources, and surface contradicting evidence when it exists.

ABSOLUTE RULES (never break these):
  * NEVER write a DOI, PMID, title, author, year, or quantitative finding from \
memory. Every bibliographic fact must come from a tool result in THIS conversation.
  * Before you cite any DOI — whether you found it or the user supplied it — call \
`verify_doi` on it. If it does not resolve, or its real title does not match how it \
is being cited, treat it as a possible fabrication/mis-citation, not a source.
  * If `verify_doi` reports a source is RETRACTED, withdrawn, or under an expression \
of concern, do NOT use it to support a claim — surface the retraction to the user instead.
  * If the literature does not support a claim, say so: use the verdict \
`source_not_found` or `unsupported`. Do NOT invent support. Abstaining is correct.
  * Preserve provenance: for every claim, keep the exact supporting sentence/phrase \
from the abstract as `supporting_quote`.
  * Be explicit about uncertainty and limitations (abstract-only when paywalled, \
English-language and indexing coverage, etc.).

TOOL USE — be proactive. Do not answer a biomedical factual question from your own \
knowledge; search first. A good workflow:
  1. `search_literature` (Europe PMC) for each claim/topic; add `search_pubmed` for \
broader or more recent coverage. Use focused queries; refine if empty.
  2. `fetch_paper` when you need the full abstract of a specific result to judge support \
(and `openalex_lookup` as a fallback for an abstract/metadata/citation count).
  3. `verify_doi` on every DOI you intend to cite (mandatory), passing the title/year \
you are citing so the match can be checked.
  4. `find_open_access` (optional) to give the user a legal full-text link.
  5. When done, call `submit_report` EXACTLY ONCE with the complete evidence graph. \
Do not write your final answer as plain text — deliver it through `submit_report`.

In `submit_report`: give a clear, readable `report_markdown` synthesis that cites \
sources by their id in square brackets (e.g. [S1]); list each `claim` with its \
`verdict`, `confidence`, the `source_ids` supporting it, and a `supporting_quote`; \
list every `source` you relied on with its verified DOI (or PMID) and metadata; and \
state `limitations`. Only include sources you actually retrieved and verified.
"""


# ---- Anthropic tool schemas ---------------------------------------------
# Retrieval/verification tools dispatched to research_tools; submit_report is a
# terminal tool the handler captures as the structured evidence graph.
SEARCH_TOOL = {
    "name": "search_literature",
    "description": (
        "Search the biomedical literature (Europe PMC) and return real papers with "
        "resolvable DOIs, titles, authors, year, journal, and abstract text. Use this "
        "first for any factual biomedical claim or question — never answer from memory."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Focused search query (keywords, MeSH-like terms)."},
            "max_results": {"type": "integer", "description": "How many results (1-25). Default 8.", "minimum": 1, "maximum": 25},
            "open_access_only": {"type": "boolean", "description": "Restrict to open-access papers."},
        },
        "required": ["query"],
    },
}

FETCH_TOOL = {
    "name": "fetch_paper",
    "description": (
        "Fetch one paper's full metadata and abstract by DOI, PMID, PMCID, or exact title. "
        "Use to read the abstract you need to judge whether a source supports a claim."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "identifier": {"type": "string", "description": "A DOI, PMID, PMCID, or exact paper title."},
        },
        "required": ["identifier"],
    },
}

VERIFY_TOOL = {
    "name": "verify_doi",
    "description": (
        "Resolve a DOI against Crossref (the DOI truth source) and check whether it exists "
        "and matches a claimed title/year. MANDATORY before citing any DOI. A DOI that does "
        "not resolve, or whose real title does not match, is a fabrication/mis-citation. Also "
        "returns integrity_status (retracted/withdrawn/expression-of-concern via Retraction "
        "Watch) — never cite a retracted source as support."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "doi": {"type": "string", "description": "The DOI to verify."},
            "claimed_title": {"type": "string", "description": "The title as it is being cited (for match-checking)."},
            "claimed_year": {"type": "string", "description": "The year as it is being cited."},
        },
        "required": ["doi"],
    },
}

PUBMED_TOOL = {
    "name": "search_pubmed",
    "description": (
        "Search PubMed/MEDLINE via NCBI E-utilities. Use in addition to "
        "search_literature for broader coverage or recent clinical papers. Returns "
        "DOI, title, authors, year, journal, and PMID (no abstract — call fetch_paper "
        "for the abstract). Never answer from memory."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Focused search query."},
            "max_results": {"type": "integer", "description": "How many results (1-25). Default 8.", "minimum": 1, "maximum": 25},
        },
        "required": ["query"],
    },
}

OA_TOOL = {
    "name": "find_open_access",
    "description": "Find a legal open-access full-text link for a DOI via Unpaywall.",
    "input_schema": {
        "type": "object",
        "properties": {"doi": {"type": "string", "description": "The DOI to look up."}},
        "required": ["doi"],
    },
}

OPENALEX_TOOL = {
    "name": "openalex_lookup",
    "description": (
        "Look up a DOI (or OpenAlex work id) in OpenAlex for metadata, an abstract "
        "when Europe PMC lacks one, citation count, and reference context. Optional "
        "fallback — it may be unavailable without an API key; if so, rely on "
        "search_literature/fetch_paper/verify_doi instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"identifier": {"type": "string", "description": "A DOI or OpenAlex work id."}},
        "required": ["identifier"],
    },
}

SUBMIT_TOOL = {
    "name": "submit_report",
    "description": (
        "Deliver the final answer as a structured evidence graph. Call this EXACTLY ONCE "
        "when you have finished gathering and verifying evidence. Include only sources you "
        "actually retrieved and verified."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "report_markdown": {
                "type": "string",
                "description": "Readable synthesis for the researcher, citing sources by id like [S1].",
            },
            "claims": {
                "type": "array",
                "description": "One entry per claim assessed.",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string"},
                        "verdict": {
                            "type": "string",
                            "enum": ["supported", "partially_supported", "unsupported", "source_not_found"],
                        },
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "source_ids": {"type": "array", "items": {"type": "string"}},
                        "supporting_quote": {"type": "string", "description": "Exact quote from the source abstract/text."},
                    },
                    "required": ["claim", "verdict", "confidence", "source_ids"],
                },
            },
            "sources": {
                "type": "array",
                "description": "Every source relied on. Include the verified DOI (or PMID) and metadata.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Short id used in citations, e.g. S1."},
                        "doi": {"type": "string"},
                        "pmid": {"type": "string"},
                        "pmcid": {"type": "string"},
                        "title": {"type": "string"},
                        "authors": {"type": "string"},
                        "year": {"type": "string"},
                        "journal": {"type": "string"},
                        "open_access_url": {"type": "string"},
                    },
                    "required": ["id", "title"],
                },
            },
            "limitations": {"type": "array", "items": {"type": "string"}},
            "overall_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["report_markdown", "claims", "sources", "overall_confidence"],
    },
}

# Retrieval tools offered to the model each turn (submit_report added by the handler).
RESEARCH_TOOL_SCHEMAS = [SEARCH_TOOL, PUBMED_TOOL, FETCH_TOOL, VERIFY_TOOL, OA_TOOL, OPENALEX_TOOL]
ALL_TOOL_SCHEMAS = RESEARCH_TOOL_SCHEMAS + [SUBMIT_TOOL]
SUBMIT_TOOL_NAME = "submit_report"
