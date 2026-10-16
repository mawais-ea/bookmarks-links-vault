"""Recipe extraction: ingredients + full method.

Sources, best first:
1. schema.org Recipe JSON-LD that recipe websites embed (exact, no AI needed).
2. Claude, from the caption / video captions (see categorizer.py).
3. A text parser for captions written as "Ingredients: ... Method: ..." (no API key needed).

A recipe is a dict: {title, servings, time, ingredients: [str], steps: [str], notes}.
"""

import html
import json
import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser

MAX_ITEMS = 80

# ---------------------------------------------------------------- helpers


def _clean(s) -> str:
    if not isinstance(s, str):
        return ""
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return " ".join(s.split())


def iso_duration(value) -> str | None:
    """PT1H30M -> '1 h 30 min'."""
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?)?", value.strip())
    if not m or not any(m.groups()):
        return None
    days, hours, minutes = (int(g or 0) for g in m.groups())
    hours += days * 24
    hours, minutes = hours + minutes // 60, minutes % 60
    parts = ([f"{hours} h"] if hours else []) + ([f"{minutes} min"] if minutes else [])
    return " ".join(parts) or None


def normalize(recipe: dict | None) -> dict | None:
    """Clean a recipe from any source; None unless it has ingredients or steps."""
    if not isinstance(recipe, dict):
        return None
    ingredients = [_clean(i) for i in recipe.get("ingredients") or [] if _clean(i)][:MAX_ITEMS]
    steps = [_clean(s) for s in recipe.get("steps") or [] if _clean(s)][:MAX_ITEMS]
    if len(ingredients) < 2 and len(steps) < 2:
        return None
    return {
        "title": _clean(recipe.get("title")) or None,
        "servings": _clean(str(recipe.get("servings") or "")) or None,
        "time": _clean(recipe.get("time")) or None,
        "ingredients": ingredients,
        "steps": steps,
        "notes": _clean(recipe.get("notes")) or None,
    }


def servings_label(servings) -> str | None:
    """'4' -> 'Serves 4'; '4 servings' / 'Makes 12' stay as they are."""
    s = str(servings or "").strip()
    return f"Serves {s}" if s.replace("-", "").isdigit() else (s or None)


def as_text(recipe: dict) -> str:
    """Plain text for copying/sharing."""
    lines = [recipe.get("title") or "Recipe"]
    meta = " · ".join(filter(None, [servings_label(recipe.get("servings")), recipe.get("time")]))
    if meta:
        lines.append(meta)
    lines += ["", "Ingredients"] + [f"- {i}" for i in recipe["ingredients"]]
    lines += ["", "Method"] + [f"{n}. {s}" for n, s in enumerate(recipe["steps"], 1)]
    if recipe.get("notes"):
        lines += ["", f"Notes: {recipe['notes']}"]
    return "\n".join(lines)


# ---------------------------------------------------------------- 1. JSON-LD


def _iter_nodes(data):
    if isinstance(data, list):
        for item in data:
            yield from _iter_nodes(item)
    elif isinstance(data, dict):
        yield data
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in data:
                yield from _iter_nodes(data[key])


def _is_recipe(node: dict) -> bool:
    t = node.get("@type")
    return t == "Recipe" or (isinstance(t, list) and "Recipe" in t)


def _steps(instructions) -> list[str]:
    if isinstance(instructions, str):
        parts = re.split(r"\n+|(?<=\.)\s+(?=\d+[.)]\s)", instructions)
        return [re.sub(r"^\s*\d+[.)]\s*", "", p) for p in parts if p.strip()]
    out: list[str] = []
    for item in instructions or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            if item.get("@type") == "HowToSection" or "itemListElement" in item:
                sub = _steps(item.get("itemListElement"))
                if sub and item.get("name"):
                    sub[0] = f"{_clean(item['name'])}: {sub[0]}"
                out += sub
            else:
                out.append(item.get("text") or item.get("name") or "")
    return out


def parse_jsonld_recipe(page_html: str) -> dict | None:
    tree = HTMLParser(page_html)
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text(deep=True) or "")
        except ValueError:
            continue
        for node in _iter_nodes(data):
            if not _is_recipe(node):
                continue
            servings = node.get("recipeYield")
            if isinstance(servings, list):
                servings = next((s for s in servings if isinstance(s, str) and not s.isdigit()), servings[0] if servings else None)
            recipe = normalize({
                "title": node.get("name"),
                "servings": servings,
                "time": iso_duration(node.get("totalTime")) or iso_duration(node.get("cookTime")),
                "ingredients": node.get("recipeIngredient") or node.get("ingredients"),
                "steps": _steps(node.get("recipeInstructions")),
            })
            if recipe:
                return recipe
    return None


# ---------------------------------------------------------------- 3. caption parser

_ING_HEADER = re.compile(r"^\W*(ingredients?|ingredienti|ingredientes|you['’]?ll need|what you need|shopping list)\b",
                         re.I)
_STEP_HEADER = re.compile(r"^\W*(instructions?|method|directions?|steps?|how to make( it)?|preparation|recipe)\b\W*$"
                          r"|^\W*(instructions?|method|directions?|steps?|how to make( it)?|preparation)\b", re.I)
_QTY = re.compile(r"^\W*(\d+([./]\d+)?|[½¼¾⅓⅔⅛]|a |an |one |two |three |half )\s*"
                  r"(g|gr|grams?|kg|ml|l|cups?|tbsp|tsp|tablespoons?|teaspoons?|oz|lbs?|pounds?|cloves?|pinch|"
                  r"handful|slices?|cans?|pieces?|large|medium|small|x\b)?", re.I)
# Without an "Ingredients" heading a line only counts if it has a real kitchen measurement,
# so numbered lists ("1️⃣ Betalist: ...") aren't mistaken for ingredients.
_QTY_STRICT = re.compile(r"^\W*(\d+([./,]\d+)?|[½¼¾⅓⅔⅛]|a|an|one|two|three|half)\s*"
                         r"(g|gr|grams?|kg|ml|l|litres?|liters?|cups?|tbsp|tsp|tablespoons?|teaspoons?|oz|lbs?|"
                         r"pounds?|cloves?|pinch(es)?|handfuls?|slices?|cans?|sticks?|bunch(es)?)\b", re.I)
_BULLET = re.compile(r"^[\s\-–•*▪◦·✅✔️🔸🔹➡️👉]*(\d+[.)]\s*)?")
_HASHTAGS = re.compile(r"^(#\w+\s*)+$")


_LEADING_SYMBOLS = re.compile(r"^[^\w½¼¾⅓⅔⅛(]+")  # bullets, emoji (🧄 🍗 ✅), dashes before the text


def _strip_bullet(line: str) -> str:
    line = _LEADING_SYMBOLS.sub("", line)
    return _BULLET.sub("", line).strip(" :-")


_NUMBERED = re.compile(r"^\s*(\d+[.)]|\d️?⃣|step \d+:?)\s*", re.I)  # "1.", "1)", "1️⃣", "Step 1"
_BULLETED = re.compile(r"^\s*[-–•*▪◦·✅✔🔸🔹➡👉🧄🧅🍅🥚🧈🌶🥛🧂🍋🥔🍗]")


def recipe_from_text(text: str | None, assume_recipe: bool = False) -> dict | None:
    """Read a recipe written in a caption.

    `assume_recipe`: the person filed this under recipes themselves, so be less strict:
    bullet lines count as ingredients and numbered lines as steps even without headings.
    """
    if not text:
        return None
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    ingredients: list[str] = []
    steps: list[str] = []
    mode = None
    for line in lines:
        if not line or _HASHTAGS.match(line):
            continue
        if assume_recipe and mode is None and not _ING_HEADER.match(line) and not _STEP_HEADER.match(line):
            if _NUMBERED.match(line):
                steps.append(_NUMBERED.sub("", line).strip(" :-"))
                continue
            if _BULLETED.match(line) or (_QTY.match(line) and _QTY.match(line).group(0).strip() and len(line) < 80):
                ingredients.append(_strip_bullet(line))
                continue
        if _ING_HEADER.match(line):
            mode = "ing"
            rest = _ING_HEADER.sub("", line).strip(" :-")
            if rest:  # "Ingredients: 2 eggs, 100g flour, milk"
                ingredients += [p.strip() for p in rest.split(",") if p.strip()]
            continue
        if _STEP_HEADER.match(line):
            mode = "steps"
            rest = _STEP_HEADER.sub("", line).strip(" :-")
            if rest:
                steps.append(rest)
            continue
        if mode == "ing":
            ingredients.append(_strip_bullet(line))
        elif mode == "steps":
            steps.append(_strip_bullet(line))
        elif _QTY_STRICT.match(line) and len(line) < 80:
            ingredients.append(_strip_bullet(line))  # quantity lines without an "Ingredients" header
        elif re.match(r"^\s*(\d+[.)]|step \d+)", line, re.I):
            steps.append(re.sub(r"^\s*(\d+[.)]|step \d+:?)\s*", "", line, flags=re.I))
    if assume_recipe:
        # Decorated titles ("• KIP SAMBA BROODJES •") also look like bullets, so ask for a real list.
        if len(ingredients) < 3 and len(steps) < 2:
            return None
        return normalize({"ingredients": ingredients, "steps": steps})
    if len(ingredients) < 3:
        return None
    return normalize({"ingredients": ingredients, "steps": steps})
