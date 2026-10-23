#!/bin/sh
# The verifier always starts from a fail-closed reward. Candidate failures are
# ordinary benchmark results; verifier/infrastructure failures remain zero.
set -u
umask 077

TRUSTED_PYTHON=/usr/local/bin/python3
TRUSTED_TIMEOUT=/usr/bin/timeout
TESTS_DIR=$(CDPATH= cd -- "$(/usr/bin/dirname -- "$0")" && pwd)
if [ "$TESTS_DIR" = "/tests" ]; then
  REWARD_FILE=${REWARD_FILE:-/logs/verifier/reward.txt}
  CANDIDATE_DIR=${PROGRAM_DIR:-/app}
else
  ROOT=$(CDPATH= cd -- "$TESTS_DIR/.." && pwd)
  REWARD_FILE=${REWARD_FILE:-"$ROOT/reward.txt"}
  CANDIDATE_DIR=${PROGRAM_DIR:-"$ROOT/environment"}
fi

write_reward_zero() {
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp TMPDIR=/tmp \
    "$TRUSTED_PYTHON" -S -s -P -B - "$REWARD_FILE" <<'PY'
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.parent / f".{path.name}.zero-{os.getpid()}"
try:
    with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("0.000000\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
PY
}

finish_zero() {
  write_reward_zero || true
  /bin/chmod 0444 "$REWARD_FILE" 2>/dev/null || true
  exit 0
}

write_reward_zero || exit 0
TMP=$(/usr/bin/mktemp -d) || finish_zero
SITE_PACKAGES="$TMP/site"
trap '/bin/rm -rf "$TMP"' EXIT HUP INT TERM

# Wheels are extracted by the fixed interpreter. No pip, user site, startup
# hook, PATH lookup, or candidate directory participates in verifier imports.
env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$TMP" TMPDIR=/tmp \
  "$TRUSTED_PYTHON" -S -s -P -B - "$TESTS_DIR/wheels" "$SITE_PACKAGES" <<'PY' || finish_zero
from pathlib import Path
import sys
import zipfile

wheels, target = map(Path, sys.argv[1:])
target.mkdir(parents=True, exist_ok=True)
for wheel in sorted(wheels.glob("*.whl")):
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(target)
PY

# Record discovery through the same trusted, isolated import route used below.
env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$TMP" TMPDIR=/tmp \
  PROGRAM_DIR="$CANDIDATE_DIR" \
  PYTHONPATH="$SITE_PACKAGES:/tests" \
  PYTHONNOUSERSITE=1 \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  "$TRUSTED_PYTHON" -S -s -P -B -m pytest \
  -p no:cacheprovider --collect-only -q "$TESTS_DIR/test_visibility.py" \
  >"$TMP/collect.txt" 2>&1 || finish_zero
cat "$TMP/collect.txt"

set +e
env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$TMP" TMPDIR=/tmp \
  PROGRAM_DIR="$CANDIDATE_DIR" \
  PYTHONPATH="$SITE_PACKAGES:/tests" \
  PYTHONNOUSERSITE=1 \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  "$TRUSTED_TIMEOUT" --signal=TERM --kill-after=10s 1500s \
  "$TRUSTED_PYTHON" -S -s -P -B -m pytest \
  -p no:cacheprovider -q "$TESTS_DIR/test_visibility.py" \
  --junitxml="$TMP/results.xml" >"$TMP/pytest.log" 2>&1
pytest_status=$?
set -e
cat "$TMP/pytest.log"

if ! test -s "$TMP/results.xml"; then
  echo "INCOMPLETE missing or empty JUnit report" >&2
  finish_zero
fi

# A nonempty file is not sufficient: reject malformed or non-JUnit XML before scoring.
env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$TMP" TMPDIR=/tmp \
  "$TRUSTED_PYTHON" -S -s -P -B - "$TMP/results.xml" <<'PY' || finish_zero
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
if root.tag not in {"testsuite", "testsuites"} or not root.findall(".//testcase"):
    raise ValueError("JUnit has no test cases")
PY

set +e
env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$TMP" TMPDIR=/tmp \
  PYTHONPATH="$SITE_PACKAGES:/tests" \
  PYTHONNOUSERSITE=1 \
  "$TRUSTED_PYTHON" -S -s -P -B "$TESTS_DIR/score.py" \
  --config "$TESTS_DIR/scoring.json" \
  --junit "$TMP/results.xml" \
  --report-out "$TMP/scoring-report.json" \
  --reward-out "$REWARD_FILE"
scoring_status=$?
set -e
if [ "$scoring_status" -ne 0 ] || ! test -s "$TMP/scoring-report.json" || ! test -s "$REWARD_FILE"; then
  echo "INCOMPLETE weighted scoring failed" >&2
  finish_zero
fi

if [ "$pytest_status" -ne 0 ] && [ "$pytest_status" -ne 1 ]; then
  echo "INCOMPLETE pytest did not complete a normal run" >&2
  finish_zero
fi

/bin/chmod 0444 "$REWARD_FILE" 2>/dev/null || true
exit 0
