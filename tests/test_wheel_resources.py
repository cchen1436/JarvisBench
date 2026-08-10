from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PLUGIN = ROOT / "plugins" / "openclaw" / "jarvis_supervisor"
PLUGIN_FILES = ("index.ts", "openclaw.plugin.json", "package.json", "read_only_exec.ts")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_non_editable_wheel_contains_default_supervisor_plugin(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    site = tmp_path / "site"
    dist.mkdir()
    site.mkdir()

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(dist),
            str(ROOT),
        ],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    wheels = list(dist.glob("jarvisbench-*.whl"))
    assert len(wheels) == 1
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            "--target",
            str(site),
            str(wheels[0]),
        ],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    task = tmp_path / "task"
    task.mkdir()
    (task / "task.public.json").write_text(
        json.dumps(
            {
                "schema_version": "jarvisbench.task-public.v1",
                "task_id": "wheel_resource_test",
                "setting": "multi_agent",
                "episode": {
                    "brief": "Exercise packaging only.",
                    "worker_count": 1,
                    "result_paths": ["results/output.json"],
                },
                "runtime": {},
            }
        ),
        encoding="utf-8",
    )
    probe = """
import json
from pathlib import Path
import jarvisbench
from jarvisbench.settings.multi_agent_runtime import MultiAgentRuntime, MultiAgentRuntimeConfig

runtime = MultiAgentRuntime(MultiAgentRuntimeConfig(
    task_dir=Path(r'''%s'''),
    episode_root=Path(r'''%s'''),
    worker_model='test/model',
))
plugin = runtime.plugin_dir.resolve()
print(json.dumps({
    'package': str(Path(jarvisbench.__file__).resolve()),
    'plugin': str(plugin),
    'files': sorted(path.name for path in plugin.iterdir() if path.is_file()),
}))
""" % (task, tmp_path / "episode")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(site)
    env["PYTHONNOUSERSITE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        check=True,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    installed = json.loads(result.stdout)
    package_path = Path(installed["package"])
    plugin_path = Path(installed["plugin"])
    assert package_path.is_relative_to(site)
    assert plugin_path.is_relative_to(site)
    assert set(PLUGIN_FILES).issubset(installed["files"])
    assert {
        name: _sha256(plugin_path / name) for name in PLUGIN_FILES
    } == {name: _sha256(SOURCE_PLUGIN / name) for name in PLUGIN_FILES}
