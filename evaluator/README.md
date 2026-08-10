# Evaluator boundary

This directory defines the public handoff to the official evaluator. The
participant image does not copy this directory.

Task-specific graders, rubrics, requester profiles, reference solutions, and
partial solutions are evaluator-only assets and are not present in this public
staging tree. The official evaluator receives a read-only task bundle and a
read-only participant run directory after the worker container exits. A sealed
task bundle exposes a `grade.py --run-dir ... --output ...` entry point. The
generic harness runs it in a private temporary directory and projects only the
public score schema. The projection contains only the allowlisted task/status
identifiers, overall score, numeric per-type scores, and a boolean/count
validity summary. Checkpoint evidence, failure messages, arbitrary nested text,
and every other evaluator field are discarded.

The harness verifies content-tree checksums for both inputs before and after the
grader process and fails if either tree changes. It also rejects writable input
roots, symlinks, special files, overlapping input trees, and a score output path
inside either input. These are validation and tamper-detection checks; this
Python process is **not a security sandbox**. File modes alone do not make data
immutable to a process running as the same OS owner.

The benchmark operator is responsible for running the grader with actual OS or
container isolation: mount the sealed bundle and participant results read-only,
write the projected score to a separate location, disable grader network
access, use an identity that cannot remount or chmod the inputs, and apply
appropriate process/resource limits. A production sealed bundle and that
operator-side isolation are supplied separately; neither is part of the
participant image.
