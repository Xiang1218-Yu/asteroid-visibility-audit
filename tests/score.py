#!/usr/bin/env python3
"""Compute a fail-closed weighted reward from a completed pytest JUnit run."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


class ScoringError(ValueError):
    """The scoring configuration or execution report is not trustworthy."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ScoringError(f"duplicate JSON key: {key}")
        payload[key] = value
    return payload


def load_config(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScoringError(f"scoring config unreadable: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ScoringError("scoring config schema_version must be 1")
    gates = raw.get("gate_tests")
    tests = raw.get("tests")
    if not isinstance(gates, list) or not gates:
        raise ScoringError("gate_tests must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in gates):
        raise ScoringError("gate_tests entries must be non-empty strings")
    if len(set(gates)) != len(gates):
        raise ScoringError("gate_tests must not contain duplicates")
    if not isinstance(tests, dict) or not tests:
        raise ScoringError("tests must be a non-empty object")
    if set(gates) & set(tests):
        raise ScoringError("gate and scored tests must be disjoint")

    normalized: dict[str, dict[str, Any]] = {}
    for case in tests:
        if not isinstance(case, str) or "::" not in case:
            raise ScoringError("scored test IDs must use file.py::test")
        entry = tests[case]
        if not isinstance(entry, dict):
            raise ScoringError(f"scored entry must be an object: {case}")
        weight = entry.get("weight")
        rationale = entry.get("rationale")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise ScoringError(f"weight must be numeric: {case}")
        weight = float(weight)
        if not math.isfinite(weight) or weight <= 0.0 or weight > 1.0:
            raise ScoringError(f"weight must be in (0, 1]: {case}")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ScoringError(f"weight rationale is required: {case}")
        normalized[case] = {"weight": weight, "rationale": rationale.strip()}
    total = math.fsum(item["weight"] for item in normalized.values())
    if abs(total - 1.0) > 1e-9:
        raise ScoringError(f"scored weights must sum to 1, got {total:.12f}")
    return {"gate_tests": list(gates), "tests": normalized}


def expected_test_name(case_id: str) -> str:
    parts = case_id.split("::")
    if len(parts) != 2 or not parts[0].endswith(".py") or not parts[1]:
        raise ScoringError(f"invalid test ID: {case_id}")
    return parts[1]


def load_outcomes(path: Path) -> dict[str, bool]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ScoringError(f"JUnit report unreadable: {exc}") from exc
    outcomes: dict[str, bool] = {}
    for node in root.findall(".//testcase"):
        name = node.get("name", "")
        if not name or "[" in name or "]" in name:
            raise ScoringError("JUnit contains an unnamed or parameterized test")
        if name in outcomes:
            raise ScoringError(f"JUnit contains duplicate test: {name}")
        outcomes[name] = all(
            node.find(tag) is None for tag in ("failure", "error", "skipped")
        )
    return outcomes


def build_result(config_path: Path, junit_path: Path) -> dict[str, Any]:
    config = load_config(config_path)
    outcomes = load_outcomes(junit_path)
    ordered = config["gate_tests"] + list(config["tests"])
    names = [expected_test_name(case) for case in ordered]
    if len(set(names)) != len(names):
        raise ScoringError("test names in scoring config are not unique")
    if set(outcomes) != set(names):
        missing = sorted(set(names) - set(outcomes))
        unexpected = sorted(set(outcomes) - set(names))
        raise ScoringError(
            f"test set mismatch; missing={missing}, unexpected={unexpected}"
        )
    gate_passed = all(outcomes[expected_test_name(case)] for case in config["gate_tests"])
    if not gate_passed:
        reward = 0.0
    else:
        reward = math.fsum(
            item["weight"]
            for case, item in config["tests"].items()
            if outcomes[expected_test_name(case)]
        )
    reward = float(round(reward, 6))
    if not math.isfinite(reward) or not 0.0 <= reward <= 1.0:
        raise ScoringError("computed reward is outside [0, 1]")
    return {
        "status": "COMPLETED",
        "reward": reward,
        "gate_passed": gate_passed,
        "tests_total": len(ordered),
        "tests_passed": sum(outcomes.values()),
    }


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def incomplete_result(message: str) -> dict[str, Any]:
    return {
        "status": "INCOMPLETE",
        "reward": 0.0,
        "gate_passed": False,
        "error": message,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--junit", required=True)
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--reward-out", required=True)
    args = parser.parse_args(argv)

    report_path = Path(args.report_out)
    reward_path = Path(args.reward_out)
    try:
        result = build_result(Path(args.config), Path(args.junit))
    except (ScoringError, OSError, ValueError) as exc:
        result = incomplete_result(str(exc))
        print(f"INCOMPLETE {exc}", file=sys.stderr)
    reward_text = "0.000000\n" if result["status"] != "COMPLETED" else f"{result['reward']:.6f}\n"
    atomic_write(reward_path, reward_text)
    atomic_write(
        report_path,
        json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n",
    )
    return 0 if result["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
