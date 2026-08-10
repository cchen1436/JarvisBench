from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

from jarvisbench.core.privacy import scan_release_tree
from jarvisbench.core.providers import read_secret, resolve_secret_file
from jarvisbench.settings.multi_agent import MultiAgentSetting
from jarvisbench.settings.single_agent import SingleAgentSetting
from jarvisbench.tracks.user_interaction import DeterministicReplayResponder, UserInteractionTrack


def _setting(name: str):
    return SingleAgentSetting() if name == "single_agent" else MultiAgentSetting()


def _validate(args: argparse.Namespace) -> int:
    findings = scan_release_tree(args.root)
    result = {"ok": not findings, "findings": findings}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not findings else 1


def _dry_run(args: argparse.Namespace) -> int:
    setting = _setting(args.setting)
    result = setting.dry_run()
    payload = {
        "setting": result.setting,
        "track": args.track,
        "controller": args.controller,
        "execution_nodes": result.execution_nodes,
        "manager_is_jarvis": result.manager_is_jarvis,
        "gateway_required": result.gateway_required,
        "mutates_worker": args.track == "agent_collaboration" and args.controller != "none",
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _replay(args: argparse.Namespace) -> int:
    track = UserInteractionTrack(DeterministicReplayResponder())
    turns = track.run(args.trace, args.output)
    print(json.dumps({"ok": True, "turns": len(turns), "output": str(args.output)}, sort_keys=True))
    return 0


def _requester_context_loader(path: Path):
    source = path.resolve(strict=True)
    if path.is_symlink() or not source.is_file() or source.stat().st_size > 128 * 1024:
        raise ValueError("requester context must be a bounded regular file")

    def load() -> str:
        # Load lazily only when Jarvis spends requester attention. The value is
        # never included in run manifests, diagnostics, or worker-visible state.
        return source.read_text(encoding="utf-8")

    return load


def _run_episode(args: argparse.Namespace) -> int:
    if args.setting != "multi_agent" or args.track != "agent_collaboration":
        raise SystemExit(
            "the executable release backend currently covers multi_agent + "
            "agent_collaboration; use replay for Track 2 and the public worker "
            "port for single_agent"
        )
    if not args.worker_model or not args.provider_base_url:
        raise SystemExit("worker model and provider base URL must be configured explicitly")
    if not os.environ.get(args.worker_api_key_env, ""):
        try:
            worker_key_file = resolve_secret_file(
                file_env="JARVISBENCH_WORKER_API_KEY_FILE",
                explicit_file=args.worker_api_key_file,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if worker_key_file is None:
            raise SystemExit(
                f"worker credential is unavailable: {args.worker_api_key_env} "
                "or --worker-api-key-file"
            )

    # Imports are lazy so baseline configuration, Track 2 replay, and release
    # validation never initialize OpenClaw or a provider client.
    from jarvisbench.reference.dynamic_mas.factory import (
        ReferenceDynamicMasConfig,
        build_reference_scheduler,
    )
    from jarvisbench.settings.multi_agent_runtime import (
        MultiAgentRuntime,
        MultiAgentRuntimeConfig,
    )

    project_id = args.project_id or f"episode-{uuid.uuid4().hex}"
    scheduler = None
    if args.controller == "reference":
        if not args.jarvis_model or not args.user_model or args.requester_context is None:
            raise SystemExit(
                "reference control requires Jarvis model, user model, and a private requester context"
            )
        try:
            reference_key = read_secret(
                value_env="JARVISBENCH_API_KEY",
                file_env="JARVISBENCH_API_KEY_FILE",
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if not reference_key:
            raise SystemExit(
                "a JARVISBENCH_API_KEY value or JARVISBENCH_API_KEY_FILE is required "
                "by the reference controller"
            )
        scheduler = build_reference_scheduler(
            project_id=project_id,
            control_root=args.episode_root / "private" / "control",
            private_ledger_path=(
                args.episode_root / "private" / "requester" / "decision_ledger.jsonl"
            ),
            requester_context_loader=_requester_context_loader(args.requester_context),
            config=ReferenceDynamicMasConfig(
                jarvis_model=args.jarvis_model,
                user_model=args.user_model,
                jarvis_reasoning=args.jarvis_reasoning,
            ),
        )
    runtime = MultiAgentRuntime(
        MultiAgentRuntimeConfig(
            task_dir=args.task_dir,
            episode_root=args.episode_root,
            project_id=project_id,
            worker_model=args.worker_model,
            provider_base_url=args.provider_base_url,
            api_key_env=args.worker_api_key_env,
            api_key_file=args.worker_api_key_file,
            thinking=args.worker_thinking,
            plugin_dir=args.plugin_dir,
        ),
        scheduler=scheduler,
    )
    result = runtime.run()
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0 if result.status == "completed" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvisbench")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="scan a release tree for privacy-boundary violations")
    validate.add_argument("--root", type=Path, default=Path.cwd())
    validate.set_defaults(func=_validate)

    dry = sub.add_parser("dry-run", help="resolve one setting × track without a model call")
    dry.add_argument("--setting", choices=("single_agent", "multi_agent"), required=True)
    dry.add_argument("--track", choices=("agent_collaboration", "user_interaction"), required=True)
    dry.add_argument("--controller", choices=("none", "reference", "external"), default="none")
    dry.set_defaults(func=_dry_run)

    replay = sub.add_parser("replay", help="run the isolated Track 2 text replay smoke")
    replay.add_argument("--trace", type=Path, required=True)
    replay.add_argument("--output", type=Path, required=True)
    replay.set_defaults(func=_replay)

    run = sub.add_parser("run", help="launch one configured benchmark episode")
    run.add_argument("--setting", choices=("single_agent", "multi_agent"), required=True)
    run.add_argument(
        "--track", choices=("agent_collaboration", "user_interaction"), required=True
    )
    run.add_argument("--controller", choices=("none", "reference"), default="none")
    run.add_argument("--task-dir", type=Path, required=True)
    run.add_argument("--episode-root", type=Path, required=True)
    run.add_argument("--project-id", default="")
    run.add_argument("--worker-model", default=os.environ.get("JARVISBENCH_WORKER_MODEL", ""))
    run.add_argument(
        "--worker-thinking",
        choices=("provider_default", "off", "low", "medium", "high"),
        default="provider_default",
    )
    run.add_argument(
        "--provider-base-url", default=os.environ.get("JARVISBENCH_API_BASE", "")
    )
    run.add_argument("--worker-api-key-env", default="JARVISBENCH_WORKER_API_KEY")
    run.add_argument(
        "--worker-api-key-file",
        type=Path,
        default=(
            Path(os.environ["JARVISBENCH_WORKER_API_KEY_FILE"])
            if os.environ.get("JARVISBENCH_WORKER_API_KEY_FILE")
            else None
        ),
    )
    run.add_argument("--jarvis-model", default=os.environ.get("JARVISBENCH_JARVIS_MODEL", ""))
    run.add_argument("--user-model", default=os.environ.get("JARVISBENCH_USER_MODEL", ""))
    run.add_argument(
        "--jarvis-reasoning",
        choices=("off", "low", "medium", "high"),
        default="medium",
    )
    run.add_argument("--requester-context", type=Path)
    run.add_argument("--plugin-dir", type=Path)
    run.set_defaults(func=_run_episode)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
