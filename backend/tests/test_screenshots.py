import io
import json

from PIL import Image, ImageDraw, ImageFont

from app.services import categorizer, screenshots


def make_png(text="Flight LHE to DXB\nDeparts 3 Nov 09:40\nBooking ref QX7Z2P", size=(900, 400)) -> bytes:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    draw.multiline_text((30, 40), text, fill="black", font=ImageFont.load_default(size=44), spacing=20)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def upload(client, data=None, name="shot.png", **form):
    return client.post("/screenshots", files={"file": (name, data or make_png(), "image/png")}, data=form)


def test_upload_goes_to_screenshots_category(client, monkeypatch):
    monkeypatch.setattr(screenshots, "read_text_locally", lambda p: None)
    r = upload(client, taken_at="2026-10-14T09:30:00Z")
    assert r.status_code == 201, r.text
    bm = client.get(f"/bookmarks/{r.json()['id']}").json()
    assert bm["platform"] == "screenshot" and bm["category_name"] == "Screenshots" and bm["status"] == "ready"
    assert bm["title"].startswith("Screenshot 14 Oct 2026") and not bm["needs_review"]

    img = client.get(bm["thumbnail_url"], headers={"Authorization": ""})  # signed URL works without login
    assert img.status_code == 200 and img.content.startswith(b"\x89PNG")
    assert client.get(bm["thumbnail_url"].split("?")[0] + "?sig=wrong").status_code == 404


def test_download_is_named_after_the_title(client, monkeypatch):
    monkeypatch.setattr(screenshots, "read_text_locally", lambda p: None)
    bm = upload(client).json()
    client.patch(f"/bookmarks/{bm['id']}", json={"title": "Flight: LHE/DXB"})
    r = client.get(bm["url"] + "&download=1", headers={"Authorization": ""})
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    assert r.headers["content-disposition"] == "attachment; filename*=utf-8''Flight%20LHE%20DXB.png"
    assert "content-disposition" not in client.get(bm["url"]).headers  # shown in the app, not saved


def test_same_image_twice_is_one_bookmark(client, monkeypatch):
    monkeypatch.setattr(screenshots, "read_text_locally", lambda p: None)
    data = make_png()
    assert upload(client, data).json()["id"] == upload(client, data).json()["id"]
    assert len(client.get("/bookmarks").json()["items"]) == 1


def test_rejects_non_images(client):
    r = client.post("/screenshots", files={"file": ("x.png", b"not an image", "image/png")})
    assert r.status_code == 422


def test_category_created_for_existing_accounts(client, monkeypatch):
    monkeypatch.setattr(screenshots, "read_text_locally", lambda p: None)
    cats = {c["name"]: c for c in client.get("/categories").json()}
    client.delete(f"/categories/{cats['Screenshots']['id']}")
    upload(client)
    cats = [c["name"] for c in client.get("/categories").json()]
    assert "Screenshots" in cats and cats.index("Screenshots") == cats.index("Other") - 1


def test_claude_reads_the_screenshot(client, monkeypatch):
    seen = {}
    payload = {"title": "Flight booking: LHE to DXB, 3 Nov", "description": "Booking confirmation.",
               "summary": "Flight details.\n- Departs 09:40", "text": "Flight LHE to DXB\nDeparts 3 Nov 09:40",
               "tags": ["travel"]}

    class Client:
        class messages:
            @staticmethod
            async def create(**kw):
                seen["content"] = kw["messages"][0]["content"]
                return type("M", (), {"stop_reason": "end_turn",
                                      "content": [type("B", (), {"type": "text", "text": json.dumps(payload)})()]})
    monkeypatch.setattr(categorizer.get_settings(), "llm_enabled", True)
    monkeypatch.setattr(categorizer.get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(categorizer, "_llm", lambda: Client)
    bm = upload(client).json()
    bm = client.get(f"/bookmarks/{bm['id']}").json()
    assert bm["title"] == "Flight booking: LHE to DXB, 3 Nov" and bm["summary_source"] == "ai"
    assert seen["content"][0]["type"] == "image" and seen["content"][0]["source"]["media_type"] == "image/png"
    assert [b["id"] for b in client.get("/bookmarks?q=DXB").json()["items"]] == [bm["id"]]


def test_local_ocr_reads_text(client):
    bm = upload(client).json()
    bm = client.get(f"/bookmarks/{bm['id']}").json()
    assert bm["status"] == "ready"
    found = [b["id"] for b in client.get("/bookmarks?q=QX7Z2P").json()["items"]]
    assert found == [bm["id"]], "OCR should make the booking reference searchable"


def test_phone_upload_raw_body(client, monkeypatch):
    monkeypatch.setattr(screenshots, "read_text_locally", lambda p: None)
    no_auth = {"Authorization": ""}
    r = client.post(f"/u/{client.api_key}", content=make_png(), headers={**no_auth, "Content-Type": "image/png"})
    assert r.status_code == 200 and r.text.startswith("Saved ✓ Screenshot")
    bm = client.get("/bookmarks").json()["items"][0]
    assert bm["platform"] == "screenshot" and bm["category_name"] == "Screenshots"

    # Multipart works too, and the same image doesn't make a second copy.
    r = client.post(f"/u/{client.api_key}", files={"file": ("a.png", make_png(), "image/png")}, headers=no_auth)
    assert r.status_code == 200 and len(client.get("/bookmarks").json()["items"]) == 1

    assert client.post("/u/lv_wrong", content=make_png(), headers=no_auth).status_code == 401
    assert client.post(f"/u/{client.api_key}", content=b"", headers=no_auth).status_code == 422


def test_heic_photos_are_accepted(client, monkeypatch):
    import pytest
    if not screenshots.HEIC_SUPPORTED:
        pytest.skip("pillow-heif can't load on this machine")
    import pillow_heif
    monkeypatch.setattr(screenshots, "read_text_locally", lambda p: None)
    img = Image.open(io.BytesIO(make_png("HEIC photo")))
    heif = pillow_heif.from_pillow(img)
    buf = io.BytesIO()
    heif.save(buf, quality=80)
    r = client.post(f"/u/{client.api_key}", content=buf.getvalue(), headers={"Authorization": ""})
    assert r.status_code == 200, r.text
    bm = client.get("/bookmarks").json()["items"][0]
    assert bm["thumbnail_url"].split("?")[0].endswith(".png")


def test_heic_without_support_gives_a_clear_message(client, monkeypatch):
    monkeypatch.setattr(screenshots, "read_text_locally", lambda p: None)
    fake_heic = bytes(3) + bytes([24]) + b"ftypheic" + bytes(64)
    r = client.post(f"/u/{client.api_key}", content=fake_heic, headers={"Authorization": ""})
    assert r.status_code == 422 and "Convert Image" in r.text
