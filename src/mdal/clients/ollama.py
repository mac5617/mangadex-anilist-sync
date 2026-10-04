"""The local model server (Ollama) used to write recommendation picks. Local addresses only.

Nothing about your accounts is sent: only titles, genres, tags, creators and scores from your list.
Structured output (a JSON schema in `format`) keeps answers machine-readable.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")


class OllamaError(Exception):
    pass


class OllamaUnavailable(OllamaError):
    """Ollama is not running, or the address is not local."""


class OllamaClient:
    def __init__(self, url: str, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 600.0) -> None:
        host = urlparse(url).hostname or ""
        if host not in LOCAL_HOSTS and f"[{host}]" not in LOCAL_HOSTS:
            raise OllamaUnavailable(f"OLLAMA_URL must point at this computer, not {host!r}")
        self.url = url.rstrip("/")
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def models(self) -> list[dict[str, Any]]:
        """[{name, size, capabilities, ...}]. Quick: a short timeout so pages never hang on it."""
        try:
            response = await self._http.get(f"{self.url}/api/tags", timeout=3.0)
        except httpx.TransportError as exc:
            raise OllamaUnavailable("Ollama is not running") from exc
        if response.status_code != 200:
            raise OllamaUnavailable(f"Ollama answered HTTP {response.status_code}")
        return response.json().get("models") or []

    async def chat_json(self, model: str, system: str, prompt: str, schema: dict[str, Any],
                        capabilities: list[str] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model, "stream": False, "format": schema,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "options": {"temperature": 0.4, "num_ctx": 16384},
        }
        if capabilities and "thinking" in capabilities:
            # gpt-oss can't switch thinking off; a little keeps it quick. Others answer directly.
            body["think"] = "low" if model.startswith("gpt-oss") else False
        try:
            response = await self._http.post(f"{self.url}/api/chat", json=body)
        except httpx.TimeoutException as exc:
            raise OllamaError("the model took more than 10 minutes to answer") from exc
        except httpx.TransportError as exc:
            raise OllamaUnavailable("Ollama is not running") from exc
        if response.status_code != 200:
            try:
                detail = response.json().get("error") or ""
            except ValueError:
                detail = ""
            raise OllamaError(f"Ollama answered HTTP {response.status_code}" + (f": {detail}" if detail else ""))
        content = (response.json().get("message") or {}).get("content") or ""
        try:
            return json.loads(content)
        except ValueError as exc:
            raise OllamaError("the model's answer was not valid JSON") from exc
