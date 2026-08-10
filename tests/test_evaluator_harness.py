from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


HARNESS = Path(__file__).resolve().parents[1] / "evaluator" / "harness.py"


def _sealed_fixture(tmp_path: Path, body: str) -> tuple[Path, Path]:
    bundle = tmp_path / "sealed"
    run_dir = tmp_path / "run"
    bundle.mkdir()
    run_dir.mkdir()
    entrypoint = bundle / "grade.py"
    entrypoint.write_text(body, encoding="utf-8")
    entrypoint.chmod(0o400)
    bundle.chmod(0o500)
    run_dir.chmod(0o500)
    return bundle, run_dir


def _restore(*paths: Path) -> None:
    for path in paths:
        path.chmod(0o700)


def test_harness_projects_only_public_score_fields(tmp_path: Path):
    bundle, run_dir = _sealed_fixture(
        tmp_path,
        """\
import argparse, json
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--run-dir', required=True)
p.add_argument('--output', required=True)
a=p.parse_args()
assert Path(a.run_dir).is_dir()
Path(a.output).write_text(json.dumps({
  'schema_version':'1.0', 'task_id':'fixture', 'status':'scored',
  'overall':0.75,
  'by_type':{
    'objective':{
      'earned':0.3, 'weight':0.4, 'normalized':0.75,
      'evidence':'EVALUATOR_PRIVATE_NESTED_TEXT'
    }
  },
  'validity':{
    'ok':False,
    'failures':['EVALUATOR_PRIVATE_FAILURE_TEXT'],
    'detail':'EVALUATOR_PRIVATE_VALIDITY_TEXT'
  },
  'checkpoints':[{'evaluator_only':'EVALUATOR_PRIVATE_CHECKPOINT_TEXT'}]
}))
""",
    )
    output = tmp_path / "public" / "score.json"
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(HARNESS),
                "--bundle",
                str(bundle),
                "--run-dir",
                str(run_dir),
                "--output",
                str(output),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        score = json.loads(output.read_text(encoding="utf-8"))
        assert score["overall"] == 0.75
        assert score["by_type"] == {
            "objective": {"earned": 0.3, "weight": 0.4, "normalized": 0.75}
        }
        assert score["validity"] == {"ok": False, "failure_count": 1}
        assert "checkpoints" not in score
        rendered = output.read_text(encoding="utf-8")
        assert "EVALUATOR_PRIVATE" not in rendered
        assert "evidence" not in rendered
        assert "failures" not in rendered
        assert "detail" not in rendered
    finally:
        _restore(bundle, run_dir)


def test_harness_rejects_nested_text_in_numeric_score_fields(tmp_path: Path):
    private_marker = "EVALUATOR_PRIVATE_NUMERIC_FIELD"
    bundle, run_dir = _sealed_fixture(
        tmp_path,
        f"""\
import argparse, json
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--run-dir', required=True)
p.add_argument('--output', required=True)
a=p.parse_args()
Path(a.output).write_text(json.dumps({{
  'schema_version':'1.0', 'task_id':'fixture', 'status':'scored',
  'overall':0.75,
  'by_type':{{'objective':{{
    'earned':'{private_marker}', 'weight':0.4, 'normalized':0.75
  }}}},
  'validity':{{'ok':True, 'failures':[]}}
}}))
""",
    )
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(HARNESS),
                "--bundle",
                str(bundle),
                "--run-dir",
                str(run_dir),
                "--output",
                str(tmp_path / "score.json"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode != 0
        assert private_marker not in completed.stdout
        assert private_marker not in completed.stderr
        assert not (tmp_path / "score.json").exists()
    finally:
        _restore(bundle, run_dir)


def test_harness_detects_participant_tree_mutation(tmp_path: Path):
    bundle, run_dir = _sealed_fixture(
        tmp_path,
        """\
import argparse, json, os
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--run-dir', required=True)
p.add_argument('--output', required=True)
a=p.parse_args()
run=Path(a.run_dir)
run.chmod(0o700)
(run/'grader-was-here.txt').write_text('mutation')
run.chmod(0o500)
Path(a.output).write_text(json.dumps({
  'schema_version':'1.0', 'task_id':'fixture', 'status':'scored',
  'overall':0.0,
  'by_type':{'objective':{'earned':0.0,'weight':1.0,'normalized':0.0}},
  'validity':{'ok':True, 'failures':[]}
}))
""",
    )
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(HARNESS),
                "--bundle",
                str(bundle),
                "--run-dir",
                str(run_dir),
                "--output",
                str(tmp_path / "score.json"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode != 0
        assert "modified an input tree" in completed.stderr
        assert not (tmp_path / "score.json").exists()
    finally:
        _restore(bundle, run_dir)


def test_harness_detects_sealed_bundle_mutation(tmp_path: Path):
    bundle, run_dir = _sealed_fixture(
        tmp_path,
        """\
import argparse, json
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--run-dir', required=True)
p.add_argument('--output', required=True)
a=p.parse_args()
bundle=Path(__file__).resolve().parent
bundle.chmod(0o700)
(bundle/'private-mutation.txt').write_text('mutation')
bundle.chmod(0o500)
Path(a.output).write_text(json.dumps({
  'schema_version':'1.0', 'task_id':'fixture', 'status':'scored',
  'overall':0.0,
  'by_type':{'objective':{'earned':0.0,'weight':1.0,'normalized':0.0}},
  'validity':{'ok':True, 'failures':[]}
}))
""",
    )
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(HARNESS),
                "--bundle",
                str(bundle),
                "--run-dir",
                str(run_dir),
                "--output",
                str(tmp_path / "score.json"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode != 0
        assert "modified an input tree" in completed.stderr
        assert not (tmp_path / "score.json").exists()
    finally:
        _restore(bundle, run_dir)


def test_harness_does_not_echo_failed_grader_output(tmp_path: Path):
    bundle, run_dir = _sealed_fixture(
        tmp_path,
        "raise SystemExit('EVALUATOR_PRIVATE_FAILURE_DETAIL')\n",
    )
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(HARNESS),
                "--bundle",
                str(bundle),
                "--run-dir",
                str(run_dir),
                "--output",
                str(tmp_path / "score.json"),
            ],
            text=True,
            capture_output=True,
            check=False,
            env={"PATH": os.environ.get("PATH", "")},
        )
        assert completed.returncode != 0
        assert "EVALUATOR_PRIVATE_FAILURE_DETAIL" not in completed.stdout
        assert "EVALUATOR_PRIVATE_FAILURE_DETAIL" not in completed.stderr
    finally:
        _restore(bundle, run_dir)
