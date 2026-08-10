from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from jarvisbench.settings.multi_agent_runtime import (
    MultiAgentRuntime,
    MultiAgentRuntimeConfig,
)


DUMMY_SECRET = b"jarvisbench-offline-secretref-smoke-v1"


def main() -> int:
    state = Path("/var/lib/jarvisbench/secretref-smoke")
    secret = state / "provider.key"
    episode = state / "episode"
    state.mkdir(mode=0o700, parents=True, exist_ok=False)
    secret.write_bytes(DUMMY_SECRET)
    secret.chmod(0o400)
    task_root = Path("/opt/jarvisbench/tasks/multi_agent")
    task_dir = next(
        path for path in sorted(task_root.iterdir()) if path.is_dir() and not path.is_symlink()
    )
    runtime = MultiAgentRuntime(
        MultiAgentRuntimeConfig(
            task_dir=task_dir,
            episode_root=episode,
            project_id="offline-secretref-smoke",
            worker_model="smoke/provider-model",
            provider_base_url="https://provider.invalid/v1",
            api_key_file=secret,
        )
    )
    runtime._prepare()
    port = runtime._free_port()
    environment = runtime._environment()
    runtime._configure_openclaw(environment, gateway_port=port)
    log_path = runtime.logs_root / "gateway.log"
    with log_path.open("wb") as log:
        gateway = subprocess.Popen(
            [
                "openclaw",
                "gateway",
                "run",
                "--port",
                str(port),
                "--bind",
                "loopback",
                "--auth",
                "none",
                "--allow-unconfigured",
                "--ws-log",
                "compact",
            ],
            cwd=runtime.workspace,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            healthy = False
            for _ in range(80):
                if gateway.poll() is not None:
                    break
                check = subprocess.run(
                    ["openclaw", "gateway", "health"],
                    cwd=runtime.workspace,
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                )
                if check.returncode == 0:
                    healthy = True
                    break
                time.sleep(0.25)
            if not healthy:
                raise RuntimeError("offline SecretRef Gateway did not become healthy")
        finally:
            if gateway.poll() is None:
                os.killpg(gateway.pid, signal.SIGTERM)
                try:
                    gateway.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(gateway.pid, signal.SIGKILL)
                    gateway.wait(timeout=5)

    files = [path for path in episode.rglob("*") if path.is_file()]
    matches = [
        str(path.relative_to(episode))
        for path in files
        if DUMMY_SECRET in path.read_bytes()
    ]
    marker_files = [
        str(path.relative_to(episode))
        for path in files
        if b"secretref-managed" in path.read_bytes()
    ]
    result = {
        "schema_version": "jarvisbench.secretref-smoke.v1",
        "ok": healthy and not matches and bool(marker_files),
        "network": "none",
        "gateway_healthy": healthy,
        "agent_messages_sent": 0,
        "files_scanned": len(files),
        "exact_secret_matches": len(matches),
        "secretref_marker_files": sorted(marker_files),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
