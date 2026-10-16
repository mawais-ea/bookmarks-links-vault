from pathlib import Path

from app.services.extractor import Metadata, hashtags_in, parse_open_graph

FIXTURES = Path(__file__).parent / "fixtures"


def test_open_graph():
    m = parse_open_graph((FIXTURES / "article.html").read_text(encoding="utf-8"))
    assert m.title == "15-Minute Garlic Butter Pasta"
    assert m.description.startswith("A quick weeknight dinner")
    assert m.thumbnail_url == "https://example.com/pasta.jpg"
    assert m.site_name == "Example Kitchen"
    assert "recipe" in m.hashtags


def test_title_tag_fallback():
    m = parse_open_graph("<html><head><title>Plain &amp; Simple</title></head></html>")
    assert m.title == "Plain & Simple"


def test_hashtags():
    assert hashtags_in("Love this #Recipe #quick_dinner!") == ["recipe", "quick_dinner"]


def test_merge_only_fills_gaps():
    a = Metadata(title="A", hashtags=["x"])
    a.merge(Metadata(title="B", description="d", hashtags=["x", "y"]))
    assert a.title == "A" and a.description == "d" and a.hashtags == ["x", "y"]


def test_generic_titles_use_the_caption():
    from app.services.extractor import better_title
    m = Metadata(title="Instagram", site_name="Instagram", description="7 Certificates in 7 days - Learn skills. #ai #coding")
    assert better_title(m, "instagram") == "7 Certificates in 7 days - Learn skills."
    assert better_title(Metadata(title="Instagram", author="Chef Ali"), "instagram") == "Instagram post by Chef Ali"
    assert better_title(Metadata(title="Garlic pasta", description="x"), "web") == "Garlic pasta"
