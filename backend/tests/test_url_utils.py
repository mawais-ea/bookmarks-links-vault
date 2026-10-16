import pytest

from app.services.url_utils import detect_platform, extract_url, is_short_link, normalize


@pytest.mark.parametrize("raw, expected", [
    ("https://www.instagram.com/reel/C1abc/?igsh=MTc4MmM1YmI2Ng==", "https://instagram.com/reel/C1abc"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc123", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/abc123?feature=share", "https://youtube.com/watch?v=abc123"),
    ("https://m.youtube.com/watch?v=xyz&t=42&si=q", "https://youtube.com/watch?v=xyz&t=42"),
    ("https://x.com/user/status/123?s=20&t=abcdef", "https://x.com/user/status/123"),
    ("https://example.com/post/?utm_source=ig&utm_medium=social&id=7", "https://example.com/post?id=7"),
    ("http://www.reddit.com/r/python/comments/abc/title/", "https://reddit.com/r/python/comments/abc/title"),
    ("example.com/page", "https://example.com/page"),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize("url, platform", [
    ("https://instagram.com/p/x", "instagram"),
    ("https://vm.tiktok.com/ZM123", "tiktok"),
    ("https://youtube.com/watch?v=1", "youtube"),
    ("https://twitter.com/a/status/1", "x"),
    ("https://fb.watch/abc", "facebook"),
    ("https://pin.it/xyz", "pinterest"),
    ("https://www.threads.net/@a/post/1", "threads"),
    ("https://notyoutube.com/x", "web"),
    ("https://github.com/a/b", "web"),
])
def test_detect_platform(url, platform):
    assert detect_platform(url) == platform


def test_extract_url_from_shared_text():
    text = "Check out this reel! https://www.instagram.com/reel/C1abc/?igsh=xyz."
    assert extract_url(text) == "https://www.instagram.com/reel/C1abc/?igsh=xyz"
    assert extract_url("no link here") is None


def test_short_links():
    assert is_short_link("https://vm.tiktok.com/ZM123/")
    assert is_short_link("https://www.reddit.com/r/x/s/abc")
    assert not is_short_link("https://tiktok.com/@a/video/1")
