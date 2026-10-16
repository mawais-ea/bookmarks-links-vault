from app.services.categorizer import (
    DEFAULT_CATEGORIES, OTHER, UserRule, categorize, domain_matches, rule_categorize,
)

CATS = [c[0] for c in DEFAULT_CATEGORIES]


def test_domain_matching():
    assert domain_matches("https://github.com/a/b", "github.com")
    assert domain_matches("https://gist.github.com/a", "github.com")
    assert not domain_matches("https://notgithub.com/a", "github.com")
    assert domain_matches("https://www.amazon.co.uk/dp/1", "amazon.")
    assert domain_matches("https://www.bbc.com/news/world-1", "bbc.com/news")
    assert not domain_matches("https://www.bbc.com/sport/1", "bbc.com/news")


def test_domain_rule_wins():
    r = rule_categorize("https://github.com/psf/requests", "", [], CATS)
    assert r and r.category == "Tech & Programming"


def test_hashtags_decide():
    r = rule_categorize("https://instagram.com/reel/1", "Garlic butter pasta in 15 minutes",
                        ["recipe", "pasta", "easyrecipe"], CATS)
    assert r and r.category == "Recipes & Food"


def test_unclear_returns_none():
    assert rule_categorize("https://instagram.com/reel/1", "look at this", [], CATS) is None


def test_user_rules_override():
    rules = [UserRule("domain", "github.com", "Education & Learning")]
    r = rule_categorize("https://github.com/a", "", [], CATS, rules)
    assert r.category == "Education & Learning"
    kw = [UserRule("keyword", "sourdough", "Home & DIY")]
    r = rule_categorize("https://x.com/a/status/1", "my sourdough starter", [], CATS, kw)
    assert r.category == "Home & DIY"


async def test_categorize_without_llm_falls_back_to_other():
    r = await categorize({"url": "https://instagram.com/p/1", "title": "hmm"}, CATS, [])
    assert r.category == OTHER and r.source == "fallback"


class _Block:
    def __init__(self, type_, text=""):
        self.type, self.text = type_, text


class _FakeLLM:
    def __init__(self, payload, stop_reason="end_turn"):
        import json
        self.calls = []
        self._msg = type("M", (), {"stop_reason": stop_reason,
                                   "content": [_Block("thinking"), _Block("text", json.dumps(payload))]})()
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._msg


async def test_llm_result_used(monkeypatch):
    from app.services import categorizer
    monkeypatch.setattr(categorizer.get_settings(), "llm_enabled", True)
    fake = _FakeLLM({"category": "Recipes & Food", "description": "Garlic pasta in 15 minutes.",
                     "tags": ["Pasta"], "confidence": 0.9, "suggested_new_category": ""})
    monkeypatch.setattr(categorizer, "_llm", lambda: fake)
    r = await categorize({"url": "https://instagram.com/reel/1", "title": "dinner idea"}, CATS, [])
    assert (r.category, r.description, r.tags, r.source) == ("Recipes & Food", "Garlic pasta in 15 minutes.", ["pasta"], "llm")
    schema = fake.calls[0]["output_config"]["format"]["schema"]
    assert schema["properties"]["category"]["enum"] == CATS


async def test_llm_low_confidence_and_domain_override(monkeypatch):
    from app.services import categorizer
    monkeypatch.setattr(categorizer.get_settings(), "llm_enabled", True)
    low = _FakeLLM({"category": "Travel", "description": "d", "tags": [], "confidence": 0.2,
                    "suggested_new_category": "Gardening"})
    monkeypatch.setattr(categorizer, "_llm", lambda: low)
    r = await categorize({"url": "https://instagram.com/p/1", "title": "x"}, CATS, [])
    assert r.category == OTHER and r.suggested_new_category == "Gardening"

    r = await categorize({"url": "https://github.com/a/b", "title": "x"}, CATS, [])
    assert r.category == "Tech & Programming" and r.description == "d"


async def test_missing_credentials_falls_back_to_rules(monkeypatch):
    from app.services import categorizer

    class NoKey:
        class messages:
            @staticmethod
            async def create(**kw):
                raise TypeError("Could not resolve authentication method. Expected one of api_key...")
    monkeypatch.setattr(categorizer.get_settings(), "llm_enabled", True)
    monkeypatch.setattr(categorizer, "_llm", lambda: NoKey)
    r = await categorize({"url": "https://github.com/a/b", "title": "x"}, CATS, [])
    assert r.category == "Tech & Programming" and r.source == "rules"


def test_words_inside_hashtags_count():
    r = rule_categorize("https://instagram.com/reel/1", "30 free certificates. Hit follow.",
                        ["aicertification", "coding", "techcareers", "aiengineer"], CATS)
    assert r and r.category == "Tech & Programming"
    r = rule_categorize("https://instagram.com/p/1", "These websites should be hidden", ["websites"], CATS)
    assert r and r.category == "Tech & Programming"
