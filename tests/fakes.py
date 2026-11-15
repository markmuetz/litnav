"""A fake net.Client: answers from a dict of URL substrings to payloads."""
import json

from litnav import sharing


def cr_msg(doi, title, family="Smith", given="Jane A.", year=2021, online=None, **kw):
    m = {"DOI": doi, "title": [title], "author": [{"family": family, "given": given}],
         "type": "journal-article", "container-title": ["Journal of the Atmospheric Sciences"],
         "volume": "78", "issue": "3", "page": "123-145",
         "published-print": {"date-parts": [[year, 3]]}}
    if online:
        m["published-online"] = {"date-parts": [[online, 12]]}
        m["issued"] = {"date-parts": [[online, 12]]}
    m.update(kw)
    return m


class FakeClient:
    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def _check(self, purpose, items):
        for it in items:
            if not it.allowed(purpose):
                from litnav.net import Refused
                raise Refused(it.key)

    def get(self, url, *, purpose, items=(), params=None, **kw):
        self._check(purpose, items)
        full = url + ("?" + json.dumps(params, sort_keys=True) if params else "")
        self.calls.append(full)
        for pat, payload in self.routes.items():
            if pat in full:
                return payload if isinstance(payload, str) else json.dumps(payload)
        from litnav.net import NetError
        raise NetError(f"404 {full}")

    def get_json(self, url, **kw):
        return json.loads(self.get(url, **kw))
