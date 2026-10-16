import json

from app.services import categorizer
from app.services.extractor import Metadata
from app.services.summarizer import extractive_summary

ARTICLE = " ".join([
    "Sourdough bread needs only flour, water and salt.",
    "The starter must be fed daily for a week before baking.",
    "Many people give up because the starter smells strange at first.",
    "Mix the dough and let it rest for one hour before adding salt.",
    "Stretch and fold the dough four times during the first two hours.",
    "The weather today was cloudy and a little cold outside.",
    "Bake the bread at 250 degrees in a covered pot for twenty minutes.",
    "Remove the lid and bake the sourdough bread twenty more minutes until dark.",
    "Let the bread cool for an hour before slicing it.",
] * 3)


def test_extractive_summary_picks_key_sentences():
    s = extractive_summary(ARTICLE)
    lines = s.splitlines()
    assert 3 <= len(lines) <= 6 and all(line.startswith("- ") for line in lines)
    assert "bread" in s.lower()
    assert extractive_summary("Too short to summarize.") is None


def test_unpunctuated_captions_are_chunked():
    captions = "[0:00] " + " ".join(f"today we talk about investing money in index funds part {i}" for i in range(30))
    s = extractive_summary(captions)
    assert s and "[0:00]" not in s


def _fake_llm(payload):
    class Msg:
        stop_reason = "end_turn"
        content = [type("B", (), {"type": "text", "text": json.dumps(payload)})()]

    class Client:
        class messages:
            @staticmethod
            async def create(**kw):
                Client.prompt = kw["messages"][0]["content"]
                return Msg
    return Client


def test_claude_summary_is_saved(client, fake_extract, monkeypatch):
    llm = _fake_llm({"category": "Recipes & Food", "description": "How to bake sourdough.", "tags": ["bread"],
                     "confidence": 0.95, "suggested_new_category": "",
                     "summary": "A sourdough guide.\n- Feed the starter daily\n- Bake at 250 degrees"})
    monkeypatch.setattr(categorizer.get_settings(), "llm_enabled", True)
    monkeypatch.setattr(categorizer, "_llm", lambda: llm)
    fake_extract["youtu"] = Metadata(title="Sourdough", is_video=True, duration=600,
                                     transcript="[0:00] " + ARTICLE, transcript_source="captions")
    bm = client.post("/bookmarks", json={"url": "https://youtu.be/bread"}).json()
    bm = client.get("/bookmarks").json()["items"][0]  # summary is part of the normal sync
    assert bm["summary"].startswith("A sourdough guide.") and bm["summary_source"] == "ai"
    assert "Video transcript:" in llm.prompt and "Let the bread cool" in llm.prompt


def test_quick_summary_without_claude(client, fake_extract):
    fake_extract["blog.example"] = Metadata(title="Sourdough", raw_text=ARTICLE)
    client.post("/bookmarks", json={"url": "https://blog.example/bread"})
    bm = client.get("/bookmarks").json()["items"][0]
    assert bm["summary_source"] == "quick" and bm["summary"].startswith("- ")
    assert [b["id"] for b in client.get("/bookmarks?q=covered").json()["items"]] == [bm["id"]]


def test_quick_summary_skips_non_english_text(client, fake_extract):
    hindi = "करके गरम होने दें और फिर पाच से सात मिनट लो टू मीडियम फ्लेम पे कुक कर लें और फिर पेस्ट बना लें. " * 20
    fake_extract["youtu"] = Metadata(title="Karahi", is_video=True, transcript=hindi, transcript_source="auto-captions",
                                     raw_text=ARTICLE)
    client.post("/bookmarks", json={"url": "https://youtu.be/karahi"})
    bm = client.get("/bookmarks").json()["items"][0]
    assert bm["summary_source"] == "quick" and "bread" in bm["summary"].lower()  # used the English description

    fake_extract["youtu"] = Metadata(title="Karahi", is_video=True, transcript=hindi, raw_text=hindi)
    client.post("/bookmarks", json={"url": "https://youtu.be/karahi2"})
    assert client.get("/bookmarks").json()["items"][0]["summary"] is None  # nothing English to show
