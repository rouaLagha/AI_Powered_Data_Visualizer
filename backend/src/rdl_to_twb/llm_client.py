from __future__ import annotations

from dataclasses import dataclass
import json
import re
import socket
from typing import Any
from urllib import error as urlerror
from urllib import request


@dataclass
class LLMConfig:
    api_url: str
    api_key: str | None = None
    model: str = "gpt-4o-mini"
    temperature: float = 0.1
    timeout_seconds: int = 120


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Some hosted endpoints (e.g., Groq behind Cloudflare) reject the default
            # Python urllib user agent with HTTP 403 (error code 1010).
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        req = request.Request(
            url=self.config.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                body = resp.read().decode("utf-8")
        except urlerror.HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                error_body = ""
            preview = error_body[:300].replace("\n", " ").strip()
            raise ConnectionError(
                f"LLM endpoint returned HTTP {exc.code}: {self.config.api_url}. "
                f"Response preview: {preview or '<empty>'}"
            ) from exc
        except urlerror.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, ConnectionRefusedError):
                raise ConnectionError(
                    f"Cannot connect to LLM endpoint: {self.config.api_url}. "
                    "Connection was refused. Start your local LLM server or update backend/config/llm_config.example.json to a reachable API URL."
                ) from exc
            raise ConnectionError(
                f"Cannot reach LLM endpoint: {self.config.api_url}. Reason: {reason or exc}."
            ) from exc
        except socket.timeout as exc:
            raise TimeoutError(
                f"LLM request timed out after {self.config.timeout_seconds}s: {self.config.api_url}."
            ) from exc

        body = body.strip()
        if not body:
            raise ValueError(
                f"LLM endpoint returned an empty response body: {self.config.api_url}."
            )

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            preview = body[:300].replace("\n", " ").strip()
            raise ValueError(
                "LLM endpoint response is not valid JSON. "
                f"URL: {self.config.api_url}. Response preview: {preview}"
            ) from exc
        return _extract_message_content(data)


def _extract_message_content(response_payload: dict[str, Any]) -> str:
    choices = response_payload.get("choices", [])
    if not choices:
        raise ValueError("No choices returned by LLM API")

    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM response content is empty")
    return _sanitize_message_content(content)


def _sanitize_message_content(content: str) -> str:
    text = content.strip()
    # Some reasoning models prepend an internal <think>...</think> section.
    # Remove only leading think blocks and keep the user-facing answer unchanged.
    text = re.sub(r"^\s*(?:<think>.*?</think>\s*)+", "", text, flags=re.DOTALL)
    return text.strip()
