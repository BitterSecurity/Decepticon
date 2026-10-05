from __future__ import annotations

import os
from typing import Any

import httpx


class BlueClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def get(
        self, path: str, *, monitor: bool = False, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        name = "BLUE_MONITOR_URL" if monitor else "BLUE_SENSOR_URL"
        default = "http://127.0.0.1:18085" if monitor else "http://127.0.0.1:18081"
        url = os.environ.get(name, default).rstrip("/") + path
        if self._client is not None:
            response = await self._client.get(url, params=params)
        else:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url, params=params)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError(f"Blue API returned a non-object response for {path}")
        return result

    async def body(self, ref: str, *, max_bytes: int) -> dict[str, Any]:
        url = os.environ.get("BLUE_SENSOR_URL", "http://127.0.0.1:18081").rstrip("/")
        if self._client is not None:
            return await self._read_body(self._client, url, ref, max_bytes)
        async with httpx.AsyncClient(timeout=10) as client:
            return await self._read_body(client, url, ref, max_bytes)

    async def _read_body(
        self, client: httpx.AsyncClient, url: str, ref: str, max_bytes: int
    ) -> dict[str, Any]:
        data = bytearray()
        async with client.stream("GET", f"{url}/bodies/{ref}") as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", "0"))
            async for chunk in response.aiter_bytes():
                data.extend(chunk[: max_bytes + 1 - len(data)])
                if len(data) > max_bytes:
                    break
        preview = data[:max_bytes]
        return {
            "ref": ref,
            "total_bytes": total,
            "preview_bytes": len(preview),
            "preview_utf8": preview.decode("utf-8", "replace"),
            "preview_limited": total > max_bytes or len(data) > max_bytes,
        }
