"""The only module in litnav that opens network connections.

Every request names a purpose (see sharing.py) and the items it concerns;
if any of those items does not allow the purpose, the request is refused
before anything leaves the machine. Requests that concern no item (a lookup
for a file not yet imported) pass `items=()` and must still say why.

Responses are cached on disk (metadata is stable), hosts are rate-limited,
and HTTP 429/5xx are retried with backoff.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__, sharing

_MIN_INTERVAL = {"export.arxiv.org": 3.0, "oaipmh.arxiv.org": 3.0, "api.crossref.org": 0.2,
                 "api.openalex.org": 0.12, "api.semanticscholar.org": 1.1}
_last: dict[str, float] = {}
_lock = threading.Lock()


class Refused(Exception):
    """A request that the sharing levels forbid."""


class NetError(Exception):
    pass


class Client:
    def __init__(self, cache_dir: Path | None = None, mailto: str = "", offline: bool = False):
        self.cache_dir = cache_dir
        self.mailto = mailto
        self.offline = offline

    @classmethod
    def for_library(cls, lib) -> "Client":
        return cls(lib.index_dir / "cache" / "http", lib.config["library"].get("mailto", ""))

    @property
    def user_agent(self) -> str:
        ua = f"litnav/{__version__} (+https://github.com/markmuetz/litnav)"
        return ua + (f" mailto:{self.mailto}" if self.mailto else "")

    def _check(self, purpose: str, items) -> None:
        for it in items:
            if not it.allowed(purpose):
                raise Refused(f"{it.key} is {it.sharing}: not sent out for {purpose}")

    def _cache_path(self, url: str, accept: str) -> Path | None:
        if not self.cache_dir:
            return None
        h = hashlib.sha1(f"{accept} {url}".encode()).hexdigest()
        return self.cache_dir / h[:2] / f"{h}.json"

    def get(self, url: str, *, purpose: str, items=(), accept: str = "application/json",
            params: dict | None = None, cache: bool = True, headers: dict | None = None) -> str:
        self._check(purpose, items)
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        if self.mailto and ("api.crossref.org" in url or "api.openalex.org" in url):
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode({"mailto": self.mailto})
        cp = self._cache_path(url, accept) if cache else None
        if cp and cp.is_file():
            return json.loads(cp.read_text("utf-8"))["body"]
        if self.offline:
            raise NetError(f"offline and not cached: {url}")
        body = self._fetch(url, accept, headers or {})
        if cp:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps({"url": url, "t": time.time(), "body": body}), "utf-8")
        return body

    def get_json(self, url: str, **kw):
        return json.loads(self.get(url, **kw))

    def _fetch(self, url: str, accept: str, headers: dict) -> str:
        host = urllib.parse.urlparse(url).netloc
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent,
                                                   "Accept": accept, **headers})
        delays = (2, 6, 20, None)
        for d in delays:
            with _lock:
                wait = _MIN_INTERVAL.get(host, 0.5) - (time.time() - _last.get(host, 0))
                if wait > 0:
                    time.sleep(wait)
                _last[host] = time.time()
            try:
                with urllib.request.urlopen(req, timeout=30) as fh:
                    return fh.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise NetError(f"404 {url}") from None
                if e.code in (429, 500, 502, 503, 504) and d is not None:
                    time.sleep(d)
                    continue
                raise NetError(f"HTTP {e.code} {url}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if d is not None:
                    time.sleep(d)
                    continue
                raise NetError(f"{e} {url}") from None
        raise NetError(f"gave up on {url}")


def open_in_browser(url: str) -> None:
    import webbrowser
    webbrowser.open(url)


# Purposes re-exported for callers' convenience.
LOOKUP = sharing.LOOKUP
SEND_OUT = sharing.SEND_OUT
