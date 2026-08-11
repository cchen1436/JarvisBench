from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol


OPENAI_API_BASE = "https://api.openai.com/v1"


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class Completion:
    text: str
    first_token_ms: float | None
    total_ms: float
    model: str


class TextProvider(Protocol):
    def complete(self, messages: Iterable[Message], *, model: str, reasoning: str | None = None) -> Completion:
        ...


def resolve_secret_file(
    *,
    file_env: str,
    explicit_file: str | Path | None = None,
) -> Path | None:
    """Resolve and validate one bounded credential file without reading it."""

    source_value = explicit_file if explicit_file is not None else os.environ.get(file_env, "")
    if not source_value:
        return None
    source = Path(source_value)
    metadata = source.stat() if source.exists() and not source.is_symlink() else None
    if (
        metadata is None
        or not source.is_file()
        or metadata.st_size < 1
        or metadata.st_size > 16 * 1024
        or stat.S_IMODE(metadata.st_mode) & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise ValueError(f"unsafe credential file configured by {file_env}")
    return source.resolve(strict=True)


def read_secret(
    *,
    value_env: str,
    file_env: str,
    explicit_value: str | None = None,
    explicit_file: str | Path | None = None,
) -> str:
    """Read one bounded credential without requiring it in process metadata."""

    value = explicit_value if explicit_value is not None else os.environ.get(value_env, "")
    if value:
        return value
    source = resolve_secret_file(file_env=file_env, explicit_file=explicit_file)
    if source is None:
        return ""
    value = source.read_text(encoding="utf-8").strip()
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(f"invalid credential file configured by {file_env}")
    return value


class OpenAIProvider:
    """Official OpenAI Responses API adapter for the reference controller.

    The endpoint is deliberately fixed to OpenAI's public API. Worker models
    are invoked independently by OpenClaw and never pass through this client.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_key_file: str | Path | None = None,
        timeout: float = 180.0,
        client: Any | None = None,
    ):
        self.api_key = read_secret(
            value_env="OPENAI_API_KEY",
            file_env="OPENAI_API_KEY_FILE",
            explicit_value=api_key,
            explicit_file=api_key_file,
        )
        self.timeout = timeout
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY or OPENAI_API_KEY_FILE is required")
        if client is None:
            # Import lazily so no-model validation never initializes a model
            # client or consults provider configuration.
            from openai import OpenAI

            client = OpenAI(
                api_key=self.api_key,
                base_url=OPENAI_API_BASE,
                timeout=self.timeout,
            )
        self.client = client

    def complete(self, messages: Iterable[Message], *, model: str, reasoning: str | None = None) -> Completion:
        payload: dict[str, Any] = {
            "model": model,
            "input": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            # Jarvis and Luna prompts may contain requester-private context.
            # The benchmark is stateless across calls, so server-side storage
            # is unnecessary.
            "store": False,
        }
        if reasoning:
            payload["reasoning"] = {
                "effort": "none" if reasoning == "off" else reasoning
            }
        start = time.perf_counter()
        first: float | None = None
        chunks: list[str] = []
        try:
            stream = self.client.responses.create(**payload)
            try:
                for event in stream:
                    if getattr(event, "type", "") != "response.output_text.delta":
                        continue
                    text = getattr(event, "delta", "") or ""
                    if not isinstance(text, str) or not text:
                        continue
                    if first is None:
                        first = (time.perf_counter() - start) * 1_000
                    chunks.append(text)
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
        except Exception as exc:
            # Do not include provider exception bodies: they can echo request
            # data. Preserve only a bounded status/type for diagnostics.
            status = getattr(exc, "status_code", None)
            detail = f"HTTP {status}" if isinstance(status, int) else type(exc).__name__
            raise RuntimeError(f"OpenAI API request failed ({detail})") from None
        total = (time.perf_counter() - start) * 1_000
        return Completion("".join(chunks), first, total, model)
