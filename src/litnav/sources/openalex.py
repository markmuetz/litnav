"""OpenAlex: works by DOI (ids, licence, references)."""
from __future__ import annotations

import urllib.parse

from ..net import LOOKUP, NetError

API = "https://api.openalex.org/works"
CC = ("cc-by", "cc-by-sa", "cc-by-nc", "cc-by-nd", "cc-by-nc-sa", "cc-by-nc-nd", "cc0", "public-domain")


def by_doi(client, doi: str, items=()) -> dict | None:
    try:
        return client.get_json(f"{API}/doi:{urllib.parse.quote(doi, safe='/')}",
                               purpose=LOOKUP, items=items,
                               params={"select": "id,doi,title,publication_year,authorships,"
                                       "referenced_works,best_oa_location,primary_location,"
                                       "cited_by_count,ids,type"})
    except NetError:
        return None


def by_ids(client, ids: list[str]) -> list[dict]:
    """Batch lookup of OpenAlex work ids (W123...), 50 per request."""
    out = []
    for i in range(0, len(ids), 50):
        chunk = [x.rsplit("/", 1)[-1] for x in ids[i:i + 50]]
        try:
            d = client.get_json(API, purpose=LOOKUP, params={
                "filter": "openalex:" + "|".join(chunk), "per-page": 50,
                "select": "id,doi,title,publication_year,authorships,cited_by_count,type"})
        except NetError:
            continue
        out.extend(d.get("results", []))
    return out


def cc_licence(work: dict) -> str | None:
    """An explicit Creative Commons (or public domain) licence, else None.
    Open-access status alone is not enough: bronze OA is free to read, not to
    redistribute."""
    for loc in (work.get("best_oa_location"), work.get("primary_location")):
        lic = ((loc or {}).get("license") or "").lower()
        if lic in CC:
            return lic
    return None
