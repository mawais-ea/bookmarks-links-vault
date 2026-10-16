import json

from app.services import categorizer
from app.services.extractor import Metadata
from app.services.recipes import as_text, iso_duration, parse_jsonld_recipe, recipe_from_text

JSONLD_PAGE = """<html><head><script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [
  {"@type": "WebPage", "name": "x"},
  {"@type": ["Recipe"], "name": "Garlic Butter Pasta", "recipeYield": ["4", "4 servings"], "totalTime": "PT1H5M",
   "recipeIngredient": ["200 g spaghetti", "3 cloves garlic", "40 g butter"],
   "recipeInstructions": [
     {"@type": "HowToSection", "name": "Pasta", "itemListElement": [
        {"@type": "HowToStep", "text": "Boil the spaghetti for 9 minutes."}]},
     {"@type": "HowToStep", "text": "Melt the butter &amp; fry the garlic."}]}
]}</script></head><body></body></html>"""

CAPTION = """Easy 15 min garlic pasta 🍝

Ingredients:
- 200g spaghetti
- 3 cloves garlic
- 2 tbsp butter
- parmesan

Method:
1. Boil pasta in salted water
2. Fry garlic in butter
3. Toss together with parmesan

#recipe #pasta"""


def test_iso_duration():
    assert iso_duration("PT1H5M") == "1 h 5 min"
    assert iso_duration("PT90M") == "1 h 30 min"
    assert iso_duration("P0DT0H20M") == "20 min"
    assert iso_duration("nonsense") is None


def test_jsonld_recipe():
    r = parse_jsonld_recipe(JSONLD_PAGE)
    assert r["title"] == "Garlic Butter Pasta" and r["servings"] == "4 servings" and r["time"] == "1 h 5 min"
    assert r["ingredients"] == ["200 g spaghetti", "3 cloves garlic", "40 g butter"]
    assert r["steps"] == ["Pasta: Boil the spaghetti for 9 minutes.", "Melt the butter & fry the garlic."]
    assert "1. Pasta: Boil" in as_text(r)


def test_caption_recipe():
    r = recipe_from_text(CAPTION)
    assert r["ingredients"] == ["200g spaghetti", "3 cloves garlic", "2 tbsp butter", "parmesan"]
    assert r["steps"] == ["Boil pasta in salted water", "Fry garlic in butter", "Toss together with parmesan"]
    assert recipe_from_text("Just a funny video about my cat\n#cats") is None


def test_site_recipe_saved_and_category_set(client, fake_extract):
    fake_extract["food.example"] = Metadata(title="Garlic pasta", recipe=parse_jsonld_recipe(JSONLD_PAGE))
    client.post("/bookmarks", json={"url": "https://food.example/garlic-pasta"})
    bm = client.get("/bookmarks").json()["items"][0]
    assert bm["recipe_source"] == "site" and len(bm["recipe"]["ingredients"]) == 3
    assert bm["category_name"] == "Recipes & Food" and not bm["needs_review"]


def test_caption_recipe_without_claude(client, fake_extract):
    fake_extract["instagram.com"] = Metadata(title="Pasta reel", raw_text=CAPTION)
    client.post("/bookmarks", json={"url": "https://instagram.com/reel/abc"})
    bm = client.get("/bookmarks").json()["items"][0]
    assert bm["recipe_source"] == "quick" and bm["recipe"]["steps"][0] == "Boil pasta in salted water"


def test_claude_recipe(client, fake_extract, monkeypatch):
    payload = {"category": "Recipes & Food", "description": "Garlic pasta.", "tags": [], "confidence": 0.9,
               "suggested_new_category": "", "summary": "",
               "recipe": {"is_recipe": True, "servings": "2", "time": "15 min",
                          "ingredients": ["200 g spaghetti", "3 cloves garlic"],
                          "steps": ["Boil the pasta.", "Fry the garlic.", "Combine."], "notes": ""}}

    class Client:
        class messages:
            @staticmethod
            async def create(**kw):
                return type("M", (), {"stop_reason": "end_turn",
                                      "content": [type("B", (), {"type": "text", "text": json.dumps(payload)})()]})
    monkeypatch.setattr(categorizer.get_settings(), "llm_enabled", True)
    monkeypatch.setattr(categorizer, "_llm", lambda: Client)
    fake_extract["tiktok.com"] = Metadata(title="Pasta", raw_text="garlic pasta in 15 minutes", is_video=True)
    client.post("/bookmarks", json={"url": "https://tiktok.com/@chef/video/1"})
    bm = client.get("/bookmarks").json()["items"][0]
    assert bm["recipe_source"] == "ai" and bm["recipe"]["servings"] == "2" and len(bm["recipe"]["steps"]) == 3

    payload["recipe"] = {"is_recipe": False, "servings": "", "time": "", "ingredients": [], "steps": [], "notes": ""}
    fake_extract["tiktok.com"] = Metadata(title="Dance", raw_text="dance video", is_video=True)
    client.post("/bookmarks", json={"url": "https://tiktok.com/@dancer/video/2"})
    assert client.get("/bookmarks").json()["items"][0]["recipe"] is None


def test_numbered_lists_are_not_recipes():
    caption = """5 websites to legally copy profitable startup ideas 👇

1️⃣ Betalist: Early-stage US startups in beta.
2️⃣ TrustMRR: Check verified revenue before building.
3️⃣ MicroLaunch: Discover tech tools & launch your own.
4️⃣ Uneed.best: Handpicked AI startup inspiration.
5️⃣ Arrfounder: Direct founder details to pitch your services."""
    assert recipe_from_text(caption) is None
    # Real measurements without a heading still count.
    assert recipe_from_text("200g pasta\n2 tbsp butter\n3 cloves garlic\nparmesan") is not None


def test_caption_guess_does_not_force_recipes_category(client, fake_extract):
    fake_extract["instagram.com"] = Metadata(title="Hmm", raw_text="Ingredients:\n- one\n- two\n- three\n- four")
    client.post("/bookmarks", json={"url": "https://instagram.com/reel/guess"})
    bm = client.get("/bookmarks").json()["items"][0]
    assert bm["recipe_source"] == "quick" and bm["category_name"] != "Recipes & Food"


def _cats(client):
    return {c["name"]: c["id"] for c in client.get("/categories").json()}


REEL_CAPTION = """Creamy garlic chicken 🍗 save this!
🧄 4 cloves garlic
🍗 500g chicken thighs
🥛 1 cup cream
🧂 salt and pepper
1. Season and sear the chicken 5 min each side
2. Add garlic, then cream, simmer 10 min
#dinner"""


def test_moving_to_recipes_gets_the_recipe(client, fake_extract):
    fake_extract["instagram.com"] = Metadata(title="Creamy garlic chicken", raw_text=REEL_CAPTION)
    bm = client.post("/bookmarks", json={"url": "https://instagram.com/reel/chicken"}).json()
    before = client.get(f"/bookmarks/{bm['id']}").json()
    if before["category_name"] != "Recipes & Food":  # the rules may already file it there
        assert not before["recipe"] or before["recipe_source"] == "quick"
    r = client.patch(f"/bookmarks/{bm['id']}", json={"category_id": _cats(client)["Other"]})
    r = client.patch(f"/bookmarks/{bm['id']}", json={"category_id": _cats(client)["Recipes & Food"]})
    after = client.get(f"/bookmarks/{bm['id']}").json()
    assert after["category_name"] == "Recipes & Food" and after["recipe"], after["recipe_source"]
    assert "500g chicken thighs" in after["recipe"]["ingredients"]
    assert after["recipe"]["steps"][0].startswith("Season and sear")


def test_no_recipe_found_says_so_and_can_retry(client, fake_extract):
    fake_extract["example.org"] = Metadata(title="My holiday", raw_text="Lovely beach day.")
    bm = client.post("/bookmarks", json={"url": "https://example.org/beach"}).json()
    client.patch(f"/bookmarks/{bm['id']}", json={"category_id": _cats(client)["Recipes & Food"]})
    assert client.get(f"/bookmarks/{bm['id']}").json()["recipe_source"] == "none"

    fake_extract["example.org"] = Metadata(title="My holiday", raw_text="- 2 eggs\n- 1 cup flour\n1. Mix\n2. Fry")
    import app.services.recipe_job as job
    from app.models import Bookmark
    from app.db import SessionLocal
    with SessionLocal() as db:  # the post's caption changed since (e.g. author edited it)
        db.get(Bookmark, bm["id"]).raw_text = "- 2 eggs\n- 1 cup flour\n1. Mix\n2. Fry"
        db.commit()
    r = client.post(f"/bookmarks/{bm['id']}/recipe")
    assert r.status_code == 200
    got = client.get(f"/bookmarks/{bm['id']}").json()
    assert got["recipe_source"] == "quick" and got["recipe"]["ingredients"] == ["2 eggs", "1 cup flour"]


def test_claude_writes_recipe_when_filed_by_hand(client, fake_extract, monkeypatch):
    from app.services import categorizer
    calls = {}

    async def fake_llm_recipe(meta):
        calls["meta"] = meta
        return {"title": None, "servings": "2", "time": "", "ingredients": ["rice", "dal"],
                "steps": ["Cook rice.", "Cook dal."], "notes": "Amounts not given."}
    monkeypatch.setattr(categorizer, "llm_recipe", fake_llm_recipe)
    fake_extract["tiktok.com"] = Metadata(title="Daal chawal", raw_text="comfort food", is_video=True,
                                          transcript="[0:00] first wash the rice")
    bm = client.post("/bookmarks", json={"url": "https://tiktok.com/@a/video/9"}).json()
    client.patch(f"/bookmarks/{bm['id']}", json={"category_id": _cats(client)["Recipes & Food"]})
    got = client.get(f"/bookmarks/{bm['id']}").json()
    assert got["recipe_source"] == "ai" and got["recipe"]["steps"] == ["Cook rice.", "Cook dal."]
