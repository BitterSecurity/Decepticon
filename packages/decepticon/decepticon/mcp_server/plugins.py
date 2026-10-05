from __future__ import annotations

from typing import Any

import httpx


class PluginClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client

    async def request(self, method: str, path: str) -> dict[str, Any]:
        url = self._base_url + "/_decepticon/bundles" + path
        if self._client is not None:
            response = await self._client.request(method, url)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(method, url)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("plugin bundle API returned a non-object response")
        return payload
