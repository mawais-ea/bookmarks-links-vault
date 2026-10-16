from datetime import datetime, timedelta, timezone

from app.services.extractor import Metadata


def _cats(client):
    return {c["name"]: c for c in client.get("/categories").json()}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_requires_auth(client):
    assert client.get("/bookmarks", headers={"Authorization": ""}).status_code == 401


def test_save_extract_and_categorize(client, fake_extract):
    fake_extract["github.com"] = Metadata(
        title="psf/requests", description="A simple, yet elegant, HTTP library. Used by millions.",
        thumbnail_url="https://opengraph.githubassets.com/x.png",
    )
    r = client.post("/bookmarks", json={"url": "Look: https://github.com/psf/requests?utm_source=x"})
    assert r.status_code == 201
    assert r.json()["status"] == "processing"  # saved instantly, enriched in background

    bm = client.get(f"/bookmarks/{r.json()['id']}").json()
    assert bm["status"] == "ready"
    assert bm["title"] == "psf/requests"
    assert bm["category_name"] == "Tech & Programming"
    assert bm["canonical_url"] == "https://github.com/psf/requests"
    assert bm["description"] == "A simple, yet elegant, HTTP library."


def test_api_key_auth_and_duplicates(client, fake_extract):
    h = {"Authorization": "", "X-API-Key": client.api_key}
    a = client.post("/bookmarks", json={"url": "https://youtu.be/abc?si=1"}, headers=h).json()
    b = client.post("/bookmarks", json={"url": "https://www.youtube.com/watch?v=abc"}, headers=h).json()
    assert a["id"] == b["id"]
    assert len(client.get("/bookmarks").json()["items"]) == 1


def test_unclear_goes_to_other_and_review(client, fake_extract):
    fake_extract["instagram.com"] = Metadata(title="hmm", raw_text="look at this")
    bm = client.post("/bookmarks", json={"url": "https://instagram.com/reel/1"}).json()
    bm = client.get(f"/bookmarks/{bm['id']}").json()
    assert bm["category_name"] == "Other" and bm["needs_review"]
    assert [b["id"] for b in client.get("/bookmarks?review=true").json()["items"]] == [bm["id"]]


def test_move_records_correction_and_learns_domain_rule(client, fake_extract):
    travel = _cats(client)["Travel"]["id"]
    for i in range(3):
        bm = client.post("/bookmarks", json={"url": f"https://someblog.net/post-{i}"}).json()
        r = client.patch(f"/bookmarks/{bm['id']}", json={"category_id": travel})
        assert r.json()["category_name"] == "Travel"
    rules = client.get("/rules").json()
    assert [(x["pattern"], x["category_name"]) for x in rules] == [("someblog.net", "Travel")]

    new = client.post("/bookmarks", json={"url": "https://someblog.net/post-99"}).json()
    assert client.get(f"/bookmarks/{new['id']}").json()["category_name"] == "Travel"


def test_accept_suggested_category_creates_it(client, fake_extract):
    bm = client.post("/bookmarks", json={"url": "https://plants.example/tomatoes"}).json()
    r = client.patch(f"/bookmarks/{bm['id']}", json={"category": "Gardening"})
    assert r.json()["category_name"] == "Gardening"
    assert "Gardening" in _cats(client)


def test_search_and_filters(client, fake_extract):
    fake_extract["a.example"] = Metadata(title="Sourdough guide", raw_text="starter, flour, water")
    fake_extract["b.example"] = Metadata(title="Leg day", raw_text="squats")
    client.post("/bookmarks", json={"url": "https://a.example/1"})
    b = client.post("/bookmarks", json={"url": "https://b.example/1"}).json()
    client.patch(f"/bookmarks/{b['id']}", json={"is_favorite": True})

    assert [x["title"] for x in client.get("/bookmarks?q=flour").json()["items"]] == ["Sourdough guide"]
    assert [x["title"] for x in client.get("/bookmarks?favorite=true").json()["items"]] == ["Leg day"]


def test_sync_since_includes_deletions(client, fake_extract):
    bm = client.post("/bookmarks", json={"url": "https://a.example/1"}).json()
    before = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert client.delete(f"/bookmarks/{bm['id']}").status_code == 204

    assert client.get("/bookmarks").json()["items"] == []
    changed = client.get("/bookmarks", params={"since": before}).json()["items"]
    assert changed[0]["id"] == bm["id"] and changed[0]["deleted_at"] is not None


def test_category_crud_and_counts(client, fake_extract):
    r = client.post("/categories", json={"name": "Gardening", "icon": "yard"})
    assert r.status_code == 201
    gid = r.json()["id"]
    bm = client.post("/bookmarks", json={"url": "https://a.example/1"}).json()
    client.patch(f"/bookmarks/{bm['id']}", json={"category_id": gid})
    assert _cats(client)["Gardening"]["count"] == 1

    assert client.delete(f"/categories/{gid}").status_code == 204
    assert client.get(f"/bookmarks/{bm['id']}").json()["category_name"] == "Other"
    assert client.delete(f"/categories/{_cats(client)['Other']['id']}").status_code == 400


def test_export_formats(client, fake_extract):
    client.post("/bookmarks", json={"url": "https://github.com/a/b"})
    assert client.get("/export?format=json").json()[0]["url"] == "https://github.com/a/b"
    assert "github.com/a/b" in client.get("/export?format=csv").text
    assert "<H3>" in client.get("/export?format=html").text


def test_login(client):
    r = client.post("/auth/login", json={"email": "ME@example.com", "password": "correct-horse"})
    assert r.status_code == 200 and r.json()["api_key"] == client.api_key
    assert client.post("/auth/login", json={"email": "me@example.com", "password": "wrong-pass"}).status_code == 401


def test_manual_order(client, fake_extract):
    ids = [client.post("/bookmarks", json={"url": f"https://a.example/{i}"}).json()["id"] for i in range(3)]
    listed = client.get("/bookmarks").json()["items"]
    assert [b["id"] for b in listed] == ids[::-1]  # newest on top by default

    # Drag the oldest between the other two.
    top, middle = listed[0]["position"], listed[1]["position"]
    client.patch(f"/bookmarks/{ids[0]}", json={"position": (top + middle) / 2})
    assert [b["id"] for b in client.get("/bookmarks").json()["items"]] == [ids[2], ids[0], ids[1]]

    # Re-sharing a link moves it back to the top.
    client.post("/bookmarks", json={"url": "https://a.example/1"})
    assert client.get("/bookmarks").json()["items"][0]["id"] == ids[1]


def test_category_reorder(client):
    cats = client.get("/categories").json()
    wanted = [cats[2]["id"], cats[0]["id"]]
    r = client.post("/categories/reorder", json={"ids": wanted})
    assert [c["id"] for c in r.json()][:2] == wanted
    assert [c["id"] for c in client.get("/categories").json()][:2] == wanted
    assert len(r.json()) == len(cats)
    assert client.post("/categories/reorder", json={"ids": [999999]}).status_code == 404


def test_shortcut_save_links(client, fake_extract):
    key = client.api_key
    shared = "https://www.instagram.com/reel/C1abc/?igsh=xyz&utm_source=ig"
    r = client.get(f"/s/{key}/{shared}", headers={"Authorization": ""})
    assert r.status_code == 200 and r.text.startswith("Saved ✓")
    assert client.get("/bookmarks").json()["items"][0]["canonical_url"] == "https://instagram.com/reel/C1abc"

    # A proxy squashing "//" and a percent-encoded link both still work.
    client.get(f"/s/{key}/https:/youtube.com/watch?v=abc", headers={"Authorization": ""})
    assert client.get("/bookmarks").json()["items"][0]["canonical_url"] == "https://youtube.com/watch?v=abc"
    from urllib.parse import quote
    r = client.get(f"/save?key={key}&url={quote('https://x.com/a/status/1?s=20', safe='')}",
                   headers={"Authorization": ""})
    assert r.status_code == 200
    assert client.get("/bookmarks").json()["items"][0]["canonical_url"] == "https://x.com/a/status/1"

    assert client.get("/s/lv_wrong/https://e.com", headers={"Authorization": ""}).status_code == 401


def test_api_key_hidden_in_access_log():
    import logging
    from app.main import _HideApiKeys
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s"',
                            ("1.2.3.4", "GET", "/s/lv_SECRET123/https://e.com?key=lv_ABC"), None)
    _HideApiKeys().filter(rec)
    msg = rec.getMessage()
    assert "SECRET" not in msg and "lv_ABC" not in msg and "/s/lv_***/" in msg
