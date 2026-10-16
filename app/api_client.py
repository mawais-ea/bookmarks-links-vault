"""Thin async client for the LinkVault backend."""

from typing import Any

import httpx


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class OfflineError(Exception):
    pass


class ApiClient:
    def __init__(self, base_url: str = "", token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def configure(self, base_url: str | None = None, token: str | None = None) -> None:
        if base_url is not None:
            self.base_url = base_url.rstrip("/")
        if token is not None:
            self.token = token

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        if not self.base_url:
            raise OfflineError("No server configured")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=20, headers=headers) as c:
                r = await c.request(method, path, **kwargs)
        except httpx.TransportError as e:
            raise OfflineError(str(e)) from e
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise ApiError(r.status_code, detail if isinstance(detail, str) else str(detail))
        if r.status_code == 204:
            return None
        ctype = r.headers.get("content-type", "")
        return r.json() if "json" in ctype else r.text

    # auth
    async def register(self, email: str, password: str) -> dict:
        return await self._request("POST", "/auth/register", json={"email": email, "password": password})

    async def login(self, email: str, password: str) -> dict:
        return await self._request("POST", "/auth/login", json={"email": email, "password": password})

    async def me(self) -> dict:
        return await self._request("GET", "/me")

    # bookmarks
    async def save_bookmark(self, url: str, note: str | None = None) -> dict:
        return await self._request("POST", "/bookmarks", json={"url": url, "note": note})

    async def upload_screenshot(self, data: bytes, filename: str, taken_at: str | None = None) -> dict:
        form = {"taken_at": taken_at} if taken_at else {}
        return await self._request("POST", "/screenshots", files={"file": (filename, data)}, data=form)

    async def list_bookmarks(self, since: str | None = None, **params) -> dict:
        if since:
            params["since"] = since
        params.setdefault("limit", 1000)
        return await self._request("GET", "/bookmarks", params=params)

    async def get_bookmark(self, bookmark_id: int) -> dict:
        return await self._request("GET", f"/bookmarks/{bookmark_id}")

    async def update_bookmark(self, bookmark_id: int, **changes) -> dict:
        return await self._request("PATCH", f"/bookmarks/{bookmark_id}", json=changes)

    async def delete_bookmark(self, bookmark_id: int) -> None:
        await self._request("DELETE", f"/bookmarks/{bookmark_id}")

    async def find_recipe(self, bookmark_id: int) -> dict:
        return await self._request("POST", f"/bookmarks/{bookmark_id}/recipe")

    async def refresh_bookmark(self, bookmark_id: int) -> dict:
        return await self._request("POST", f"/bookmarks/{bookmark_id}/refresh")

    # categories & rules
    async def categories(self) -> list[dict]:
        return await self._request("GET", "/categories")

    async def create_category(self, name: str, icon: str = "folder", color: str = "#607D8B") -> dict:
        return await self._request("POST", "/categories", json={"name": name, "icon": icon, "color": color})

    async def update_category(self, category_id: int, **changes) -> dict:
        return await self._request("PATCH", f"/categories/{category_id}", json=changes)

    async def delete_category(self, category_id: int) -> None:
        await self._request("DELETE", f"/categories/{category_id}")

    async def reorder_categories(self, ids: list[int]) -> list[dict]:
        return await self._request("POST", "/categories/reorder", json={"ids": ids})

    async def rules(self) -> list[dict]:
        return await self._request("GET", "/rules")

    async def create_rule(self, type_: str, pattern: str, category_id: int) -> dict:
        return await self._request("POST", "/rules", json={"type": type_, "pattern": pattern, "category_id": category_id})

    async def delete_rule(self, rule_id: int) -> None:
        await self._request("DELETE", f"/rules/{rule_id}")

    async def export(self, fmt: str) -> str:
        return await self._request("GET", "/export", params={"format": fmt})
