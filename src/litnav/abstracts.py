"""Public abstracts: from bibliographic APIs, or from a page the user saved.

An abstract that a publisher shows to anyone is metadata, not the paper's
text: it may go where a summary may ([abstracts] to_models in library.toml,
default true). It is kept apart from the abstract litnav finds in the
paper's own text, which follows the paper's sharing level.

Sources, tried in order until one has the abstract:
  OpenAlex (batched by DOI), Semantic Scholar (DOI or arXiv id), Crossref
  (DOI), arXiv (arXiv id); then, only with an API key in the environment,
  the Springer Nature Meta API (SPRINGER_API_KEY; Springer and Nature DOIs)
  and Elsevier (ELSEVIER_API_KEY; 10.1016 DOIs).
Publisher web pages are never fetched: they refuse automated requests, and
their terms forbid it. For a paper no API covers, the user opens the page
in a browser without institutional access, saves it, and `from_html` takes
only the abstract from it (`litnav abstracts KEY --from-html FILE`).

Each result, found or not, is written to papers/<key>/abstract.json, so a
paper is only looked up again with --recheck. Confidential items are never
looked up.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import urllib.parse
from html.parser import HTMLParser

from . import keys
from . import sharing as S
from .net import LOOKUP, NetError
from .sources import arxiv

OPENALEX = "https://api.openalex.org/works"
S2 = "https://api.semanticscholar.org/graph/v1/paper/"
CROSSREF = "https://api.crossref.org/works/"
SPRINGER = "https://api.springernature.com/meta/v2/json"
ELSEVIER = "https://api.elsevier.com/content/abstract/doi/"
SPRINGER_PREFIXES = ("10.1007/", "10.1038/", "10.1186/", "10.1140/")
MIN_LEN, MAX_LEN = 100, 6000      # shorter is a snippet; longer is more than an abstract
FILE = "abstract.json"


def to_models(lib) -> bool:
    return bool(lib.config.get("abstracts", {}).get("to_models", True))


def read(it) -> dict | None:
    p = it.dir / FILE
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except json.JSONDecodeError:
        return None


def public(it) -> str:
    """The stored public abstract, or ''."""
    return (read(it) or {}).get("abstract", "")


def write(it, abstract: str, source: str, url: str = "", tried=()) -> dict:
    d = {"abstract": abstract, "source": source, "url": url,
         "fetched": dt.date.today().isoformat()}
    if tried:
        d["tried"] = list(tried)
    (it.dir / FILE).write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n", "utf-8")
    return d


def clean(text: str) -> str:
    """Plain text from JATS/HTML markup, without a leading 'Abstract' label."""
    t = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    t = " ".join(t.split())
    return re.sub(r"^(?:abstract|summary)\s*[:.]?\s+", "", t, flags=re.I)


def _ok(t: str) -> str:
    t = clean(t)
    return t if MIN_LEN <= len(t) <= MAX_LEN else ""


def from_inverted(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = sorted((p, w) for w, ps in inv.items() for p in ps)
    return " ".join(w for _, w in pos)


# -- API sources: each returns (abstract, url) or None

def openalex_batch(client, dois: list[str]) -> dict[str, str]:
    """DOI (lower case) -> abstract, 50 DOIs per request."""
    out = {}
    for i in range(0, len(dois), 50):
        chunk = dois[i:i + 50]
        try:
            d = client.get_json(OPENALEX, purpose=LOOKUP, params={
                "filter": "doi:" + "|".join(chunk), "per-page": 50,
                "select": "doi,abstract_inverted_index"})
        except NetError:
            continue
        for w in d.get("results", []):
            doi = (w.get("doi") or "").lower().removeprefix("https://doi.org/")
            ab = _ok(from_inverted(w.get("abstract_inverted_index")))
            if doi and ab:
                out[doi] = ab
    return out


def semantic_scholar(client, doi: str = "", arxiv_id: str = ""):
    pid = f"DOI:{doi}" if doi else f"ARXIV:{arxiv_id}"
    try:
        d = client.get_json(S2 + urllib.parse.quote(pid, safe=":/"), purpose=LOOKUP,
                            params={"fields": "abstract"})
    except NetError:
        return None
    ab = _ok(d.get("abstract") or "")
    return (ab, f"https://www.semanticscholar.org/paper/{d.get('paperId', '')}") if ab else None


def crossref(client, doi: str):
    try:
        d = client.get_json(CROSSREF + urllib.parse.quote(doi, safe="/"), purpose=LOOKUP)
    except NetError:
        return None
    ab = _ok((d.get("message") or {}).get("abstract") or "")
    return (ab, f"https://doi.org/{doi}") if ab else None


def arxiv_abs(client, aid: str):
    rec = arxiv.lookup(client, aid)
    ab = _ok((rec or {}).get("abstract", ""))
    return (ab, f"https://arxiv.org/abs/{aid}") if ab else None


def springer(client, doi: str, key: str):
    try:
        d = client.get_json(SPRINGER, purpose=LOOKUP, cache=False,   # the key is in the URL
                            params={"q": f"doi:{doi}", "api_key": key})
    except NetError:
        return None
    for r in d.get("records", []):
        a = r.get("abstract")
        if isinstance(a, dict):
            a = " ".join(a.get("p", [])) if isinstance(a.get("p"), list) else a.get("p", "")
        ab = _ok(a or "")
        if ab:
            return ab, f"https://doi.org/{doi}"
    return None


def elsevier(client, doi: str, key: str):
    try:
        d = client.get_json(ELSEVIER + urllib.parse.quote(doi, safe="/"), purpose=LOOKUP,
                            headers={"X-ELS-APIKey": key})
    except NetError:
        return None
    core = (d.get("abstracts-retrieval-response") or {}).get("coredata") or {}
    ab = _ok(core.get("dc:description") or "")
    return (ab, f"https://doi.org/{doi}") if ab else None


def lookup_one(client, doi: str = "", arxiv_id: str = "", env=None, oa: dict | None = None):
    """(abstract, source, url, tried) for one paper."""
    env = os.environ if env is None else env
    doi = (doi or "").lower()
    tried = []
    if doi:
        tried.append("openalex")
        got = (oa or {}).get(doi) if oa is not None else openalex_batch(client, [doi]).get(doi)
        if got:
            return got, "openalex", f"https://doi.org/{doi}", tried
    steps = []
    if doi or arxiv_id:
        steps.append(("semanticscholar", lambda: semantic_scholar(client, doi, arxiv_id)))
    if doi:
        steps.append(("crossref", lambda: crossref(client, doi)))
    if arxiv_id:
        steps.append(("arxiv", lambda: arxiv_abs(client, arxiv_id)))
    if doi.startswith(SPRINGER_PREFIXES) and env.get("SPRINGER_API_KEY"):
        steps.append(("springer", lambda: springer(client, doi, env["SPRINGER_API_KEY"])))
    if doi.startswith("10.1016/") and env.get("ELSEVIER_API_KEY"):
        steps.append(("elsevier", lambda: elsevier(client, doi, env["ELSEVIER_API_KEY"])))
    for name, fn in steps:
        tried.append(name)
        got = fn()
        if got:
            return got[0], name, got[1], tried
    return "", "none", "", tried


def _arxiv_id(it) -> str:
    return (it.meta.get("ids", {}).get("arxiv") or (it.entry.get("eprint") if it.entry else "")
            or "")


def run(lib, client, *, keys_=None, recheck=False, dry_run=False, env=None, log=print) -> dict:
    """Fill papers/<key>/abstract.json for library items that lack one."""
    items = []
    for it in lib.items(purpose=S.LOOKUP):          # never confidential items
        if keys_ and it.key not in keys_:
            continue
        if not recheck and read(it) is not None:
            continue
        items.append(it)
    c = {"found": 0, "none": 0, "no-id": 0}
    oa = openalex_batch(client, sorted({it.doi.lower() for it in items if it.doi}))
    for i, it in enumerate(items, 1):
        aid = _arxiv_id(it)
        if not it.doi and not aid:
            c["no-id"] += 1
            log(f"  [{i}/{len(items)}] {it.key:<40} no DOI or arXiv id")
            continue
        ab, src, url, tried = lookup_one(client, it.doi, aid, env=env, oa=oa)
        c["found" if ab else "none"] += 1
        if ab:
            c[src] = c.get(src, 0) + 1
        log(f"  [{i}/{len(items)}] {it.key:<40} {src}")
        if not dry_run:
            write(it, ab, src, url, tried)
    return c


# -- the manual fallback: a page the user saved from a browser

class _Page(HTMLParser):
    """Collects meta tags, JSON-LD, the page title, and the text of the first
    element whose id or class names an abstract. Nothing else is kept."""
    BLOCK_ID = re.compile(r"abstract|abs1|^abs$", re.I)

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.ld: list[str] = []
        self._in_ld = False
        self._ld_buf = []
        self.block: list[str] = []
        self._depth = 0           # >0 while inside the abstract element
        self._done = False
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name and a.get("content"):
                self.meta.setdefault(name, []).append(a["content"])
            return
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._in_ld, self._ld_buf = True, []
            return
        if tag == "title":
            self._in_title = True
        if self._depth:
            if tag not in ("br", "img", "hr", "input", "meta", "link"):
                self._depth += 1
            return
        if not self._done and tag in ("div", "section", "p", "blockquote", "article"):
            ident = " ".join(filter(None, [a.get("id"), a.get("class")]))
            if ident and any(self.BLOCK_ID.search(x) for x in ident.split()) \
                    and "abstract-" not in (a.get("id") or "").lower().replace("abstract-content", ""):
                self._depth = 1

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self.ld.append("".join(self._ld_buf))
            self._in_ld = False
        if tag == "title":
            self._in_title = False
        if self._depth:
            self._depth -= 1
            if self._depth == 0 and len(clean(" ".join(self.block))) >= MIN_LEN:
                self._done = True
            elif self._depth == 0:
                self.block = []

    def handle_data(self, data):
        if self._in_ld:
            self._ld_buf.append(data)
        elif self._in_title:
            self.title += data
        elif self._depth and not self._done:
            self.block.append(data)


def _ld_abstract(chunks: list[str]) -> str:
    for raw in chunks:
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for obj in (d if isinstance(d, list) else [d]):
            if isinstance(obj, dict):
                for k in ("abstract", "description"):
                    if isinstance(obj.get(k), str) and _ok(obj[k]):
                        return obj[k]
    return ""


def page_title(p: _Page) -> str:
    for k in ("citation_title", "dc.title", "og:title"):
        if p.meta.get(k):
            return p.meta[k][0]
    return p.title


def from_html(text: str, expect_title: str = "") -> tuple[str, str]:
    """(abstract, where it came from) from a saved page; ('', reason) if there
    is none. Only the abstract is taken: a page with the full text (the user
    had access) gives up its abstract and nothing more."""
    p = _Page()
    p.feed(text)
    if expect_title:
        got = page_title(p)
        want = {w for w in keys.words(expect_title) if w not in keys.STOP and len(w) > 3}
        have = set(keys.words(got))
        if want and len(want & have) / len(want) < 0.6:
            return "", f"the page's title ({got.strip()[:80]!r}) does not match the paper's"
    cands = [(clean(" ".join(p.block)), "abstract element")]
    for k in ("citation_abstract", "dc.description", "dcterms.abstract", "description",
              "og:description"):
        for v in p.meta.get(k, []):
            cands.append((clean(v), f"meta {k}"))
    cands.append((clean(_ld_abstract(p.ld)), "JSON-LD"))
    good = [(t, w) for t, w in cands if MIN_LEN <= len(t) <= MAX_LEN]
    if not good:
        return "", "no abstract found in the page"
    # the longest candidate: meta descriptions are often cut short
    t, where = max(good, key=lambda x: len(x[0]))
    return t, where
