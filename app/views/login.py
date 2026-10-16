from typing import TYPE_CHECKING

import flet as ft

from api_client import ApiError, OfflineError

if TYPE_CHECKING:
    from main import LinkVaultApp


def build(app: "LinkVaultApp") -> ft.View:
    server = ft.TextField(label="Server URL", value=app.db.get("server_url") or app.default_server(),
                          keyboard_type=ft.KeyboardType.URL,
                          visible=not app.web)  # on the web page the server is the one serving it
    email = ft.TextField(label="Email", value=app.db.get("email"), keyboard_type=ft.KeyboardType.EMAIL,
                         autofill_hints=[ft.AutofillHint.EMAIL])
    password = ft.TextField(label="Password", password=True, can_reveal_password=True)
    error = ft.Text(color=ft.Colors.ERROR, visible=False)
    busy = ft.ProgressRing(visible=False, width=20, height=20)

    async def submit(register: bool):
        error.visible = False
        busy.visible = True
        app.page.update()
        app.api.configure(base_url=server.value.strip(), token="")
        try:
            fn = app.api.register if register else app.api.login
            res = await fn(email.value.strip(), password.value)
        except OfflineError:
            error.value = "Can't reach the server. Check the URL and that the backend is running."
        except ApiError as e:
            error.value = e.detail
        else:
            await app.on_logged_in(server.value.strip(), email.value.strip(), res)
            return
        finally:
            busy.visible = False
        error.visible = True
        app.page.update()

    password.on_submit = lambda: app.run(submit(False))
    return ft.View(
        route="/login",
        padding=24,
        scroll=ft.ScrollMode.ALWAYS,
        vertical_alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[ft.Container(width=420, content=ft.Column([
            ft.Icon(ft.Icons.BOOKMARKS, size=56, color=ft.Colors.PRIMARY),
            ft.Text("LinkVault", size=28, weight=ft.FontWeight.BOLD),
            ft.Text("Save links from anywhere. They sort themselves.", color=ft.Colors.ON_SURFACE_VARIANT),
            server, email, password, error,
            ft.Row([
                busy,
                ft.TextButton("Create account", on_click=lambda: app.run(submit(True))),
                ft.FilledButton("Log in", on_click=lambda: app.run(submit(False))),
            ], alignment=ft.MainAxisAlignment.END),
        ], horizontal_alignment=ft.CrossAxisAlignment.STRETCH, spacing=12))],
    )
