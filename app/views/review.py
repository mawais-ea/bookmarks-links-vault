"""Review inbox: low-confidence items waiting for a one-tap category confirmation."""

from typing import TYPE_CHECKING

import flet as ft

from .common import bookmark_card, category_icon, empty_state, show_move_dialog

if TYPE_CHECKING:
    from main import LinkVaultApp


def review_item(app: "LinkVaultApp", bm: dict, cats: list[dict]) -> ft.Control:
    actions: list[ft.Control] = []
    if bm.get("suggested_category"):
        actions.append(ft.Chip(
            label=ft.Text(f"Create “{bm['suggested_category']}”"), leading=ft.Icon(ft.Icons.ADD, size=16),
            on_click=lambda: app.run(app.update_bookmark(bm["id"], category=bm["suggested_category"])),
        ))
    current = next((c for c in cats if c["id"] == bm.get("category_id")), None)
    if current:
        actions.append(ft.Chip(
            label=ft.Text(f"Keep in {current['name']}"), leading=ft.Icon(ft.Icons.CHECK, size=16),
            on_click=lambda: app.run(app.update_bookmark(bm["id"], needs_review=False)),
        ))
    for c in cats[:6]:
        if current and c["id"] == current["id"]:
            continue
        actions.append(ft.Chip(
            label=ft.Text(c["name"]), leading=ft.Icon(category_icon(c["icon"]), size=16, color=c["color"]),
            on_click=lambda e, c=c: app.run(app.update_bookmark(bm["id"], category_id=c["id"])),
        ))
    actions.append(ft.Chip(label=ft.Text("Other…"), on_click=lambda: show_move_dialog(app, bm)))
    return ft.Column([bookmark_card(app, bm), ft.Row(actions, wrap=True, spacing=6)], spacing=4)


def build(app: "LinkVaultApp") -> ft.View:
    items = app.db.bookmarks(review=True)
    cats = app.db.categories()
    # Most used categories first: they're the likeliest one-tap answers.
    cats_by_use = sorted(cats, key=lambda c: -c["count"])
    body = [review_item(app, b, cats_by_use) for b in items] or [
        empty_state(ft.Icons.TASK_ALT, "All caught up. Links LinkVault isn't sure about will show up here.")]
    return ft.View(
        route="/review",
        appbar=ft.AppBar(title=ft.Text("Review")),
        navigation_bar=app.nav_bar(2),
        scroll=ft.ScrollMode.ALWAYS,
        padding=ft.Padding.only(left=12, top=12, right=22, bottom=12),  # room for the scrollbar
        controls=body,
    )
