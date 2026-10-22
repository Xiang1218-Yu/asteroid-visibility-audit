#!/bin/sh
export PYTHONDONTWRITEBYTECODE=1
set -u
D=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd); REWARD_FILE=${REWARD_FILE:-/logs/verifier/reward.txt}; mkdir -p "$(dirname -- "$REWARD_FILE")"; printf '0.000000\n' > "$REWARD_FILE"; TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT; export PROGRAM_DIR=${PROGRAM_DIR:-/app}
python3 -m venv "$TMP/v" || exit 1
"$TMP/v/bin/python" -m pip install --no-index --find-links "$D/wheels" --require-hashes -r "$D/requirements-test.txt" || exit 1
set +e
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$TMP/v/bin/python" -m pytest -q "$D/test_visibility.py" --junitxml="$TMP/x.xml"; status=$?
set -e
python3 - "$TMP/x.xml" "$REWARD_FILE" <<'SCOREPY'
import sys,xml.etree.ElementTree as E
g={'geometry':(.35,{'test_sidereal_rotation_changes_visibility_within_window','test_spherical_altitude_uses_latitude_declination_and_hour_angle','test_equality_at_altitude_and_solar_threshold_is_visible'}),'solar_windows':(.27,{'test_solar_avoidance_blocks_high_altitude_target','test_final_irregular_sample_and_visibility_gap_split_windows','test_minimum_window_yields_brief_status'}),'rejection':(.22,{'test_boolean_and_extra_key_rejection_preserve_two_outputs','test_nonfinite_and_range_rejection_preserve_two_outputs','test_corrupt_ledger_preserves_report_ledger_and_directory'}),'durability':(.16,{'test_report_is_compact_sorted_lf_and_rounded','test_same_input_deduplicates_canonical_ledger_event','test_distinct_input_appends_ledger_event','test_success_is_byte_stable_without_temp_files'})}
try:
 c={x.attrib.get('name'):x.find('failure') is None and x.find('error') is None and x.find('skipped') is None for x in E.parse(sys.argv[1]).getroot().findall('.//testcase')};expected=set().union(*(x[1] for x in g.values()));score=sum(w*sum(c[n] for n in tests)/len(tests) for w,tests in g.values()) if set(c)==expected else 0
except Exception:score=0
open(sys.argv[2],'w').write(f'{score:.6f}\n')
SCOREPY
exit "$status"
