from typing import TYPE_CHECKING

import flet as ft

from api_client import ApiError, OfflineError
from screenshot_watcher import screenshot_folders
from .common import category_icon

if TYPE_CHECKING:
    from main import LinkVaultApp

ICON_CHOICES = ["folder", "yard", "pets", "sports_soccer", "palette", "camera_alt", "movie", "book",
                "child_care", "directions_car", "local_cafe", "spa", "science", "brush", "favorite"]
COLOR_CHOICES = ["#607D8B", "#E64A19", "#1976D2", "#388E3C", "#7B1FA2", "#F57C00", "#D81B60", "#0097A7"]


def _section(title: str) -> ft.Control:
    return ft.Container(ft.Text(title, size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.PRIMARY),
                        padding=ft.Padding.only(top=18, bottom=4))


def category_dialog(app: "LinkVaultApp", cat: dict | None = None) -> None:
    name = ft.TextField(label="Name", value=cat["name"] if cat else "", autofocus=True)
    icon = ft.Dropdown(label="Icon", value=cat["icon"] if cat else "folder", options=[
        ft.DropdownOption(key=i, text=i.replace("_", " "), leading_icon=category_icon(i))
        for i in dict.fromkeys(ICON_CHOICES + ([cat["icon"]] if cat else []))])
    color = ft.Dropdown(label="Color", value=cat["color"] if cat else COLOR_CHOICES[0], options=[
        ft.DropdownOption(key=c, text=c, leading_icon=ft.Icon(ft.Icons.CIRCLE, color=c))
        for c in dict.fromkeys(COLOR_CHOICES + ([cat["color"]] if cat else []))])

    async def save():
        if not (name.value or "").strip():
            return
        try:
            if cat:
                await app.api.update_category(cat["id"], name=name.value.strip(), icon=icon.value, color=color.value)
            else:
                await app.api.create_category(name.value.strip(), icon.value, color.value)
        except ApiError as e:
            app.toast(e.detail)
            return
        except OfflineError:
            app.toast("You're offline; categories need a connection")
            return
        app.page.pop_dialog()
        app.toast(f"Saved category “{name.value.strip()}”")
        await app.do_sync()

    async def delete():
        try:
            await app.api.delete_category(cat["id"])
        except ApiError as e:
            app.toast(e.detail)
            return
        app.page.pop_dialog()
        app.toast(f"Deleted {cat['name']}; its links moved to Other")
        await app.do_sync()

    actions = [ft.TextButton("Cancel", on_click=lambda: app.page.pop_dialog()),
               ft.FilledButton("Save", on_click=lambda: app.run(save()))]
    if cat and cat["name"] != "Other":
        actions.insert(0, ft.TextButton("Delete", on_click=lambda: app.run(delete())))
    app.page.show_dialog(ft.AlertDialog(
        title=ft.Text("Edit category" if cat else "New category"),
        content=ft.Column([name, icon, color], tight=True, width=340),
        actions=actions,
    ))


def rule_dialog(app: "LinkVaultApp") -> None:
    kind = ft.Dropdown(label="When", value="domain", options=[
        ft.DropdownOption(key="domain", text="Link is from website"),
        ft.DropdownOption(key="keyword", text="Text or hashtag contains")])
    pattern = ft.TextField(label="Website or word", hint_text="e.g. allrecipes.com or sourdough")
    cats = app.db.categories()
    category = ft.Dropdown(label="Put it in", value=str(cats[0]["id"]) if cats else None,
                           options=[ft.DropdownOption(key=str(c["id"]), text=c["name"]) for c in cats])

    async def save():
        if len((pattern.value or "").strip()) < 2 or not category.value:
            return
        try:
            await app.api.create_rule(kind.value, pattern.value.strip(), int(category.value))
        except ApiError as e:
            app.toast(e.detail)
            return
        app.page.pop_dialog()
        app.refresh()

    app.page.show_dialog(ft.AlertDialog(
        title=ft.Text("New rule"),
        content=ft.Column([kind, pattern, category], tight=True, width=340),
        actions=[ft.TextButton("Cancel", on_click=lambda: app.page.pop_dialog()),
                 ft.FilledButton("Add", on_click=lambda: app.run(save()))],
    ))


def build(app: "LinkVaultApp") -> ft.View:
    rules_col = ft.Column(spacing=0)

    async def load_rules():
        try:
            rules = await app.api.rules()
        except Exception:
            rules_col.controls = [ft.Text("Rules are available when online.", size=12)]
        else:
            rules_col.controls = [
                ft.ListTile(
                    leading=ft.Icon(ft.Icons.LANGUAGE if r["type"] == "domain" else ft.Icons.TAG),
                    title=ft.Text(r["pattern"]), subtitle=ft.Text(f"→ {r['category_name']}"),
                    trailing=ft.IconButton(ft.Icons.DELETE_OUTLINE, on_click=lambda e, r=r: app.run(delete_rule(r["id"]))),
                ) for r in rules
            ] or [ft.Text("No custom rules yet. Moving 3 links from the same website to the same category "
                          "creates one automatically.", size=12, color=ft.Colors.ON_SURFACE_VARIANT)]
        rules_col.update()

    async def delete_rule(rule_id: int):
        await app.api.delete_rule(rule_id)
        await load_rules()

    def theme_change(e):
        app.db.set("theme", e.control.value)
        app.apply_theme()

    api_key = app.db.get("api_key")
    cats = app.db.categories()
    controls = [
        _section("Account"),
        ft.ListTile(leading=ft.Icon(ft.Icons.PERSON), title=ft.Text(app.db.get("email") or "—"),
                    subtitle=ft.Text(app.db.get("server_url"))),
        ft.ListTile(
            leading=ft.Icon(ft.Icons.KEY), title=ft.Text("API key"),
            subtitle=ft.Text("For the iPhone Shortcut, share extension, tray app and browser extension"),
            trailing=ft.IconButton(ft.Icons.CONTENT_COPY, tooltip="Copy API key",
                                   on_click=lambda: app.run(app.copy(api_key, "API key copied"))),
        ),

        *([
            _section("Screenshots"),
            ft.Switch(label="Save new screenshots automatically", value=app.auto_screenshots_enabled(),
                      on_change=lambda e: app.set_auto_screenshots(e.control.value)),
            ft.Text("Watching: " + (", ".join(str(f) for f in screenshot_folders()) or "no Screenshots folder found"),
                    size=12, color=ft.Colors.ON_SURFACE_VARIANT),
        ] if app.is_windows else []),

        _section("Appearance"),
        ft.Dropdown(label="Theme", value=app.db.get("theme", "system"), width=220, on_select=theme_change,
                    options=[ft.DropdownOption(key=k, text=k.title()) for k in ("system", "light", "dark")]),

        _section("Categories"),
        *[ft.ListTile(leading=ft.Icon(category_icon(c["icon"]), color=c["color"]), title=ft.Text(c["name"]),
                      subtitle=ft.Text(f"{c['count']} links"), on_click=lambda e, c=c: category_dialog(app, c))
          for c in cats],
        ft.TextButton("Add category", icon=ft.Icons.ADD, on_click=lambda: category_dialog(app)),

        _section("Rules"),
        rules_col,
        ft.TextButton("Add rule", icon=ft.Icons.ADD, on_click=lambda: rule_dialog(app)),

        _section("Your data"),
        ft.ListTile(leading=ft.Icon(ft.Icons.ARCHIVE), title=ft.Text("Archive"), on_click=lambda: app.go("/archive")),
        ft.Row([ft.OutlinedButton(f"Export {fmt.upper()}", icon=ft.Icons.DOWNLOAD,
                                  on_click=lambda e, fmt=fmt: app.run(app.export(fmt)))
                for fmt in ("json", "csv", "html")], wrap=True),
        ft.TextButton("Re-download everything from server", icon=ft.Icons.SYNC,
                      on_click=lambda: app.run(app.do_sync(full=True))),
        ft.TextButton("Log out", icon=ft.Icons.LOGOUT, on_click=lambda: app.logout()),
    ]

    view = ft.View(
        route="/settings",
        appbar=ft.AppBar(title=ft.Text("Settings")),
        navigation_bar=app.nav_bar(3),
        scroll=ft.ScrollMode.ALWAYS,
        padding=ft.Padding.only(left=16, top=16, right=26, bottom=16),  # room for the scrollbar
        controls=controls,
    )
    app.after_render(load_rules)
    return view
