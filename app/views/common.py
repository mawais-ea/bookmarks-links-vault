"""Shared UI pieces: bookmark cards, icons, the move-to-category dialog."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import flet as ft

if TYPE_CHECKING:
    from main import LinkVaultApp

# Server base URL; screenshot images come back as signed paths like /files/1/abc.png?sig=...
SERVER = ""


def resolve(url: str | None) -> str | None:
    if url and url.startswith("/"):
        return SERVER.rstrip("/") + url
    return url


def download_url(bm: dict) -> str:
    """A screenshot's image as a file to save (the server names it after the title)."""
    url = resolve(bm["url"]) or ""
    return url + ("&" if "?" in url else "?") + "download=1"


PLATFORM_ICONS = {
    "screenshot": ft.Icons.SCREENSHOT_MONITOR,
    "youtube": ft.Icons.SMART_DISPLAY, "instagram": ft.Icons.PHOTO_CAMERA, "tiktok": ft.Icons.MUSIC_NOTE,
    "x": ft.Icons.TAG, "facebook": ft.Icons.FACEBOOK, "reddit": ft.Icons.FORUM, "linkedin": ft.Icons.WORK,
    "pinterest": ft.Icons.PUSH_PIN, "threads": ft.Icons.ALTERNATE_EMAIL, "web": ft.Icons.LANGUAGE,
}
PLATFORM_NAMES = {
    "screenshot": "Screenshot",
    "youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok", "x": "X", "facebook": "Facebook",
    "reddit": "Reddit", "linkedin": "LinkedIn", "pinterest": "Pinterest", "threads": "Threads", "web": "Web",
}


def category_icon(name: str | None) -> ft.Icons:
    return getattr(ft.Icons, (name or "folder").upper(), ft.Icons.FOLDER)


def platform_icon(platform: str | None) -> ft.Icons:
    return PLATFORM_ICONS.get(platform or "web", ft.Icons.LANGUAGE)


def nice_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - dt).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    return dt.astimezone().strftime("%d %b %Y")


def thumbnail(url: str | None, platform: str | None, size: int = 88) -> ft.Control:
    fallback = ft.Container(
        width=size, height=size, border_radius=10, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        alignment=ft.Alignment.CENTER, content=ft.Icon(platform_icon(platform), size=size // 3),
    )
    if not url:
        return fallback
    # The icon sits behind the picture: if the image can't load (offline, expired link) the icon shows
    # instead of an empty box. error_content alone doesn't render on Flet desktop.
    return ft.Stack([
        fallback,
        ft.Image(src=resolve(url), width=size, height=size, fit=ft.BoxFit.COVER, border_radius=10,
                 error_content=ft.Container(width=size, height=size)),
    ], width=size, height=size)


def status_badge(bm: dict) -> ft.Control | None:
    if bm["status"] == "processing":
        return ft.Row([ft.ProgressRing(width=12, height=12, stroke_width=2), ft.Text("Reading link…", size=11)], spacing=6)
    if bm["status"] == "pending":
        return ft.Row([ft.Icon(ft.Icons.CLOUD_OFF, size=14), ft.Text("Waiting to upload", size=11)], spacing=6)
    if bm["status"] == "failed":
        return ft.Row([ft.Icon(ft.Icons.ERROR_OUTLINE, size=14, color=ft.Colors.ERROR),
                       ft.Text("Couldn't read this link", size=11)], spacing=6)
    return None


def bookmark_menu(app: "LinkVaultApp", bm: dict, icon_color=None, bgcolor=None) -> ft.PopupMenuButton:
    """The ⋮ menu shared by list cards and thumbnail cards."""
    return ft.PopupMenuButton(
        icon=ft.Icons.MORE_VERT,
        icon_color=icon_color,
        bgcolor=bgcolor,
        tooltip="Options",
        items=[
            ft.PopupMenuItem(content="Open", icon=ft.Icons.OPEN_IN_NEW, on_click=lambda: app.open_url(resolve(bm["url"]))),
            ft.PopupMenuItem(content="Unfavorite" if bm.get("is_favorite") else "Favorite",
                             icon=ft.Icons.STAR_BORDER,
                             on_click=lambda: app.run(app.update_bookmark(bm["id"], is_favorite=not bm.get("is_favorite")))),
            ft.PopupMenuItem(content="Rename", icon=ft.Icons.DRIVE_FILE_RENAME_OUTLINE,
                             on_click=lambda: rename_dialog(app, bm)),
            ft.PopupMenuItem(content="Move to…", icon=ft.Icons.DRIVE_FILE_MOVE,
                             on_click=lambda: show_move_dialog(app, bm)),
            *([ft.PopupMenuItem(content="Download", icon=ft.Icons.DOWNLOAD,
                                on_click=lambda: app.open_url(download_url(bm)))]
              if bm["platform"] == "screenshot" else []),
            ft.PopupMenuItem(content="Copy text", icon=ft.Icons.CONTENT_COPY,
                             on_click=lambda: app.run(app.copy(bm.get("raw_text") or "", "Text copied")))
            if bm["platform"] == "screenshot" else
            ft.PopupMenuItem(content="Copy link", icon=ft.Icons.CONTENT_COPY, on_click=lambda: app.run(app.copy(bm["url"]))),
            ft.PopupMenuItem(content="Delete", icon=ft.Icons.DELETE_OUTLINE,
                             on_click=lambda: app.run(app.delete_bookmark(bm["id"]))),
        ],
    )


def bookmark_card(app: "LinkVaultApp", bm: dict, show_category: bool = True) -> ft.Control:
    meta_bits = [ft.Icon(platform_icon(bm["platform"]), size=14),
                 ft.Text(PLATFORM_NAMES.get(bm["platform"], bm["platform"]), size=11)]
    if show_category and bm.get("category_name"):
        meta_bits += [ft.Text("·", size=11), ft.Text(bm["category_name"], size=11)]
    meta_bits += [ft.Text("·", size=11), ft.Text(nice_date(bm.get("created_at")), size=11)]
    if bm.get("recipe"):
        meta_bits.append(ft.Icon(ft.Icons.RESTAURANT_MENU, size=14, tooltip="Has a full recipe"))
    if bm.get("is_favorite"):
        meta_bits.append(ft.Icon(ft.Icons.STAR, size=14, color=ft.Colors.AMBER))

    body = [
        ft.Text(bm.get("title") or bm["url"], weight=ft.FontWeight.W_600, max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS),
    ]
    if bm.get("description"):
        body.append(ft.Text(bm["description"], size=13, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                            color=ft.Colors.ON_SURFACE_VARIANT))
    badge = status_badge(bm)
    body.append(badge or ft.Row(meta_bits, spacing=4, wrap=True))

    is_local = bm["id"] < 0
    menu = None if is_local else bookmark_menu(app, bm)
    row = ft.Row(
        [thumbnail(bm.get("thumbnail_url"), bm["platform"]), ft.Column(body, spacing=4, expand=True)]
        + ([menu] if menu else []),
        vertical_alignment=ft.CrossAxisAlignment.START, spacing=12,
    )
    return ft.Card(
        content=ft.Container(
            row, padding=10, border_radius=12, ink=True,
            on_click=None if is_local else (lambda: app.go(f"/bookmark/{bm['id']}")),
            on_long_press=None if is_local else (lambda: show_move_dialog(app, bm)),
        ),
    )


def mini_card(app: "LinkVaultApp", bm: dict, grid: bool = False, width: float | None = None) -> ft.Container:
    """Compact card for the Recent strip and thumbnail grids, with the same ⋮ menu as list cards."""
    is_local = bm["id"] < 0
    picture: ft.Control = thumbnail(bm.get("thumbnail_url"), bm["platform"], size=150)
    if not is_local:
        # Menu button on the image's top-right corner; the dark circle keeps it visible on any picture.
        picture = ft.Stack([
            picture,
            ft.Container(
                bookmark_menu(app, bm, icon_color=ft.Colors.WHITE,
                              bgcolor=None),
                right=2, top=2, width=36, height=36, border_radius=18,
                bgcolor=ft.Colors.with_opacity(0.55, ft.Colors.BLACK),
                alignment=ft.Alignment.CENTER,
            ),
        ], width=150, height=150)
    meta = []
    if bm.get("is_favorite"):
        meta.append(ft.Icon(ft.Icons.STAR, size=13, color=ft.Colors.AMBER))
    if bm.get("recipe"):
        meta.append(ft.Icon(ft.Icons.RESTAURANT_MENU, size=13))
    return ft.Container(
        width=width or (None if grid else 150), ink=True, border_radius=12,
        border=ft.Border.all(2, ft.Colors.TRANSPARENT),  # lit up when a card is dragged over it
        # strip: room for its scrollbar under the title; grid: a little breathing space
        padding=ft.Padding.all(6) if grid else ft.Padding.only(bottom=16),
        on_click=None if is_local else (lambda: app.go(f"/bookmark/{bm['id']}")),
        on_long_press=None if is_local else (lambda: show_move_dialog(app, bm)),
        content=ft.Column([
            picture,
            ft.Row([*meta, ft.Text(bm.get("title") or bm["url"], size=12, max_lines=2,
                                   overflow=ft.TextOverflow.ELLIPSIS, expand=True)],
                   spacing=4, vertical_alignment=ft.CrossAxisAlignment.START),
        ], spacing=4),
    )


def rename_dialog(app: "LinkVaultApp", bm: dict) -> None:
    """Give a saved link or screenshot a name of your own."""
    name = ft.TextField(label="Name", value=bm.get("title") or "", autofocus=True, multiline=True, max_lines=4)
    error = ft.Text(color=ft.Colors.ERROR, visible=False)

    async def save():
        new = " ".join((name.value or "").split())
        if not new:
            error.value, error.visible = "The name can't be empty", True
            error.update()
            return
        app.page.pop_dialog()
        if new != (bm.get("title") or ""):
            await app.update_bookmark(bm["id"], title=new)
            app.toast("Renamed")

    name.on_submit = lambda: app.run(save())
    app.page.show_dialog(ft.AlertDialog(
        title=ft.Text("Rename"),
        content=ft.Column([name, error], tight=True, width=380),
        actions=[ft.TextButton("Cancel", on_click=lambda: app.page.pop_dialog()),
                 ft.FilledButton("Save", on_click=lambda: app.run(save()))],
    ))


def show_move_dialog(app: "LinkVaultApp", bm: dict) -> None:
    def pick(cat_id: int):
        async def _go():
            app.page.pop_dialog()
            await app.update_bookmark(bm["id"], category_id=cat_id)
        return lambda: app.run(_go())

    tiles = [
        ft.ListTile(
            leading=ft.Icon(category_icon(c["icon"]), color=c["color"]),
            title=ft.Text(c["name"]),
            trailing=ft.Icon(ft.Icons.CHECK) if c["id"] == bm.get("category_id") else None,
            on_click=pick(c["id"]),
        )
        for c in app.db.categories()
    ]
    app.page.show_dialog(ft.AlertDialog(
        title=ft.Text("Move to category"),
        content=ft.Column(tiles, scroll=ft.ScrollMode.ALWAYS, tight=True, width=360, height=420),
        actions=[ft.TextButton("Cancel", on_click=lambda: app.page.pop_dialog())],
    ))


def empty_state(icon: ft.Icons, text: str) -> ft.Control:
    return ft.Container(
        padding=40, alignment=ft.Alignment.CENTER,
        content=ft.Column([ft.Icon(icon, size=48, color=ft.Colors.OUTLINE),
                           ft.Text(text, color=ft.Colors.ON_SURFACE_VARIANT, text_align=ft.TextAlign.CENTER)],
                          horizontal_alignment=ft.CrossAxisAlignment.CENTER),
    )
