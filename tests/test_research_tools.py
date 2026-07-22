"""Offline tests for the scholarly-API clients (mocked HTTP; stdlib only).

Run with either:
    python -m pytest agent_skeleton/tests/test_research_tools.py -q
    python tests/test_research_tools.py
"""
from __future__ import annotations

# --- self-bootstrap so `agent_skeleton` imports without an editable install ---
import pathlib
import sys
import types

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if "agent_skeleton" not in sys.modules:
    try:
        import agent_skeleton  # noqa: F401
    except Exception:
        _pkg = types.ModuleType("agent_skeleton")
        _pkg.__path__ = [str(_ROOT)]
        sys.modules["agent_skeleton"] = _pkg
# ---------------------------------------------------------------------------

import json
import urllib.error
from unittest import mock

from agent_skeleton import research_tools as rt


class _FakeResp:
    def __init__(self, payload: dict, code: int = 200):
        self._data = json.dumps(payload).encode("utf-8")
        self._code = code

    def getcode(self):
        return self._code

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen_router(routes: dict):
    """Return a fake urlopen that maps a URL substring -> payload | Exception."""
    def _fake(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for key, val in routes.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return _FakeResp(val)
        raise urllib.error.URLError(f"no route for {url}")
    return _fake


def _combo(routes):
    # Patch urlopen + neutralize throttling sleeps for fast tests.
    return mock.patch.object(rt.urllib.request, "urlopen", _urlopen_router(routes)), \
        mock.patch.object(rt.time, "sleep", lambda *_: None)


def test_normalize_doi():
    assert rt.normalize_doi("https://doi.org/10.1/AbC") == "10.1/abc"
    assert rt.normalize_doi("doi:10.2/x") == "10.2/x"
    assert rt.normalize_doi("  10.3/Y  ") == "10.3/y"
    assert rt.normalize_doi("") is None
    assert rt.normalize_doi(None) is None


def test_title_similarity():
    assert rt._title_similarity("Statins reduce mortality", "Statins reduce mortality") == 1.0
    assert rt._title_similarity("apple orange banana", "kiwi mango grapefruit") == 0.0
    assert rt._title_similarity(None, "x") == 0.0


def test_search_literature_parses_europepmc():
    payload = {"resultList": {"result": [{
        "id": "42", "source": "MED", "pmid": "42", "doi": "10.1000/Real",
        "title": "A real biomedical paper.", "authorString": "Doe J, Roe R",
        "pubYear": "2020", "journalTitle": "J Test", "isOpenAccess": "Y",
        "abstractText": "We found something real.",
    }]}}
    p1, p2 = _combo({"europepmc": payload})
    rt._reset_caches()
    with p1, p2:
        out = rt.search_literature("statins mortality", max_results=5)
    assert out["ok"] is True
    assert out["count"] == 1
    rec = out["results"][0]
    assert rec["doi"] == "10.1000/real"           # normalized
    assert rec["is_open_access"] is True
    assert rec["abstract"] == "We found something real."
    assert rec["url"] == "https://doi.org/10.1000/real"


def test_verify_doi_resolves_and_matches():
    payload = {"message": {
        "title": ["Statins reduce cardiovascular mortality"],
        "author": [{"given": "J", "family": "Doe"}],
        "issued": {"date-parts": [[2019]]},
        "container-title": ["The Lancet"], "type": "journal-article",
    }}
    p1, p2 = _combo({"api.crossref.org": payload})
    rt._reset_caches()
    with p1, p2:
        good = rt.verify_doi("10.1000/real", claimed_title="Statins reduce cardiovascular mortality")
        rt._reset_caches()
        bad = rt.verify_doi("10.1000/real", claimed_title="An entirely unrelated title about frogs")
    assert good["resolves"] is True and good["verified"] is True
    assert good["year"] == 2019
    # Same DOI resolves, but a mismatched claimed title must NOT verify.
    assert bad["resolves"] is True and bad["verified"] is False


def test_verify_doi_404_does_not_resolve():
    err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    p1, p2 = _combo({"api.crossref.org": err})
    rt._reset_caches()
    with p1, p2:
        out = rt.verify_doi("10.9999/fabricated")
    assert out["ok"] is True and out["resolves"] is False and out["verified"] is False


def test_verify_doi_flags_retraction():
    # Crossref carries Retraction Watch data on the retracted record's `updated-by`.
    # A correction alongside the retraction must not mask the (blocking) retraction.
    payload = {"message": {
        "title": ["A since-retracted study"],
        "issued": {"date-parts": [[2010]]},
        "type": "journal-article",
        "updated-by": [
            {"type": "correction", "label": "Correction", "DOI": "10.1/corr",
             "updated": {"date-parts": [[2011]]}},
            {"type": "retraction", "label": "Retraction", "source": "retraction-watch",
             "DOI": "10.1/notice", "updated": {"date-parts": [[2012]]}},
        ],
    }}
    p1, p2 = _combo({"api.crossref.org": payload})
    rt._reset_caches()
    with p1, p2:
        out = rt.verify_doi("10.1/retracted")
    assert out["resolves"] is True
    assert out["integrity_status"] == "retracted"
    assert out["integrity_detail"] and "10.1/notice" in out["integrity_detail"]


def test_verify_doi_retraction_outranks_concern():
    # A record with BOTH an expression of concern and a retraction must be labeled
    # "retracted" (most severe), not "concern" — even if concern is listed first.
    payload = {"message": {
        "title": ["A paper later retracted"], "issued": {"date-parts": [[2020]]},
        "type": "journal-article",
        "updated-by": [
            {"type": "expression_of_concern", "label": "Expression of concern", "DOI": "10.1/eoc",
             "updated": {"date-parts": [[2020]]}},
            {"type": "retraction", "label": "Retraction", "DOI": "10.1/retr",
             "updated": {"date-parts": [[2020]]}},
        ],
    }}
    p1, p2 = _combo({"api.crossref.org": payload})
    rt._reset_caches()
    with p1, p2:
        out = rt.verify_doi("10.1/both")
    assert out["integrity_status"] == "retracted"
    assert "10.1/retr" in out["integrity_detail"]


def test_verify_doi_clean_record_is_ok():
    payload = {"message": {"title": ["A clean paper"], "issued": {"date-parts": [[2021]]},
                           "type": "journal-article"}}
    p1, p2 = _combo({"api.crossref.org": payload})
    rt._reset_caches()
    with p1, p2:
        out = rt.verify_doi("10.1/clean")
    assert out["integrity_status"] == "ok" and out["integrity_detail"] is None


def test_search_literature_omits_invalid_sort_param():
    # Regression: Europe PMC returns 0 hits if `sort=relevance` is passed. The
    # request must NOT include a sort param (relevance is the default).
    seen = {}

    def _router(req, timeout=None):
        seen["url"] = req.full_url
        return _FakeResp({"resultList": {"result": []}})

    rt._reset_caches()
    with mock.patch.object(rt.urllib.request, "urlopen", _router), \
            mock.patch.object(rt.time, "sleep", lambda *_: None):
        rt.search_literature("statins")
    assert "sort=" not in seen["url"], f"unexpected sort param in {seen['url']}"


def test_search_pubmed_parses():
    esearch = {"esearchresult": {"idlist": ["111"]}}
    esummary = {"result": {"uids": ["111"], "111": {
        "uid": "111", "title": "A clinical trial.", "authors": [{"name": "Doe J"}],
        "pubdate": "2022 Jan", "fulljournalname": "NEJM",
        "articleids": [{"idtype": "doi", "value": "10.1/aBc"}, {"idtype": "pubmed", "value": "111"}],
    }}}
    p1, p2 = _combo({"esearch.fcgi": esearch, "esummary.fcgi": esummary})
    rt._reset_caches()
    with p1, p2:
        out = rt.search_pubmed("cancer immunotherapy", max_results=5)
    assert out["ok"] is True and out["count"] == 1
    rec = out["results"][0]
    assert rec["doi"] == "10.1/abc"        # normalized
    assert rec["pmid"] == "111"
    assert rec["year"] == "2022"
    assert rec["journal"] == "NEJM"


def test_openalex_lookup_parses_and_reconstructs_abstract():
    work = {
        "display_name": "Real OA Paper.", "doi": "https://doi.org/10.1/OA",
        "publication_year": 2020,
        "authorships": [{"author": {"display_name": "Roe R"}}],
        "primary_location": {"source": {"display_name": "PLOS ONE"}},
        "cited_by_count": 42, "referenced_works": ["w1", "w2"],
        "open_access": {"is_oa": True, "oa_url": "http://oa"},
        "best_oa_location": {"pdf_url": "http://oa.pdf"},
        "abstract_inverted_index": {"Hello": [0], "world": [1]},
    }
    p1, p2 = _combo({"api.openalex.org": work})
    rt._reset_caches()
    with p1, p2:
        out = rt.openalex_lookup("10.1/OA")
    assert out["ok"] is True and out["found"] is True
    rec = out["record"]
    assert rec["doi"] == "10.1/oa"
    assert rec["citation_count"] == 42
    assert rec["reference_count"] == 2
    assert rec["abstract"] == "Hello world"       # reconstructed from inverted index
    assert rec["open_access_url"] == "http://oa.pdf"


def test_openalex_degrades_when_key_required():
    err = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    p1, p2 = _combo({"api.openalex.org": err})
    rt._reset_caches()
    with p1, p2:
        out = rt.openalex_lookup("10.1/x")
    assert out["ok"] is False and "OPENALEX_API_KEY" in out["error"]


def test_tools_never_raise_on_network_error():
    err = urllib.error.URLError("dns fail")
    p1, p2 = _combo({
        "europepmc": err, "api.crossref.org": err, "unpaywall": err,
        "esearch.fcgi": err, "api.openalex.org": err,
    })
    rt._reset_caches()
    with p1, p2:
        assert rt.search_literature("x")["ok"] is False
        rt._reset_caches()
        assert rt.search_pubmed("x")["ok"] is False
        rt._reset_caches()
        assert rt.verify_doi("10.1/x")["ok"] is False
        rt._reset_caches()
        assert rt.find_open_access("10.1/x")["ok"] is False
        rt._reset_caches()
        assert rt.openalex_lookup("10.1/x")["ok"] is False


if __name__ == "__main__":
    _fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    _fail = 0
    for fn in _fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            _fail += 1
            print(f"FAIL {fn.__name__}: {exc!r}")
    print(f"\n{len(_fns) - _fail}/{len(_fns)} passed")
    sys.exit(1 if _fail else 0)
