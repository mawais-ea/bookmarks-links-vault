from typing import TYPE_CHECKING

import flet as ft

from .common import PLATFORM_NAMES, empty_state, nice_date, platform_icon, resolve, status_badge

if TYPE_CHECKING:
    from main import LinkVaultApp

RECIPE_SOURCES = {
    "site": "From the recipe website",
    "ai": "Written out by Claude from the post",
    "quick": "Read from the caption",
}


def servings_label(servings) -> str | None:
    """'4' -> 'Serves 4'; '4 servings' / 'Makes 12' stay as they are."""
    s = str(servings or "").strip()
    return f"Serves {s}" if s.replace("-", "").isdigit() else (s or None)


def recipe_text(recipe: dict) -> str:
    lines = [recipe.get("title") or "Recipe"]
    meta = " · ".join(filter(None, [servings_label(recipe.get("servings")), recipe.get("time")]))
    if meta:
        lines.append(meta)
    lines += ["", "Ingredients"] + [f"- {i}" for i in recipe.get("ingredients") or []]
    lines += ["", "Method"] + [f"{n}. {s}" for n, s in enumerate(recipe.get("steps") or [], 1)]
    if recipe.get("notes"):
        lines += ["", f"Notes: {recipe['notes']}"]
    return "\n".join(lines)


def ingredient_row(text: str) -> ft.Control:
    """Tick-off ingredient. The checkbox's own label can't wrap, so the text sits beside it
    and wraps onto as many lines as it needs. Tapping the text ticks it too."""
    label = ft.Text(text, expand=True, selectable=False)
    box = ft.Checkbox(value=False)

    def apply():
        label.style = ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH) if box.value else None
        label.color = ft.Colors.ON_SURFACE_VARIANT if box.value else None

    def toggle_from_text():
        box.value = not box.value
        apply()
        row.update()

    def on_box(e):
        apply()
        row.update()

    box.on_change = on_box
    row = ft.Row(
        [box, ft.Container(label, expand=True, on_click=toggle_from_text, padding=ft.Padding.only(top=10, bottom=4))],
        vertical_alignment=ft.CrossAxisAlignment.START, spacing=4,
    )
    return row


def is_recipe_category(app: "LinkVaultApp", bm: dict) -> bool:
    cat = app.db.category(bm["category_id"]) if bm.get("category_id") else None
    return bool(cat) and ("recipe" in cat["name"].lower() or cat["icon"] in ("restaurant", "restaurant_menu"))


def recipe_status_box(app: "LinkVaultApp", bm: dict) -> ft.Control | None:
    """Shown instead of a recipe while it's being fetched, or when none was found."""
    muted = ft.Colors.ON_SURFACE_VARIANT
    source = bm.get("recipe_source")
    in_recipes = is_recipe_category(app, bm)
    if source == "processing":
        body = ft.Row([ft.ProgressRing(width=16, height=16, stroke_width=2),
                       ft.Text("Getting the recipe…", color=muted, expand=True)], spacing=10)
    elif source == "none":
        body = ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=18, color=muted),
                       ft.Text("No recipe found in this post. Without a Claude API key only recipes written "
                               "in the caption or on a recipe website can be read.", color=muted, expand=True),
                       ft.TextButton("Try again", on_click=lambda: app.run(app.find_recipe(bm["id"])))],
                      vertical_alignment=ft.CrossAxisAlignment.START, spacing=8)
    elif in_recipes and bm["platform"] != "screenshot" and bm["status"] != "processing":
        body = ft.Row([ft.Icon(ft.Icons.RESTAURANT_MENU, size=18, color=muted),
                       ft.Text("This is in your recipes but has no recipe yet.", color=muted, expand=True),
                       ft.FilledButton("Get recipe", on_click=lambda: app.run(app.find_recipe(bm["id"])))],
                      spacing=8)
    else:
        return None
    return ft.Container(padding=14, border_radius=12, bgcolor=ft.Colors.SURFACE_CONTAINER_LOW, content=body)


def recipe_section(app: "LinkVaultApp", bm: dict) -> ft.Control | None:
    """Ingredients as a tick-off list and the full method as numbered steps."""
    recipe = bm.get("recipe")
    if not recipe:
        return recipe_status_box(app, bm)
    muted = ft.Colors.ON_SURFACE_VARIANT
    header = ft.Row([
        ft.Icon(ft.Icons.RESTAURANT_MENU, size=20),
        ft.Text("Recipe", weight=ft.FontWeight.BOLD, size=16),
        ft.Text(RECIPE_SOURCES.get(bm.get("recipe_source"), ""), size=12, color=muted, expand=True),
        ft.IconButton(ft.Icons.CONTENT_COPY, tooltip="Copy recipe",
                      on_click=lambda: app.run(app.copy(recipe_text(recipe), "Recipe copied"))),
    ], spacing=8)
    parts: list[ft.Control] = [header]

    facts = [(ft.Icons.PEOPLE_OUTLINE, servings_label(recipe.get("servings"))),
             (ft.Icons.TIMER_OUTLINED, recipe.get("time"))]
    chips = [ft.Chip(label=ft.Text(text), leading=ft.Icon(icon, size=16)) for icon, text in facts if text]
    if chips:
        parts.append(ft.Row(chips, wrap=True, spacing=6))

    ingredients = recipe.get("ingredients") or []
    if ingredients:
        parts.append(ft.Text(f"Ingredients ({len(ingredients)})", weight=ft.FontWeight.W_600, size=15))
        parts += [ingredient_row(i) for i in ingredients]

    steps = recipe.get("steps") or []
    if steps:
        parts.append(ft.Text("Method", weight=ft.FontWeight.W_600, size=15))
        for n, step in enumerate(steps, 1):
            parts.append(ft.Row([
                ft.CircleAvatar(content=ft.Text(str(n), size=12), radius=12),
                ft.Text(step, selectable=True, expand=True),
            ], vertical_alignment=ft.CrossAxisAlignment.START, spacing=10))

    if recipe.get("notes"):
        parts.append(ft.Row([ft.Icon(ft.Icons.LIGHTBULB_OUTLINE, size=18, color=muted),
                             ft.Text(recipe["notes"], color=muted, expand=True, selectable=True)],
                            vertical_alignment=ft.CrossAxisAlignment.START, spacing=8))
    return ft.Container(padding=14, border_radius=12, bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                        content=ft.Column(parts, spacing=8))


def screenshot_text_section(app: "LinkVaultApp", bm: dict) -> ft.Control | None:
    text = bm.get("raw_text")
    if bm["platform"] != "screenshot" or not text:
        return None
    return ft.Container(
        padding=14, border_radius=12, bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        content=ft.Column([
            ft.Row([ft.Icon(ft.Icons.TEXT_SNIPPET, size=20),
                    ft.Text("Text in screenshot", weight=ft.FontWeight.BOLD, size=16, expand=True),
                    ft.IconButton(ft.Icons.CONTENT_COPY, tooltip="Copy text",
                                  on_click=lambda: app.run(app.copy(text, "Text copied")))], spacing=8),
            ft.Text(text, selectable=True, size=14),
        ], spacing=8),
    )


def summary_section(app: "LinkVaultApp", bm: dict) -> ft.Control | None:
    """Summary of the video / article under the description."""
    summary = bm.get("summary")
    if bm["status"] == "processing" or bm.get("transcript_status") == "processing":
        body: ft.Control = ft.Row([ft.ProgressRing(width=16, height=16, stroke_width=2),
                                   ft.Text("Writing the summary…", color=ft.Colors.ON_SURFACE_VARIANT)], spacing=10)
        label = ""
    elif summary:
        body = ft.Markdown(summary, selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB)
        label = "Quick summary · add a Claude API key for better ones" if bm.get("summary_source") == "quick" else ""
    else:
        return None
    header: list[ft.Control] = [
        ft.Icon(ft.Icons.AUTO_AWESOME, size=20),
        ft.Text("Summary", weight=ft.FontWeight.BOLD, size=16),
        ft.Text(label, size=12, color=ft.Colors.ON_SURFACE_VARIANT, expand=True),
    ]
    if summary:
        header.append(ft.IconButton(ft.Icons.CONTENT_COPY, tooltip="Copy summary",
                                    on_click=lambda: app.run(app.copy(summary, "Summary copied"))))
    return ft.Container(
        padding=14, border_radius=12, bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        content=ft.Column([ft.Row(header, spacing=8), body], spacing=8),
    )


def build(app: "LinkVaultApp", route: str) -> ft.View:
    bm_id = int(route.rsplit("/", 1)[-1])
    bm = app.db.bookmark(bm_id)
    if not bm:
        return ft.View(route=route, appbar=ft.AppBar(title=ft.Text("Bookmark")),
                       controls=[empty_state(ft.Icons.LINK_OFF, "This bookmark was deleted.")])

    # Full width, and no max_lines: the boxes grow so the whole text is always visible.
    title = ft.TextField(label="Title", value=bm.get("title") or "", multiline=True, max_lines=None)
    description = ft.TextField(label="Description", value=bm.get("description") or "", multiline=True,
                               min_lines=2, max_lines=None)
    note = ft.TextField(label="Your note", value=bm.get("note") or "", multiline=True, min_lines=2, max_lines=None)
    category = ft.Dropdown(
        label="Category", value=str(bm["category_id"]) if bm.get("category_id") else None, expand=True,
        options=[ft.DropdownOption(key=str(c["id"]), text=c["name"]) for c in app.db.categories()],
    )

    async def save(_=None):
        changes = {}
        for key, field in (("title", title), ("description", description), ("note", note)):
            if (field.value or "") != (bm.get(key) or ""):
                changes[key] = field.value
        if category.value and int(category.value) != bm.get("category_id"):
            changes["category_id"] = int(category.value)
        if changes:
            await app.update_bookmark(bm_id, **changes)
            app.toast("Saved")

    header: list[ft.Control] = []
    is_shot = bm["platform"] == "screenshot"
    if is_shot:
        # The whole screenshot, not a cropped banner; tap "Open image" for full size.
        header.append(ft.Image(src=resolve(bm["thumbnail_url"]), fit=ft.BoxFit.CONTAIN, border_radius=12,
                               height=480, error_content=empty_state(ft.Icons.BROKEN_IMAGE, "Image unavailable offline")))
    elif bm.get("thumbnail_url"):
        header.append(ft.Image(src=resolve(bm["thumbnail_url"]), height=220, fit=ft.BoxFit.COVER, border_radius=12,
                               error_content=ft.Container(height=0)))
    meta = ft.Row([
        ft.Icon(platform_icon(bm["platform"]), size=16),
        ft.Text(PLATFORM_NAMES.get(bm["platform"], bm["platform"])),
        ft.Text("·"), ft.Text(bm.get("author") or bm.get("site_name") or ""),
        ft.Text("·"), ft.Text(f"Saved {nice_date(bm.get('created_at')).lower()}"),
    ], spacing=6, wrap=True)
    header.append(meta)
    if badge := status_badge(bm):
        header.append(badge)
    if bm.get("tags"):
        header.append(ft.Row([ft.Chip(label=ft.Text(f"#{t}"), leading=None) for t in bm["tags"]], wrap=True))

    is_fav = bm.get("is_favorite")
    is_archived = bm.get("is_archived")
    if is_shot:
        actions = ft.Row([
            ft.FilledButton("Open image", icon=ft.Icons.OPEN_IN_NEW, on_click=lambda: app.open_url(resolve(bm["url"]))),
        ], wrap=True)
    else:
        actions = ft.Row([
            ft.FilledButton("Open", icon=ft.Icons.OPEN_IN_NEW, on_click=lambda: app.open_url(bm["url"])),
            ft.OutlinedButton("Copy link", icon=ft.Icons.CONTENT_COPY, on_click=lambda: app.run(app.copy(bm["url"]))),
            ft.OutlinedButton("Share", icon=ft.Icons.SHARE, on_click=lambda: app.run(app.share(bm))),
        ], wrap=True)

    return ft.View(
        route=route,
        appbar=ft.AppBar(
            title=ft.Text("Bookmark"),
            actions=[
                ft.IconButton(ft.Icons.STAR if is_fav else ft.Icons.STAR_BORDER, tooltip="Favorite",
                              icon_color=ft.Colors.AMBER if is_fav else None,
                              on_click=lambda: app.run(app.update_bookmark(bm_id, is_favorite=not is_fav))),
                ft.IconButton(ft.Icons.UNARCHIVE if is_archived else ft.Icons.ARCHIVE,
                              tooltip="Unarchive" if is_archived else "Archive",
                              on_click=lambda: app.run(app.update_bookmark(bm_id, is_archived=not is_archived))),
                ft.IconButton(ft.Icons.REFRESH, tooltip="Re-read link & re-categorize",
                              on_click=lambda: app.run(app.refresh_bookmark(bm_id))),
                ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="Delete",
                              on_click=lambda: app.run(app.delete_bookmark(bm_id, go_back=True))),
            ],
        ),
        scroll=ft.ScrollMode.ALWAYS,
        padding=ft.Padding.only(left=16, top=16, right=26, bottom=16),  # room for the scrollbar
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,  # fields and sections use the full width
        controls=[
            *header,
            actions,
            *([] if is_shot else [ft.Text(bm["url"], size=12, selectable=True, color=ft.Colors.ON_SURFACE_VARIANT)]),
            title,
            description,
            *([rc] if (rc := recipe_section(app, bm)) else []),
            *([sm] if (sm := summary_section(app, bm)) else []),
            *([tx] if (tx := screenshot_text_section(app, bm)) else []),
            ft.Row([category]),
            note,
            ft.Row([ft.FilledButton("Save changes", icon=ft.Icons.CHECK, on_click=save)],
                   alignment=ft.MainAxisAlignment.END),
        ],
    )
