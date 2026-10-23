#!/bin/sh
# Candidate failures are ordinary benchmark outcomes. Infrastructure failures
# leave an atomic zero behind and are reported as incomplete.
set -u
umask 077
export PYTHONDONTWRITEBYTECODE=1
TESTS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$TESTS_DIR" = "/tests" ]; then
  REWARD_FILE=${REWARD_FILE:-/logs/verifier/reward.txt}
  export PROGRAM_DIR=${PROGRAM_DIR:-/app}
  SCORER=/tests/score.py
  SCORING=/tests/scoring.json
else
  ROOT=$(CDPATH= cd -- "$TESTS_DIR/.." && pwd)
  REWARD_FILE=${REWARD_FILE:-"$ROOT/reward.txt"}
  export PROGRAM_DIR=${PROGRAM_DIR:-"$ROOT/environment"}
  SCORER="$TESTS_DIR/score.py"
  SCORING="$TESTS_DIR/scoring.json"
fi

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

write_reward_zero || exit 0
TMP=$(mktemp -d) || exit 0
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
python3 -m venv "$TMP/venv" || exit 0
"$TMP/venv/bin/python" -m pip install --no-index --find-links "$TESTS_DIR/wheels" --require-hashes -r "$TESTS_DIR/requirements-test.txt" || exit 0
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$TMP/venv/bin/python" -m pytest --collect-only -q "$TESTS_DIR/test_visibility.py" >"$TMP/collect.txt" 2>&1 || exit 0
cat "$TMP/collect.txt"

set +e
if command -v timeout >/dev/null 2>&1; then
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 timeout --signal=TERM --kill-after=10 1500 "$TMP/venv/bin/python" -m pytest -p no:cacheprovider -q "$TESTS_DIR/test_visibility.py" --junitxml="$TMP/results.xml" >"$TMP/pytest.log" 2>&1
else
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$TMP/venv/bin/python" -m pytest -p no:cacheprovider -q "$TESTS_DIR/test_visibility.py" --junitxml="$TMP/results.xml" >"$TMP/pytest.log" 2>&1
fi
pytest_status=$?
set -e
cat "$TMP/pytest.log"

if ! test -s "$TMP/results.xml"; then
  echo "INCOMPLETE missing or empty JUnit report" >&2
  write_reward_zero || exit 0
  chmod 0444 "$REWARD_FILE" 2>/dev/null || true
  exit 0
fi

set +e
python3 "$SCORER" \
  --config "$SCORING" \
  --junit "$TMP/results.xml" \
  --report-out "$TMP/scoring-report.json" \
  --reward-out "$REWARD_FILE"
scoring_status=$?
set -e
if [ "$scoring_status" -ne 0 ] || ! test -s "$TMP/scoring-report.json" || ! test -s "$REWARD_FILE"; then
  echo "INCOMPLETE weighted scoring failed" >&2
  write_reward_zero || exit 0
fi

if [ "$pytest_status" -ne 0 ] && [ "$pytest_status" -ne 1 ]; then
  echo "INCOMPLETE pytest did not complete a normal run" >&2
  write_reward_zero || exit 0
fi

chmod 0444 "$REWARD_FILE" 2>/dev/null || true
exit 0
