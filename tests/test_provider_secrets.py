from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvisbench.core.providers import OpenAICompatibleProvider, read_secret, resolve_secret_file
from jarvisbench.settings.multi_agent_runtime import MultiAgentRuntime, MultiAgentRuntimeConfig


def _task(root: Path) -> Path:
    task = root / "task"
    public = task / "public"
    public.mkdir(parents=True)
    for name in ("workstreams.json", "output_contract.json", "result_schema.json"):
        (public / name).write_text("{}\n", encoding="utf-8")
    (task / "task.public.json").write_text(
        json.dumps(
            {
                "task_id": "secret-fixture",
                "episode": {
                    "brief": "fixture",
                    "worker_count": 1,
                    "result_paths": ["results/value.txt"],
                },
                "runtime": {"worker_timeout_seconds": 30},
            }
        ),
        encoding="utf-8",
    )
    return task


def test_provider_accepts_bounded_mounted_secret_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    secret = tmp_path / "provider-key"
    secret.write_text("private-value\n", encoding="utf-8")
    secret.chmod(0o400)
    monkeypatch.delenv("JARVISBENCH_API_KEY", raising=False)
    monkeypatch.setenv("JARVISBENCH_API_KEY_FILE", str(secret))
    provider = OpenAICompatibleProvider(base_url="https://provider.invalid/v1")
    assert provider.api_key == "private-value"


def test_secret_file_rejects_symlink(tmp_path: Path):
    target = tmp_path / "target"
    target.write_text("private-value\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="unsafe credential file"):
        read_secret(
            value_env="UNSET_VALUE",
            file_env="UNSET_FILE",
            explicit_file=link,
        )


@pytest.mark.parametrize("mode", [0o404, 0o440, 0o644])
def test_secret_file_rejects_group_or_world_access(tmp_path: Path, mode: int):
    secret = tmp_path / "provider-key"
    secret.write_text("private-value\n", encoding="utf-8")
    secret.chmod(mode)
    with pytest.raises(ValueError, match="unsafe credential file"):
        resolve_secret_file(file_env="UNSET_FILE", explicit_file=secret)


def test_secret_file_rejects_empty_file(tmp_path: Path):
    secret = tmp_path / "provider-key"
    secret.touch(mode=0o400)
    with pytest.raises(ValueError, match="unsafe credential file"):
        resolve_secret_file(file_env="UNSET_FILE", explicit_file=secret)


def test_secret_file_resolver_returns_absolute_path_without_reading(tmp_path: Path):
    secret = tmp_path / "provider-key"
    secret.write_text("private-value\n", encoding="utf-8")
    secret.chmod(0o400)
    assert resolve_secret_file(file_env="UNSET_FILE", explicit_file=secret) == secret.resolve()


def test_runtime_reads_worker_secret_file_without_environment_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    secret = tmp_path / "worker-key"
    secret.write_text("worker-private-value\n", encoding="utf-8")
    secret.chmod(0o400)
    monkeypatch.delenv("JARVISBENCH_WORKER_API_KEY", raising=False)
    runtime = MultiAgentRuntime(
        MultiAgentRuntimeConfig(
            task_dir=_task(tmp_path),
            episode_root=tmp_path / "episode",
            worker_model="provider/model",
            provider_base_url="https://provider.invalid/v1",
            api_key_file=secret,
        )
    )
    runtime.openclaw_home.mkdir(parents=True)
    runtime._write_openclaw_config()
    value = json.loads((runtime.openclaw_home / "openclaw.json").read_text())
    assert value["models"]["providers"]["provider"]["apiKey"] == {
        "source": "file",
        "provider": "jarvisbench_worker",
        "id": "value",
    }
    assert value["secrets"]["providers"]["jarvisbench_worker"] == {
        "source": "file",
        "path": str(secret.resolve()),
        "mode": "singleValue",
    }
    assert "worker-private-value" not in (runtime.openclaw_home / "openclaw.json").read_text()


def test_runtime_uses_env_secret_ref_without_persisting_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PRIVATE_WORKER_KEY", "worker-private-value")
    runtime = MultiAgentRuntime(
        MultiAgentRuntimeConfig(
            task_dir=_task(tmp_path),
            episode_root=tmp_path / "episode",
            worker_model="provider/model",
            provider_base_url="https://provider.invalid/v1",
            api_key_env="PRIVATE_WORKER_KEY",
        )
    )
    runtime.openclaw_home.mkdir(parents=True)
    runtime._write_openclaw_config()
    raw = (runtime.openclaw_home / "openclaw.json").read_text()
    value = json.loads(raw)
    assert value["models"]["providers"]["provider"]["apiKey"] == {
        "source": "env",
        "provider": "jarvisbench_worker",
        "id": "PRIVATE_WORKER_KEY",
    }
    assert value["secrets"]["providers"]["jarvisbench_worker"] == {
        "source": "env",
        "allowlist": ["PRIVATE_WORKER_KEY"],
    }
    assert "worker-private-value" not in raw
