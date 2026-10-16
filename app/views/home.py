from typing import TYPE_CHECKING

import flet as ft

from .common import bookmark_card, category_icon, empty_state, mini_card
from .settings import category_dialog

if TYPE_CHECKING:
    from main import LinkVaultApp


def paste_box(app: "LinkVaultApp") -> ft.Control:
    field = ft.TextField(hint_text="Paste a link to save", expand=True, dense=True,
                         prefix_icon=ft.Icons.ADD_LINK, border=ft.OutlineInputBorder(border_radius=24))

    async def save(_=None):
        url = (field.value or "").strip()
        if not url:
            return
        field.value = ""
        field.update()
        await app.save_link(url)

    field.on_submit = save
    return ft.Row([
        field,
        ft.IconButton(ft.Icons.SAVE, tooltip="Save link", on_click=save),
        ft.IconButton(ft.Icons.ADD_PHOTO_ALTERNATE, tooltip="Add screenshot or image",
                      on_click=lambda: app.run(app.pick_screenshots())),
    ])


TILE_COLS = {"xs": 6, "sm": 4, "md": 3, "lg": 2}


def category_tile(app: "LinkVaultApp", c: dict, width: float | None = None) -> ft.Container:
    return ft.Container(
        width=width,
        padding=14, border_radius=16, ink=True,
        bgcolor=ft.Colors.with_opacity(0.14, c["color"]),
        border=ft.Border.all(2, ft.Colors.TRANSPARENT),
        on_click=lambda: app.go(f"/category/{c['id']}"),
        content=ft.Column([
            ft.Icon(category_icon(c["icon"]), color=c["color"], size=28),
            ft.Text(c["name"], weight=ft.FontWeight.W_600, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            ft.Text(f"{c['count']} saved", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
        ], spacing=4),
    )


def draggable_category_tile(app: "LinkVaultApp", c: dict) -> ft.Control:
    """Drag a tile onto another tile to take its place."""
    tile = category_tile(app, c)

    def highlight(on: bool):
        tile.border = ft.Border.all(2, ft.Colors.PRIMARY if on else ft.Colors.TRANSPARENT)
        tile.update()

    def will_accept(e):
        highlight(bool(e.accept))

    def accept(e):
        highlight(False)
        source = e.src if getattr(e, "src", None) is not None else app.page.get_control(e.src_id)
        app.move_category(int(source.data), c["id"])

    return ft.DragTarget(
        group="category", col=TILE_COLS, data=c["id"],
        on_will_accept=will_accept, on_leave=lambda e: highlight(False), on_accept=accept,
        content=ft.Draggable(
            group="category", data=c["id"], content=tile,
            content_when_dragging=ft.Container(height=96, border_radius=16,
                                               border=ft.Border.all(2, ft.Colors.OUTLINE_VARIANT)),
            content_feedback=ft.Container(category_tile(app, c, width=170), opacity=0.9),
        ),
    )


def new_category_tile(app: "LinkVaultApp") -> ft.Control:
    return ft.Container(
        col=TILE_COLS,
        padding=14, border_radius=16, ink=True,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        on_click=lambda: category_dialog(app),
        content=ft.Column([
            ft.Icon(ft.Icons.CREATE_NEW_FOLDER, color=ft.Colors.PRIMARY, size=28),
            ft.Text("New category", weight=ft.FontWeight.W_600, color=ft.Colors.PRIMARY),
            ft.Text("Add your own", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
        ], spacing=4),
    )


def section_header(title: str, *actions: ft.Control) -> ft.Control:
    return ft.Row([ft.Text(title, size=18, weight=ft.FontWeight.BOLD, expand=True), *actions],
                  vertical_alignment=ft.CrossAxisAlignment.CENTER)


def view_switch(app: "LinkVaultApp", route: str) -> ft.Control:
    """Thumbnails / list toggle for one screen; Recent and every category remember their own."""
    mode = app.view_for(route)

    def button(icon, value, tip):
        on = mode == value
        return ft.IconButton(icon, tooltip=tip, on_click=lambda: app.set_view(route, value),
                             icon_color=ft.Colors.PRIMARY if on else ft.Colors.ON_SURFACE_VARIANT,
                             bgcolor=ft.Colors.with_opacity(0.12, ft.Colors.PRIMARY) if on else None)

    return ft.Row([button(ft.Icons.GRID_VIEW, "thumbs", "Thumbnail view"),
                   button(ft.Icons.VIEW_LIST, "list", "List view")], spacing=0)


def thumb_grid(app: "LinkVaultApp", items: list[dict]) -> ft.Control:
    """Thumbnail grid for a category; drop a card onto another to move it there."""
    def cell(b: dict) -> ft.Control:
        tile = mini_card(app, b, grid=True)

        def lit(on: bool):
            tile.border = ft.Border.all(2, ft.Colors.PRIMARY if on else ft.Colors.TRANSPARENT)
            tile.update()

        def accept(e):
            lit(False)
            source = e.src if getattr(e, "src", None) is not None else app.page.get_control(e.src_id)
            app.move_bookmark_onto(items, int(source.data), b["id"])

        return ft.DragTarget(
            group="bookmark", col={"xs": 6, "sm": 4, "md": 3, "lg": 2}, data=b["id"],
            on_will_accept=lambda e: lit(bool(e.accept)), on_leave=lambda e: lit(False), on_accept=accept,
            content=ft.Draggable(
                group="bookmark", data=b["id"], content=tile, affinity=app.drag_affinity,
                content_when_dragging=ft.Container(height=180, border_radius=12,
                                                   border=ft.Border.all(2, ft.Colors.OUTLINE_VARIANT)),
                content_feedback=ft.Container(mini_card(app, b, grid=True, width=160), opacity=0.9)),
        )

    return ft.ResponsiveRow([cell(b) for b in items], spacing=12, run_spacing=12)


def reorderable(app: "LinkVaultApp", items: list[dict], make, **kwargs) -> ft.ReorderableListView:
    """A list whose cards can be dragged into a new order (drag handle on PC, long-press on phones)."""
    def on_reorder(e: ft.OnReorderEvent):
        app.move_bookmark(items, e.old_index, e.new_index)

    controls = [make(b) for b in items]
    for b, c in zip(items, controls):
        c.key = f"bm-{b['id']}"
    return ft.ReorderableListView(controls=controls, on_reorder=on_reorder, scroll=ft.ScrollMode.ALWAYS, **kwargs)


def build(app: "LinkVaultApp") -> ft.View:
    recent = app.db.bookmarks(limit=12)
    cats = app.db.categories()  # in the order you arranged them
    list_mode = app.view_for("/") == "list"

    top: list[ft.Control] = [paste_box(app)]
    middle: ft.Control | None = None
    if not recent:
        top.append(empty_state(
            ft.Icons.BOOKMARKS,
            "Nothing saved yet.\nShare a link to LinkVault from any app, or paste one above.",
        ))
    else:
        top.append(section_header("Recent", view_switch(app, "/")))
        if not list_mode:
            middle = reorderable(app, recent, lambda b: ft.Container(mini_card(app, b), margin=ft.Margin.only(right=12)),
                                 horizontal=True, height=240)

    bottom: list[ft.Control] = [
        section_header("Categories", ft.TextButton("New category", icon=ft.Icons.ADD,
                                                   on_click=lambda: category_dialog(app))),
        ft.Text("Drag a tile onto another to rearrange.", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
        ft.ResponsiveRow([draggable_category_tile(app, c) for c in cats] + [new_category_tile(app)],
                         spacing=10, run_spacing=10),
    ]

    common = dict(
        route="/",
        appbar=ft.AppBar(
            title=ft.Text("LinkVault"),
            actions=[ft.IconButton(ft.Icons.STAR, tooltip="Favorites", on_click=lambda: app.go("/favorites")),
                     app.sync_button()],
        ),
        navigation_bar=app.nav_bar(0),
        floating_action_button=ft.FloatingActionButton(icon=ft.Icons.ADD_LINK, tooltip="Save link or image from clipboard",
                                                       on_click=lambda: app.run(app.save_from_clipboard())),
    )
    if list_mode and recent:
        # One draggable list for the whole page; everything else rides along as its header/footer.
        return ft.View(**common, padding=ft.Padding.only(left=16, top=16, right=16, bottom=0), controls=[
            reorderable(app, recent[:8], lambda b: bookmark_card(app, b), expand=True,
                        header=ft.Column(top, spacing=12),
                        footer=ft.Container(ft.Column(bottom, spacing=12), padding=ft.Padding.only(top=12, bottom=90)),
                        padding=ft.Padding.only(right=10)),
        ])
    return ft.View(**common, scroll=ft.ScrollMode.ALWAYS,
                   padding=ft.Padding.only(left=16, top=16, right=26, bottom=16),  # room for the scrollbar
                   controls=top + ([middle] if middle else []) + bottom)


def build_list(app: "LinkVaultApp", route: str) -> ft.View:
    """Category, Favorites and Archive: thumbnails or list (each screen its own), drag to reorder in both."""
    if route == "/favorites":
        title, items, show_cat = "Favorites", app.db.bookmarks(favorite=True), True
    elif route == "/archive":
        title, items, show_cat = "Archive", app.db.bookmarks(archived=True), True
    else:
        cat_id = int(route.rsplit("/", 1)[-1])
        cat = app.db.category(cat_id) or {"name": "Category"}
        title, items, show_cat = cat["name"], app.db.bookmarks(category_id=cat_id), False

    appbar = ft.AppBar(title=ft.Text(title), actions=[view_switch(app, route), app.sync_button()])
    if not items:
        return ft.View(route=route, appbar=appbar, padding=12,
                       controls=[empty_state(ft.Icons.INBOX, "No links here yet.")])
    if app.view_for(route) == "thumbs":
        return ft.View(route=route, appbar=appbar, scroll=ft.ScrollMode.ALWAYS,
                       padding=ft.Padding.only(left=12, top=12, right=22, bottom=24), controls=[
                           ft.Text("Drag a card onto another to move it there.", size=12,
                                   color=ft.Colors.ON_SURFACE_VARIANT),
                           thumb_grid(app, items)])
    return ft.View(
        route=route, appbar=appbar, padding=ft.Padding.only(left=12, top=12, right=12, bottom=0),
        controls=[reorderable(app, items, lambda b: bookmark_card(app, b, show_category=show_cat), expand=True,
                              padding=ft.Padding.only(right=10, bottom=24))],
    )
