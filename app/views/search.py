from typing import TYPE_CHECKING

import flet as ft

from .common import PLATFORM_NAMES, bookmark_card, empty_state, platform_icon

if TYPE_CHECKING:
    from main import LinkVaultApp


def build(app: "LinkVaultApp") -> ft.View:
    state = {"q": app.search_state.get("q", ""), "platform": app.search_state.get("platform"),
             "category": app.search_state.get("category")}
    results = ft.Column(spacing=4)

    def run_search():
        app.search_state.update(state)
        has_filter = state["q"].strip() or state["platform"] or state["category"]
        items = app.db.bookmarks(
            q=state["q"], platform=state["platform"],
            category_id=int(state["category"]) if state["category"] else None, limit=200,
        ) if has_filter else []
        if has_filter and not items:
            results.controls = [empty_state(ft.Icons.SEARCH_OFF, "No matches.")]
        elif not has_filter:
            results.controls = [empty_state(ft.Icons.SEARCH, "Search titles, descriptions, notes and tags.")]
        else:
            results.controls = [ft.Text(f"{len(items)} result{'s' * (len(items) != 1)}", size=12)] + \
                               [bookmark_card(app, b) for b in items]

    def on_change(e):
        state["q"] = e.control.value or ""
        run_search()
        results.update()

    def platform_chip(p: str) -> ft.Chip:
        def toggle(e):
            state["platform"] = p if e.control.selected else None
            for c in chips.controls:
                c.selected = c.data == state["platform"]
            run_search()
            chips.update()
            results.update()
        chip = ft.Chip(label=ft.Text(PLATFORM_NAMES.get(p, p)), leading=ft.Icon(platform_icon(p), size=16),
                       selected=state["platform"] == p, data=p, on_select=toggle)
        return chip

    chips = ft.Row([platform_chip(p) for p in app.db.platforms()], scroll=ft.ScrollMode.ALWAYS,
                   wrap=False, height=58, vertical_alignment=ft.CrossAxisAlignment.START)  # scrollbar below chips

    def on_category(e):
        state["category"] = e.control.value or None
        run_search()
        results.update()

    category_dd = ft.Dropdown(
        label="Category", value=state["category"], dense=True, width=260,
        options=[ft.DropdownOption(key="", text="All categories")]
        + [ft.DropdownOption(key=str(c["id"]), text=c["name"]) for c in app.db.categories()],
        on_select=on_category,
    )

    run_search()
    return ft.View(
        route="/search",
        appbar=ft.AppBar(title=ft.Text("Search")),
        navigation_bar=app.nav_bar(1),
        scroll=ft.ScrollMode.ALWAYS,
        padding=ft.Padding.only(left=12, top=12, right=22, bottom=12),  # room for the scrollbar
        controls=[
            ft.TextField(value=state["q"], hint_text="Search everything", prefix_icon=ft.Icons.SEARCH,
                         autofocus=True, on_change=on_change,
                         border=ft.OutlineInputBorder(border_radius=24)),
            chips,
            category_dd,
            results,
        ],
    )
