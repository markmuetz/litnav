import pytest

from test_search import ilib, FakeEmbedder  # noqa: F401

starlette = pytest.importorskip("starlette")
from starlette.testclient import TestClient  # noqa: E402

from litnav.web.app import create_app  # noqa: E402

PORT = 8765
TOKEN = "t0ken"
KEY = "launch-key"
ORIGIN = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def client(ilib):
    c = TestClient(create_app(ilib, PORT, token=TOKEN, key=KEY), base_url=ORIGIN)
    r = c.get("/", params={"key": KEY})                 # sets the session cookie, redirects
    assert r.status_code == 200 and TOKEN in r.text
    return c


def post(c, path, body, **h):
    headers = {"X-Litnav-Token": TOKEN, "Origin": ORIGIN} | h
    return c.post(path, json=body, headers=headers)


def test_guard(ilib):
    c = TestClient(create_app(ilib, PORT, token=TOKEN, key=KEY), base_url=ORIGIN)
    assert c.get("/api/items").status_code == 401                       # no cookie
    bad = TestClient(create_app(ilib, PORT, token=TOKEN, key=KEY), base_url="http://evil.example:8765")
    assert bad.get("/", params={"key": KEY}).status_code == 403         # DNS rebinding
    # another user on the machine can reach 127.0.0.1 but has no launch key
    r = c.get("/")
    assert r.status_code == 403 and TOKEN not in r.text
    assert c.get("/", params={"key": "guess"}).status_code == 403
    assert c.get("/api/items").status_code == 401
    c.get("/", params={"key": KEY})
    assert c.get("/").status_code == 200                                # the cookie is enough now
    assert c.post("/api/reading", json={}).status_code == 403            # no token header
    assert post(c, "/api/reading", {}, Origin="http://evil.example").status_code == 403


def test_reads(client):
    items = client.get("/api/items").json()
    assert {i["key"] for i in items} == {"smith2021cold", "jones2019gravity", "doe2026plans_draft"}
    d = client.get("/api/item/smith2021cold").json()
    assert d["title"] and d["sections"] and "bibtex" in d
    slug = d["sections"][0]["slug"]
    assert client.get(f"/api/item/smith2021cold/section/{slug}").json()["text"]
    r = client.get("/api/search", params={"q": "cold pools", "mode": "keyword"}).json()
    assert {x["key"] for x in r["results"]} >= {"smith2021cold"}
    assert client.get("/api/search", params={"q": ""}).json()["results"] == []
    assert client.get("/api/graph").json()["missing"]
    assert "tags" in client.get("/api/tags").json()
    assert client.get("/api/item/nosuch").status_code == 404
    assert client.get("/static/app.js").status_code == 200


def test_writes(client, ilib):
    r = post(client, "/api/item/smith2021cold/tags", {"add": ["cold_pools"]})
    assert "cold_pools" in r.json()["tags"]
    assert post(client, "/api/item/smith2021cold/tags", {"add": ["bad tag,x"]}).status_code == 400
    assert post(client, "/api/reading", {"action": "add", "key": "smith2021cold", "depth": "skim"}).json()["ok"]
    assert post(client, "/api/reading", {"action": "done", "key": "smith2021cold", "depth": "read"}).json()["ok"]
    d = client.get("/api/reading").json()
    assert d["list"][0]["status"] == "done" and d["cards"][0]["id"] == "smith2021cold"
    assert post(client, "/api/reading", {"action": "add", "key": "smith2021cold", "depth": "nope"}).status_code == 400


def test_discovery_endpoints(client, ilib):
    import json as J
    ilib.index_dir.mkdir(parents=True, exist_ok=True)
    (ilib.index_dir / "graph.json").write_text(J.dumps({"nodes": [], "links": [], "candidates": [
        {"id": "W2", "doi": "10.1/new", "title": "Gravity waves aloft", "year": 2000, "first_author": "A Lee",
         "cited_by_library": 6, "citers": ["smith2021cold"]}]}))
    cs = client.get("/api/candidates", params={"min_cites": 5}).json()
    assert [c["id"] for c in cs] == ["W2"]
    assert post(client, "/api/to-fetch", {"action": "add", "doi": "10.1/new", "title": "Gravity waves aloft"}).json()["ok"]
    assert client.get("/api/candidates").json()[0]["listed"] == "listed"
    assert post(client, "/api/to-fetch", {"action": "opened", "doi": "10.1/new"}).json()["ok"]
    assert client.get("/api/to-fetch").json()[0]["status"] == "opened"
    assert post(client, "/api/to-fetch", {"action": "add_skip", "doi": "10.1/other", "title": "x"}).json()["ok"]
    assert post(client, "/api/to-fetch", {"action": "bogus"}).status_code == 400
