#!/bin/sh
# The verifier result is the reward file; candidate test failures are ordinary
# benchmark outcomes, not verifier crashes. Every other path leaves an atomic
# zero reward behind.
set -u
umask 077
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

# Fail closed before any dependency or test work begins. Later writes replace
# this value atomically only after a complete, valid test run.
rm -f "$REWARD_FILE" 2>/dev/null || true
mkdir -p "$(dirname "$REWARD_FILE")" 2>/dev/null || true
echo "0.000000" >"$REWARD_FILE" 2>/dev/null || exit 0

write_reward_zero() {
  python3 - "$REWARD_FILE" <<'PY'
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.parent / f".{path.name}.zero-{os.getpid()}"
try:
    with open(tmp, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("0.000000\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
finally:
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
PY
}

write_reward_zero || exit 0
TMP=$(mktemp -d) || exit 0
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
python3 -m venv "$TMP/venv" || exit 0
"$TMP/venv/bin/python" -m pip install --no-index --find-links "$TESTS_DIR/wheels" --require-hashes -r "$TESTS_DIR/requirements-test.txt" || exit 0
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$TMP/venv/bin/python" -m pytest --collect-only -q "$TESTS_DIR/test_visibility.py" >"$TMP/collect.txt" 2>&1 || exit 0
set +e
if command -v timeout >/dev/null 2>&1; then
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 timeout --signal=TERM --kill-after=10 1500 "$TMP/venv/bin/python" -m pytest -p no:cacheprovider -q "$TESTS_DIR/test_visibility.py" --junitxml="$TMP/results.xml" >"$TMP/pytest.log" 2>&1
else
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$TMP/venv/bin/python" -m pytest -p no:cacheprovider -q "$TESTS_DIR/test_visibility.py" --junitxml="$TMP/results.xml" >"$TMP/pytest.log" 2>&1
fi
status=$?
set -e
cat "$TMP/pytest.log"

if ! test -s "$TMP/results.xml"; then
  echo "INCOMPLETE missing or empty JUnit report" >&2
  write_reward_zero || exit 0
  chmod 0444 "$REWARD_FILE" 2>/dev/null || true
  exit 0
fi

python3 - "$TMP/results.xml" "$REWARD_FILE" "$status" <<'SCOREPY'
import math
import os
import sys
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

# Machine-checkable per-test weights.  Each test has one primary behavioral
# group; group totals preserve the published 0.38/0.29/0.21/0.12 model.
CASES = {
    "test_kepler_propagation_uses_eccentric_periapsis_distance": ("ASV-R2-01", "orbital_dynamics", Decimal("0.126667")),
    "test_midpoint_risk_candidate_detects_between_sample_close_approach": ("ASV-R3-01", "orbital_dynamics", Decimal("0.126667")),
    "test_inclination_rotation_and_obliquity_control_station_altitude": ("ASV-R2-R4-01", "orbital_dynamics", Decimal("0.126666")),
    "test_earth_occultation_overrides_permissive_horizon_limit": ("ASV-R4-01", "observing_geometry", Decimal("0.072500")),
    "test_near_horizon_refraction_can_meet_apparent_altitude_limit": ("ASV-R4-02", "observing_geometry", Decimal("0.072500")),
    "test_solar_avoidance_rejects_an_otherwise_high_target": ("ASV-R4-03", "observing_geometry", Decimal("0.072500")),
    "test_final_nonintegral_sampling_endpoint_is_included": ("ASV-R3-02", "observing_geometry", Decimal("0.072500")),
    "test_short_visible_window_is_brief_not_trackable": ("ASV-R5-01", "network_decision", Decimal("0.070000")),
    "test_widely_separated_qualified_stations_enable_stereo_tracking": ("ASV-R5-02", "network_decision", Decimal("0.070000")),
    "test_narrow_station_separation_is_not_stereo_tracking": ("ASV-R5-03", "network_decision", Decimal("0.070000")),
    "test_duplicate_station_identifier_is_rejected_without_side_effects": ("ASV-R1-R6-01", "integrity", Decimal("0.017143")),
    "test_earth_intersecting_orbit_is_rejected_without_side_effects": ("ASV-R1-R6-02", "integrity", Decimal("0.017143")),
    "test_compact_sorted_output_has_required_public_fields": ("ASV-R6-01", "integrity", Decimal("0.017143")),
    "test_same_canonical_input_deduplicates_ledger_event": ("ASV-R6-02", "integrity", Decimal("0.017143")),
    "test_corrupt_ledger_is_rejected_without_side_effects": ("ASV-R6-03", "integrity", Decimal("0.017143")),
    "test_directory_ledger_target_is_rejected_without_partial_report_update": ("ASV-R6-04", "integrity", Decimal("0.017143")),
    "test_repeated_success_is_byte_stable_and_leaves_no_temp_files": ("ASV-R6-05", "integrity", Decimal("0.017142")),
}
GROUP_WEIGHTS = {
    "orbital_dynamics": Decimal("0.38"),
    "observing_geometry": Decimal("0.29"),
    "network_decision": Decimal("0.21"),
    "integrity": Decimal("0.12"),
}


def atomic_reward(path: Path, credit: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.credit-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{credit:.6f}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


credit = 0.0
try:
    # pytest uses 0 for all pass and 1 for ordinary assertion failures. Any
    # other code is a collection/internal interruption and is fail-closed.
    if int(sys.argv[3]) not in (0, 1):
        raise RuntimeError("pytest did not complete a normal test run")
    root = ET.parse(sys.argv[1]).getroot()
    outcomes = {}
    for case in root.findall(".//testcase"):
        name = case.attrib.get("name", "")
        if not name or name in outcomes:
            raise RuntimeError("JUnit test set is invalid")
        outcomes[name] = all(case.find(tag) is None for tag in ("failure", "error", "skipped"))
    expected = set(CASES)
    if set(outcomes) != expected:
        raise RuntimeError("JUnit test set is incomplete or unexpected")
    if not math.isclose(float(sum((case[2] for case in CASES.values()), Decimal("0"))), 1.0, abs_tol=1e-12):
        raise RuntimeError("per-test weights do not sum to one")
    for group, expected_weight in GROUP_WEIGHTS.items():
        group_weight = sum((case[2] for case in CASES.values() if case[1] == group), Decimal("0"))
        if group_weight != expected_weight:
            raise RuntimeError("group weight mismatch")
    credit = float(sum((case[2] for name, case in CASES.items() if outcomes[name]), Decimal("0")))
    group_passes = {
        group: sum(outcomes[name] for name, case in CASES.items() if case[1] == group)
        for group in GROUP_WEIGHTS
    }
    # A no-op must not receive incidental credit from generic geometry or
    # filesystem checks. At least one orbital and one network-decision
    # contract must pass before partial credit is meaningful.
    if group_passes["orbital_dynamics"] == 0 or group_passes["network_decision"] == 0:
        credit = 0.0
    if not math.isfinite(credit) or not 0.0 <= credit <= 1.0:
        raise RuntimeError("non-finite credit")
except Exception:
    print("INCOMPLETE scoring rejected", file=sys.stderr)
    credit = 0.0
atomic_reward(Path(sys.argv[2]), credit)
SCOREPY
# A valid test run may contain candidate failures; Harbor consumes reward.txt.
chmod 0444 "$REWARD_FILE" 2>/dev/null || true
exit 0
