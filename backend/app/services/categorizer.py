"""Categorization: rules first (free, instant), then Claude for anything unclear."""

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field

import anthropic

from ..config import get_settings
from . import recipes

log = logging.getLogger(__name__)

OTHER = "Other"

# name, icon (Material icon name), color
DEFAULT_CATEGORIES: list[tuple[str, str, str]] = [
    ("Recipes & Food", "restaurant", "#E64A19"),
    ("Tech & Programming", "code", "#1976D2"),
    ("Fitness & Health", "fitness_center", "#388E3C"),
    ("Travel", "flight", "#0097A7"),
    ("Finance & Money", "savings", "#2E7D32"),
    ("Education & Learning", "school", "#5E35B1"),
    ("Fashion & Beauty", "checkroom", "#D81B60"),
    ("Home & DIY", "home_repair_service", "#8D6E63"),
    ("Entertainment & Memes", "theater_comedy", "#FBC02D"),
    ("News", "newspaper", "#455A64"),
    ("Shopping", "shopping_bag", "#F57C00"),
    ("Music", "music_note", "#7B1FA2"),
    ("Motivation", "bolt", "#FF7043"),
    ("Business & Career", "work", "#3949AB"),
    ("Screenshots", "screenshot", "#00897B"),
    (OTHER, "folder", "#78909C"),
]

DOMAIN_RULES: dict[str, str] = {
    "github.com": "Tech & Programming", "gitlab.com": "Tech & Programming",
    "stackoverflow.com": "Tech & Programming", "dev.to": "Tech & Programming",
    "news.ycombinator.com": "Tech & Programming", "pypi.org": "Tech & Programming",
    "medium.com": "Education & Learning", "coursera.org": "Education & Learning",
    "udemy.com": "Education & Learning", "khanacademy.org": "Education & Learning",
    "wikipedia.org": "Education & Learning",
    "allrecipes.com": "Recipes & Food", "bbcgoodfood.com": "Recipes & Food",
    "seriouseats.com": "Recipes & Food", "food.com": "Recipes & Food", "tasty.co": "Recipes & Food",
    "nytimes.com/cooking": "Recipes & Food", "budgetbytes.com": "Recipes & Food",
    "amazon.": "Shopping", "ebay.": "Shopping", "aliexpress.com": "Shopping", "etsy.com": "Shopping",
    "temu.com": "Shopping", "shein.com": "Shopping", "walmart.com": "Shopping", "amzn.to": "Shopping",
    "booking.com": "Travel", "airbnb.": "Travel", "tripadvisor.": "Travel", "expedia.": "Travel",
    "skyscanner.": "Travel",
    "spotify.com": "Music", "soundcloud.com": "Music", "music.apple.com": "Music", "bandcamp.com": "Music",
    "bbc.com/news": "News", "bbc.co.uk/news": "News", "cnn.com": "News", "reuters.com": "News",
    "apnews.com": "News", "theguardian.com": "News", "aljazeera.com": "News",
    "investopedia.com": "Finance & Money", "bloomberg.com": "Finance & Money",
    "linkedin.com": "Business & Career", "glassdoor.": "Business & Career", "indeed.com": "Business & Career",
}

KEYWORD_RULES: dict[str, list[str]] = {
    "Recipes & Food": ["recipe", "recipes", "ingredients", "cooking", "baking", "dinner", "lunch", "breakfast",
                       "dessert", "foodie", "airfryer", "mealprep", "tbsp", "tsp", "oven", "pasta", "chicken",
                       "vegan", "easyrecipe", "whatieatinaday"],
    "Tech & Programming": ["python", "javascript", "typescript", "programming", "coding", "developer", "code",
                           "api", "linux", "github", "software", "ai", "machinelearning", "react", "rust",
                           "devops", "sql", "tech", "gadget", "iphone", "android", "chatgpt", "llm",
                           "website", "websites", "tools", "apps", "engineer", "engineering", "automation",
                           "nocode", "saas", "cybersecurity", "openai", "anthropic", "claude", "gemini",
                           "browser", "extension", "chrome", "computer", "laptop", "excel", "dataanalytics"],
    "Fitness & Health": ["workout", "gym", "fitness", "exercise", "cardio", "hiit", "protein", "abs", "yoga",
                         "pilates", "stretch", "running", "weightloss", "health", "glutes", "squat", "deadlift"],
    "Travel": ["travel", "itinerary", "flight", "hotel", "vacation", "trip", "traveltips", "wanderlust",
               "backpacking", "destination", "beach", "visit", "hiddengem", "roadtrip"],
    "Finance & Money": ["finance", "money", "invest", "investing", "stocks", "crypto", "bitcoin", "budget",
                        "savings", "personalfinance", "etf", "dividend", "taxes", "debt", "passiveincome"],
    "Education & Learning": ["learn", "learning", "tutorial", "course", "courses", "lesson", "study", "explained",
                             "science", "history", "howto", "education", "facts", "language", "certificate",
                             "certificates", "certification", "skills", "university", "students"],
    "Fashion & Beauty": ["fashion", "outfit", "ootd", "makeup", "skincare", "beauty", "hairstyle", "style",
                         "nails", "grwm", "streetwear"],
    "Home & DIY": ["diy", "homedecor", "decor", "interior", "renovation", "cleaning", "organization", "garden",
                   "gardening", "woodworking", "homehacks", "furniture", "plants"],
    "Entertainment & Memes": ["meme", "memes", "funny", "comedy", "lol", "movie", "series", "netflix", "anime",
                              "gaming", "trailer", "humor", "prank"],
    "News": ["breaking", "news", "election", "politics", "government", "update"],
    "Shopping": ["deal", "deals", "discount", "sale", "amazonfinds", "musthave", "haul", "buy", "coupon"],
    "Music": ["music", "song", "album", "cover", "guitar", "piano", "playlist", "concert", "lyrics", "remix"],
    "Motivation": ["motivation", "mindset", "inspiration", "discipline", "selfimprovement", "quotes", "success",
                   "productivity", "habits"],
    "Business & Career": ["business", "startup", "startups", "career", "careers", "entrepreneur", "marketing",
                          "jobs", "job", "interview", "resume", "sales", "leadership", "linkedin", "sidehustle",
                          "founder", "founders", "freelance", "freelancing", "remote", "hiring", "salary",
                          "agency", "clients"],
}

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class UserRule:
    type: str  # domain|keyword
    pattern: str
    category: str


@dataclass
class CategoryResult:
    category: str
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = "rules"  # rules|llm|fallback
    suggested_new_category: str | None = None
    summary: str | None = None
    recipe: dict | None = None
    llm_used: bool = False


def domain_matches(url: str, pattern: str) -> bool:
    """'github.com' matches the host and subdomains, 'amazon.' any TLD, 'bbc.com/news' a path prefix."""
    rest = url.lower().split("://", 1)[-1].split("?")[0].split("#")[0]
    host, _, path = rest.partition("/")
    host = host.removeprefix("www.").removeprefix("m.")
    pattern = pattern.lower().strip()
    pat_host, _, pat_path = pattern.partition("/")
    if pat_host.endswith("."):
        host_ok = host.startswith(pat_host) or ("." + pat_host) in host
    else:
        host_ok = host == pat_host or host.endswith("." + pat_host)
    return host_ok and (not pat_path or path.startswith(pat_path))


def rule_categorize(url: str, text: str, hashtags: list[str], categories: list[str],
                    user_rules: list[UserRule] = ()) -> CategoryResult | None:
    """Return a result only when one category clearly wins."""
    available = set(categories)

    # User rules beat built-in ones; domain rules are decisive on their own.
    for r in user_rules:
        if r.type == "domain" and r.category in available and domain_matches(url, r.pattern):
            return CategoryResult(r.category, confidence=0.95, source="rules")
    for dom, cat in DOMAIN_RULES.items():
        if cat in available and domain_matches(url, dom):
            return CategoryResult(cat, confidence=0.9, source="rules")

    words = Counter(_WORD_RE.findall(text.lower()))
    tag_set = {h.lower() for h in hashtags}
    scores: Counter[str] = Counter()
    for cat, keywords in KEYWORD_RULES.items():
        if cat not in available:
            continue
        for kw in keywords:
            if kw in tag_set:
                scores[cat] += 2  # hashtags are deliberate signals
            elif words[kw]:
                scores[cat] += min(words[kw], 2)
            if len(kw) >= 4 and any(kw in t and kw != t for t in tag_set):
                scores[cat] += 1  # inside a longer hashtag: #techcareers, #aicertification
    for r in user_rules:
        if r.type == "keyword" and r.category in available:
            kw = r.pattern.lower()
            if kw in tag_set or words[kw] or kw in text.lower():
                scores[r.category] += 3

    if not scores:
        return None
    (top, top_score), *rest = scores.most_common(2) + [("", 0)]
    second = rest[0][1]
    if (top_score >= 3 and top_score >= 2 * second) or (top_score >= 2 and second == 0):
        return CategoryResult(top, confidence=min(0.6 + 0.05 * top_score, 0.85), source="rules")
    return None


# ---------------------------------------------------------------- LLM

_client: anthropic.AsyncAnthropic | None = None


def _llm() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        key = get_settings().anthropic_api_key or None
        _client = anthropic.AsyncAnthropic(api_key=key, max_retries=3, timeout=60.0)
    return _client


def _schema(categories: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": categories},
            "description": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
            "suggested_new_category": {"type": "string"},
            "summary": {"type": "string"},
            "recipe": {
                "type": "object",
                "properties": {
                    "is_recipe": {"type": "boolean"},
                    "servings": {"type": "string"},
                    "time": {"type": "string"},
                    "ingredients": {"type": "array", "items": {"type": "string"}},
                    "steps": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                },
                "required": ["is_recipe", "servings", "time", "ingredients", "steps", "notes"],
                "additionalProperties": False,
            },
        },
        "required": ["category", "description", "tags", "confidence", "suggested_new_category", "summary", "recipe"],
        "additionalProperties": False,
    }


def build_prompt(meta: dict, categories: list[str], examples: list[str]) -> str:
    lang = get_settings().summary_language
    transcript = meta.get("transcript")
    # Haiku's context is large and cheap: give it the whole video, not just the start.
    transcript_block = f"Video transcript:\n{transcript[:60000]}\n" if transcript else ""
    return f"""You sort links a person saved from social media and the web into their personal categories.

Categories: {", ".join(categories)}
Past corrections by this user (follow these preferences): {"; ".join(examples) if examples else "none"}

Link: {meta.get("url")}
Platform: {meta.get("platform")}
Title: {meta.get("title") or ""}
Site / author: {meta.get("site_name") or ""} {meta.get("author") or ""}
Hashtags: {" ".join("#" + h for h in meta.get("hashtags") or [])}
Caption / text:
{(meta.get("raw_text") or meta.get("description") or "")[:20000]}
{transcript_block}
Return:
- category: the best fit from the list. Use "{OTHER}" only if nothing fits.
- description: one plain sentence under 160 characters saying what this link is about, useful when browsing later. Write it in {lang}, translating if the content is in another language.
- tags: up to 4 short lowercase topic tags, in {lang}.
- confidence: 0 to 1, how sure you are about the category.
- suggested_new_category: if none of the categories fit well and a short new category name would (e.g. "Gardening"), give it; otherwise an empty string.
- summary: a detailed summary of the content so the person doesn't have to watch or read it again. Start with one or two sentences on what it is, then 3 to 8 bullet lines ("- ") covering the main points, steps, tips, numbers, names, ingredients or recommendations it actually contains. Use only what's in the content above; don't invent details. Write it in {lang}, translating if the content is in another language. If there is too little content to summarize beyond the description, return an empty string.
- recipe: if this is a recipe (food or drink), set is_recipe to true and write it out in full so the person can cook from it:
  - ingredients: every ingredient, one per item, with the quantity exactly as stated (e.g. "200 g spaghetti", "2 cloves garlic, minced"). If no amount is given, write just the ingredient. Never guess amounts.
  - steps: the whole method in order, one clear action per item, including temperatures, times and visual cues that are mentioned.
  - servings and time: only if stated, otherwise an empty string.
  - notes: tips, substitutions, and anything that is missing (e.g. "The video doesn't give quantities").
  Write the recipe in {lang}. When a dish or ingredient has a local name, add it in brackets if it helps, e.g. "1 cup yogurt (dahi)".
  If it is not a recipe, set is_recipe to false and leave the other recipe fields empty."""


async def llm_categorize(meta: dict, categories: list[str], examples: list[str]) -> CategoryResult | None:
    s = get_settings()
    if not s.llm_enabled:
        return None
    try:
        msg = await _llm().messages.create(
            model=s.llm_model,
            max_tokens=8000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": _schema(categories)}},
            messages=[{"role": "user", "content": build_prompt(meta, categories, examples)}],
        )
    except anthropic.AuthenticationError:
        log.warning("Anthropic API key is invalid; using rules only")
        return None
    except TypeError as e:
        # Raised by the SDK when no credentials are configured at all.
        if "authentication" not in str(e).lower():
            raise
        log.warning("No ANTHROPIC_API_KEY configured; using rules only")
        return None
    except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APIStatusError) as e:
        log.warning("LLM categorization failed: %s", e)
        return None

    if msg.stop_reason != "end_turn":
        log.warning("LLM stopped with %s", msg.stop_reason)
        return None
    text = next((b.text for b in msg.content if b.type == "text"), None)
    try:
        data = json.loads(text or "")
    except json.JSONDecodeError:
        return None

    category = data.get("category")
    confidence = float(data.get("confidence") or 0)
    if category not in categories:
        category, confidence = OTHER, 0.0
    return CategoryResult(
        category=category,
        description=(data.get("description") or "").strip()[:300] or None,
        tags=[t.strip().lower() for t in data.get("tags") or [] if t.strip()][:4],
        confidence=confidence,
        source="llm",
        suggested_new_category=(data.get("suggested_new_category") or "").strip()[:80] or None,
        summary=(data.get("summary") or "").strip()[:6000] or None,
        recipe=_recipe_from_llm(data.get("recipe")),
        llm_used=True,
    )


def _recipe_from_llm(r) -> dict | None:
    if not isinstance(r, dict) or not r.get("is_recipe"):
        return None
    return recipes.normalize(r)


RECIPE_ONLY_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "servings": {"type": "string"},
        "time": {"type": "string"},
        "ingredients": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["found", "servings", "time", "ingredients", "steps", "notes"],
    "additionalProperties": False,
}


async def llm_recipe(meta: dict) -> dict | None:
    """The person filed this under recipes: write the recipe out from whatever the post contains."""
    s = get_settings()
    if not s.llm_ready():
        return None
    transcript = meta.get("transcript")
    prompt = f"""The person saved this post and filed it under recipes. Write out the recipe so they can cook from it.

Link: {meta.get("url")}
Title: {meta.get("title") or ""}
Author: {meta.get("author") or ""}
Caption / text:
{(meta.get("raw_text") or meta.get("description") or "")[:20000]}
{"Video transcript:" + chr(10) + transcript[:60000] if transcript else ""}

Return:
- found: true if the content contains a recipe or enough of one to cook from (ingredients and/or method); false if there is really nothing to go on.
- ingredients: every ingredient, one per item, with the quantity exactly as stated. If an amount isn't given, write just the ingredient. Never guess amounts.
- steps: the whole method in order, one clear action per item, with any temperatures, times and cues mentioned.
- servings, time: only if stated, otherwise an empty string.
- notes: tips, substitutions, and what is missing (e.g. "Quantities aren't given in the video").
Write everything in {s.summary_language}; when a dish or ingredient has a local name, you may add it in brackets."""
    try:
        msg = await _llm().messages.create(
            model=s.llm_model,
            max_tokens=8000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": RECIPE_ONLY_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        log.warning("recipe extraction failed: %s", e)
        return None
    except TypeError as e:  # no credentials configured
        if "authentication" not in str(e).lower():
            raise
        return None
    if msg.stop_reason != "end_turn":
        return None
    try:
        data = json.loads(next((b.text for b in msg.content if b.type == "text"), ""))
    except json.JSONDecodeError:
        return None
    return recipes.normalize(data) if data.get("found") else None


async def categorize(meta: dict, categories: list[str], examples: list[str],
                     user_rules: list[UserRule] = ()) -> CategoryResult:
    """Rules first; the LLM handles unclear cases and writes the description."""
    s = get_settings()
    text = "\n".join(filter(None, [meta.get("title"), meta.get("description"), meta.get("raw_text"),
                                    (meta.get("transcript") or "")[:4000]]))
    by_rules = rule_categorize(meta.get("url", ""), text, meta.get("hashtags") or [], categories, user_rules)

    by_llm = await llm_categorize(meta, categories, examples)
    if by_llm:
        if by_rules and by_rules.confidence >= 0.9:
            # A domain rule is authoritative; keep the LLM's description and tags.
            by_llm.category, by_llm.confidence, by_llm.source = by_rules.category, by_rules.confidence, "rules"
        elif by_llm.confidence < s.llm_min_confidence:
            by_llm.category = by_rules.category if by_rules else OTHER
        return by_llm
    if by_rules:
        return by_rules
    return CategoryResult(OTHER, confidence=0.0, source="fallback")
