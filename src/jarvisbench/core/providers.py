from __future__ import annotations

import json
import os
import stat
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol


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


class OpenAICompatibleProvider:
    """Small provider-neutral adapter with no endpoint or credential defaults."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        api_key_file: str | Path | None = None,
        timeout: float = 180.0,
    ):
        self.base_url = (base_url or os.environ.get("JARVISBENCH_API_BASE", "")).rstrip("/")
        self.api_key = read_secret(
            value_env="JARVISBENCH_API_KEY",
            file_env="JARVISBENCH_API_KEY_FILE",
            explicit_value=api_key,
            explicit_file=api_key_file,
        )
        self.timeout = timeout
        if not self.base_url or not self.api_key:
            raise ValueError(
                "JARVISBENCH_API_BASE and a JARVISBENCH_API_KEY value or file are required"
            )

    def complete(self, messages: Iterable[Message], *, model: str, reasoning: str | None = None) -> Completion:
        payload: dict[str, object] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
        }
        if reasoning:
            payload["reasoning_effort"] = reasoning
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        start = time.perf_counter()
        first: float | None = None
        chunks: list[str] = []
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        break
                    data = json.loads(body)
                    text = data.get("choices", [{}])[0].get("delta", {}).get("content") or ""
                    if text:
                        if first is None:
                            first = (time.perf_counter() - start) * 1_000
                        chunks.append(text)
        except urllib.error.HTTPError as exc:
            # Do not include response bodies: providers sometimes echo request data.
            raise RuntimeError(f"provider HTTP error {exc.code}") from exc
        total = (time.perf_counter() - start) * 1_000
        return Completion("".join(chunks), first, total, model)
