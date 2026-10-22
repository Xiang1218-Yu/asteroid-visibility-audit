#!/bin/sh
set -u
export PYTHONDONTWRITEBYTECODE=1
TESTS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$TESTS_DIR" = "/tests" ]; then
  REWARD_FILE=${REWARD_FILE:-/logs/verifier/reward.txt}
  export PROGRAM_DIR=${PROGRAM_DIR:-/app}
else
  ROOT=$(CDPATH= cd -- "$TESTS_DIR/.." && pwd)
  REWARD_FILE=${REWARD_FILE:-"$ROOT/reward.txt"}
  export PROGRAM_DIR=${PROGRAM_DIR:-"$ROOT/environment"}
fi
mkdir -p "$(dirname -- "$REWARD_FILE")"
printf '0.000000\n' > "$REWARD_FILE"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
python3 -m venv "$TMP/venv" || exit 1
"$TMP/venv/bin/python" -m pip install --no-index --find-links "$TESTS_DIR/wheels" --require-hashes -r "$TESTS_DIR/requirements-test.txt" || exit 1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$TMP/venv/bin/python" -m pytest --collect-only -q "$TESTS_DIR/test_visibility.py" >"$TMP/collect.txt" 2>&1 || exit 1
set +e
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$TMP/venv/bin/python" -m pytest -p no:cacheprovider -q "$TESTS_DIR/test_visibility.py" --junitxml="$TMP/results.xml"
status=$?
set -e
python3 - "$TMP/results.xml" "$REWARD_FILE" "$status" <<'SCOREPY'
import sys
import xml.etree.ElementTree as ET

GROUPS = {
    "geometry": {
        "test_sidereal_rotation_changes_visibility_within_window",
        "test_spherical_altitude_uses_latitude_declination_and_hour_angle",
        "test_equality_at_altitude_and_solar_threshold_is_visible",
    },
    "solar_windows": {
        "test_solar_avoidance_blocks_high_altitude_target",
        "test_final_irregular_sample_and_visibility_gap_split_windows",
        "test_minimum_window_yields_brief_status",
    },
    "rejection": {
        "test_boolean_and_extra_key_rejection_preserve_two_outputs",
        "test_nonfinite_and_range_rejection_preserve_two_outputs",
        "test_corrupt_ledger_preserves_report_ledger_and_directory",
        "test_output_target_rejection_preserves_report_ledger_and_directory",
    },
    "durability": {
        "test_report_is_compact_sorted_lf_and_rounded",
        "test_same_input_deduplicates_canonical_ledger_event",
        "test_distinct_input_appends_ledger_event",
        "test_success_is_byte_stable_without_temp_files",
    },
}
WEIGHTS = {"geometry": 0.35, "solar_windows": 0.27, "rejection": 0.22, "durability": 0.16}

score = 0.0
try:
    if int(sys.argv[3]) != 0:
        raise RuntimeError("test execution failed")
    root = ET.parse(sys.argv[1]).getroot()
    outcomes = {}
    for case in root.findall(".//testcase"):
        name = case.attrib.get("name", "")
        outcomes[name] = all(case.find(tag) is None for tag in ("failure", "error", "skipped"))
    expected = set().union(*GROUPS.values())
    if set(outcomes) != expected or not all(outcomes.values()):
        raise RuntimeError("test result set is incomplete or non-passing")
    group_score = {
        group: sum(outcomes[name] for name in names) / len(names)
        for group, names in GROUPS.items()
    }
    computed = sum(WEIGHTS[group] * group_score[group] for group in GROUPS)
    if abs(computed - 1.0) > 1e-12:
        raise RuntimeError("unexpected full score")
    score = computed
except Exception:
    score = 0.0
with open(sys.argv[2], "w", encoding="utf-8") as stream:
    stream.write(f"{score:.6f}\n")
SCOREPY
exit "$status"
