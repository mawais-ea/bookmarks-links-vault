from app.services import transcriber
from app.services.extractor import Metadata
from app.services.transcriber import format_segments, parse_json3, parse_vtt, pick_caption_track


def test_json3_and_paragraphs():
    data = {"events": [
        {"tStartMs": 0, "segs": [{"utf8": "Hello and welcome."}]},
        {"tStartMs": 1000},  # spacer event without text
        {"tStartMs": 12000, "segs": [{"utf8": "Today we cook "}, {"utf8": "pasta."}]},
        {"tStartMs": 41000, "segs": [{"utf8": "First, boil water."}]},
        {"tStartMs": 75000, "segs": [{"utf8": "Done &amp; dusted."}]},
    ]}
    text = format_segments(parse_json3(data))
    assert text == ("[0:00] Hello and welcome. Today we cook pasta.\n\n"
                    "[0:41] First, boil water.\n\n[1:15] Done & dusted.")


def test_vtt_rolling_captions_are_deduplicated():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:03.000
<c>so today</c>

00:00:03.000 --> 00:00:05.000
so today we're making

00:00:05.000 --> 00:00:07.000
so today we're making bread
"""
    assert format_segments(parse_vtt(vtt)) == "[0:01] so today we're making bread"


def test_long_videos_get_hour_timestamps():
    assert format_segments([(3725, "Late in the video.")]) == "[1:02:05] Late in the video."


def test_caption_track_preference():
    def track(ext="json3"):
        return [{"ext": "vtt", "url": "u-vtt"}, {"ext": ext, "url": f"u-{ext}"}]
    info = {"language": "es", "subtitles": {"en": track(), "es": track()},
            "automatic_captions": {"es-orig": track(), "fr": track()}}
    assert pick_caption_track(info) == ("u-json3", "json3", "captions")  # uploader subs in video language
    info = {"language": "es", "subtitles": {}, "automatic_captions": {"es-orig": track(), "en": track()}}
    assert pick_caption_track(info)[2] == "auto-captions"
    assert pick_caption_track({"subtitles": {"live_chat": track()}}) is None


def test_bookmark_gets_caption_transcript(client, fake_extract):
    fake_extract["youtu"] = Metadata(title="Pasta video", is_video=True, duration=60,
                                           transcript="[0:00] Boil the water.", transcript_source="captions")
    bm = client.post("/bookmarks", json={"url": "https://youtu.be/abc"}).json()
    full = client.get(f"/bookmarks/{bm['id']}").json()
    assert full["transcript"] == "[0:00] Boil the water."
    assert (full["transcript_status"], full["transcript_source"]) == ("ready", "captions")
    listed = client.get("/bookmarks").json()["items"][0]
    assert listed["transcript"] is None and listed["transcript_status"] == "ready"  # keeps sync light
    assert [b["id"] for b in client.get("/bookmarks?q=water").json()["items"]] == [bm["id"]]


def test_video_without_captions_uses_speech_to_text(client, fake_extract, monkeypatch):
    fake_extract["tiktok.com"] = Metadata(title="Reel", is_video=True, duration=30)
    monkeypatch.setattr(transcriber, "can_transcribe_audio", lambda d: True)
    monkeypatch.setattr(transcriber, "whisper_transcribe", lambda url: "[0:00] Spoken words.")
    bm = client.post("/bookmarks", json={"url": "https://tiktok.com/@a/video/1"}).json()
    full = client.get(f"/bookmarks/{bm['id']}").json()
    assert (full["transcript"], full["transcript_source"], full["status"]) == (
        "[0:00] Spoken words.", "speech-to-text", "ready")


def test_video_with_nothing_to_transcribe(client, fake_extract):
    fake_extract["tiktok.com"] = Metadata(title="Reel", is_video=True, duration=30)
    bm = client.post("/bookmarks", json={"url": "https://tiktok.com/@a/video/2"}).json()
    assert client.get(f"/bookmarks/{bm['id']}").json()["transcript_status"] == "none"


def test_prefers_english_when_asked():
    def track():
        return [{"ext": "json3", "url": "u"}]
    info = {"language": "hi", "subtitles": {}, "automatic_captions": {"hi-orig": track(), "en": track()}}
    assert pick_caption_track(info)[2] == "auto-captions"  # original language by default (for Claude)
    assert pick_caption_track(info, prefer="en")[2] == "translated captions"
    info_en = {"language": "en", "subtitles": {"en": track()}, "automatic_captions": {"en": track()}}
    assert pick_caption_track(info_en, prefer="en")[2] == "captions"


def test_prompt_asks_for_english():
    from app.services.categorizer import build_prompt
    p = build_prompt({"url": "u", "title": "चिकन कड़ाही"}, ["Recipes & Food"], [])
    assert "Write it in English, translating" in p and "Write the recipe in English" in p
