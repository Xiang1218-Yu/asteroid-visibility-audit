#!/usr/bin/env python3
"""Incomplete starter: intentionally omits orbital propagation and station geometry."""
import argparse
import json
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ledger", required=True)
    args = parser.parse_args(argv)
    try:
        raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
        names = sorted(station["id"] for station in raw["stations"])
        report = {
            "closest_approach_s": raw["window"]["start_s"],
            "closest_distance_km": raw["orbit"]["a_km"],
            "earth_risk_status": "CLEAR",
            "sample_times_s": [raw["window"]["start_s"], raw["window"]["end_s"]],
            "stations": [{"id": name, "status": "NOT_VISIBLE", "windows": []} for name in names],
            "union_status": "NO_TRACK",
        }
        Path(args.output).write_text(json.dumps(report), encoding="utf-8")
        Path(args.ledger).write_text("{}\n", encoding="utf-8")
        return 0
    except Exception:
        print("REJECT INVALID_INPUT", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
