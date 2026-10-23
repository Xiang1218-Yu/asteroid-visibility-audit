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

write_zero() {
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

write_zero || exit 0
TMP=$(mktemp -d) || exit 0
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
python3 -m venv "$TMP/venv" || exit 0
"$TMP/venv/bin/python" -m pip install --no-index --find-links "$TESTS_DIR/wheels" --require-hashes -r "$TESTS_DIR/requirements-test.txt" || exit 0
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$TMP/venv/bin/python" -m pytest --collect-only -q "$TESTS_DIR/test_visibility.py" >"$TMP/collect.txt" 2>&1 || exit 0
set +e
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$TMP/venv/bin/python" -m pytest -p no:cacheprovider -q "$TESTS_DIR/test_visibility.py" --junitxml="$TMP/results.xml" >"$TMP/pytest.log" 2>&1
status=$?
set -e
cat "$TMP/pytest.log"

python3 - "$TMP/results.xml" "$REWARD_FILE" "$status" <<'SCOREPY'
import math
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

GROUPS = {
    "orbital_dynamics": {
        "test_kepler_propagation_uses_eccentric_periapsis_distance",
        "test_midpoint_risk_candidate_detects_between_sample_close_approach",
        "test_inclination_rotation_and_obliquity_control_station_altitude",
    },
    "observing_geometry": {
        "test_earth_occultation_overrides_permissive_horizon_limit",
        "test_near_horizon_refraction_can_meet_apparent_altitude_limit",
        "test_solar_avoidance_rejects_an_otherwise_high_target",
        "test_final_nonintegral_sampling_endpoint_is_included",
    },
    "network_decision": {
        "test_short_visible_window_is_brief_not_trackable",
        "test_widely_separated_qualified_stations_enable_stereo_tracking",
        "test_narrow_station_separation_is_not_stereo_tracking",
    },
    "integrity": {
        "test_duplicate_station_identifier_is_rejected_without_side_effects",
        "test_earth_intersecting_orbit_is_rejected_without_side_effects",
        "test_compact_sorted_output_has_required_public_fields",
        "test_same_canonical_input_deduplicates_ledger_event",
        "test_corrupt_ledger_is_rejected_without_side_effects",
        "test_directory_ledger_target_is_rejected_without_partial_report_update",
        "test_repeated_success_is_byte_stable_and_leaves_no_temp_files",
    },
}
WEIGHTS = {
    "orbital_dynamics": 0.38,
    "observing_geometry": 0.29,
    "network_decision": 0.21,
    "integrity": 0.12,
}


def atomic_reward(path: Path, score: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.score-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{score:.6f}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


score = 0.0
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
    expected = set().union(*GROUPS.values())
    if set(outcomes) != expected:
        raise RuntimeError("JUnit test set is incomplete or unexpected")
    if set(GROUPS) != set(WEIGHTS) or not math.isclose(sum(WEIGHTS.values()), 1.0, abs_tol=1e-12):
        raise RuntimeError("invalid group weights")
    group_passes = {group: sum(outcomes[name] for name in names) for group, names in GROUPS.items()}
    score = sum(
        WEIGHTS[group] * group_passes[group] / len(names)
        for group, names in GROUPS.items()
    )
    # A no-op must not receive incidental credit from generic geometry or
    # filesystem checks. At least one orbital and one network-decision
    # contract must pass before partial credit is meaningful.
    if group_passes["orbital_dynamics"] == 0 or group_passes["network_decision"] == 0:
        score = 0.0
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise RuntimeError("non-finite score")
except Exception:
    score = 0.0
atomic_reward(Path(sys.argv[2]), score)
SCOREPY
# A valid test run may contain candidate failures; Harbor consumes reward.txt.
exit 0
