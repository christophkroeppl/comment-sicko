#!/usr/bin/env bash
# End-to-end proof for layers 1 and 2.
#
# Isolated HERMES_HOME, plugin enabled, one long deliberately-confusing prompt
# (three reversals, three cited dead ends). Pass condition is on the filesystem:
# comments the gate would still remove from the produced file.
#
# A control run with the gates off proves the prompt actually provokes comments,
# so a green gated run cannot be explained by a model that happened to write none.
#
# Usage: tests/e2e_write_gate.sh [--keep]
#        tests/e2e_write_gate.sh --arm <label> <off|modify> <workdir>
#
# --arm runs exactly one arm and writes its verdict to <workdir>/<label>.rc.
# That lets both arms be launched as detached processes and collected later,
# instead of being killed by a caller's foreground timeout.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${TMPDIR:-/tmp}/comment-sicko-e2e.$$"
KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

cleanup() { [[ $KEEP -eq 1 || -n "${ARM:-}" ]] || rm -rf "$WORK"; }
trap cleanup EXIT

# The isolated home has no provider credentials of its own beyond the copied
# .env, so pin the same model the main profile uses. Overridable from the
# environment when a different provider is wanted.
: "${E2E_MODEL:=space-bunny-free}"
# opencode-ZEN, not opencode-go: go rejects space-bunny-free with
# "Model is unavailable" (HTTP 400) while zen answers it 200.
: "${E2E_PROVIDER:=opencode-zen}"
: "${E2E_BASE_URL:=https://opencode.ai/zen/v1}"
export E2E_MODEL E2E_PROVIDER E2E_BASE_URL
echo "e2e model: $E2E_PROVIDER/$E2E_MODEL"

# 1500s, not 900s: at 900 both arms were killed mid-turn and the resulting
# partial file was scored as if it were a finished one. Override with E2E_TIMEOUT.
TURN_TIMEOUT="${E2E_TIMEOUT:-1500}"
echo "e2e turn timeout: ${TURN_TIMEOUT}s"

# HERMES_RUNTIME_DIR is where Hermes unpacks its own pinned interpreter. Moving
# it makes the launcher re-exec a python that does not exist yet, so the isolated
# home reuses the real runtime dir and only HERMES_HOME differs.
REAL_RUNTIME_DIR="${HERMES_RUNTIME_DIR:-$HOME/.hermes/tools}"
echo "e2e runtime dir: $REAL_RUNTIME_DIR"

# Invoke the checkout directly. Going through the shared `hermes` launcher from a
# run under a different HERMES_HOME republishes ~/.hermes/bin/hermes with a
# baked-in path, which breaks every later `hermes` invocation. tests/hermes_direct.sh
# avoids the launcher entirely.
HERMES_REPO_ROOT="${HERMES_REPO_ROOT:-$HOME/.hermes/hermes-agent}"
export HERMES_REPO_ROOT
HERMES_BIN="$REPO/tests/hermes_direct.sh"

assess() {
    # $1 = produced file. Prints the stripper's verdict; exit 1 if anything is
    # still removable. Asks the stripper rather than grepping for '#', so a
    # legitimate '#' inside a string is not counted as a comment.
    python3 - "$REPO" "$1" <<'PY'
import sys, pathlib
repo, target = sys.argv[1], sys.argv[2]
sys.path.insert(0, repo)
from scanner import scan_file
from scanner.strip import strip_comments

fp = pathlib.Path(target)
report = scan_file(fp)
result = strip_comments(fp.read_text(encoding="utf-8"), target)

print(f"  comment lines: {report.comment_lines}   code lines: {report.code_lines}")
print(f"  deletable findings: {len(report.findings)}")
for f in report.findings[:15]:
    print(f"    line {f.line}  {f.level:<6} {f.kind:<10} {f.text[:64]}")
print(f"  REMOVABLE BY GATE: {result.removed_count}")
for line, kind, body in result.removed[:15]:
    print(f"    line {line}  {kind:<10} {body[:60]}")
sys.exit(1 if result.removed_count else 0)
PY
}

run_case() {
    # $1 = label, $2 = gate mode (off | modify)
    local label="$1" mode="$2"
    local home="$WORK/$label"
    mkdir -p "$home/plugins" "$home/proj"

    export HERMES_HOME="$home"
    export HERMES_RUNTIME_DIR="$REAL_RUNTIME_DIR"

    # `plugins.enabled` is required as well as the link: with only config.yaml the
    # discovery finds no plugin dir, and with only the link it stays disabled.
    cat > "$home/config.yaml" <<YAML
model:
  default: ${E2E_MODEL}
  provider: ${E2E_PROVIDER}
  base_url: ${E2E_BASE_URL}
  api_mode: chat_completions
plugins:
  enabled:
    - comment-sicko
  disabled: []
  entries:
    comment-sicko:
      settings:
        write_gate_mode: $mode
        verify_gate_mode: $mode
        sicko_enabled: false
YAML
    cp "$HOME/.hermes/.env" "$home/.env" 2>/dev/null || true
    ln -sfn "$REPO" "$home/plugins/comment-sicko"

    local prompt
    prompt="$(cat "$REPO/fixtures/confused_prompt.md")"

    echo "--- $label (write_gate_mode=$mode): running hermes chat"
    # --in is the supported way to set the session directory. A bare
    # `cd "$home/proj"` in a subshell is NOT enough: this shell exports
    # TERMINAL_CWD (the cwd of the parent Hermes session), and every cwd
    # consumer prefers that over the process cwd, so the agent wrote into
    # the parent's working directory instead. Unset it as well, or --in
    # refreshes it to point at the wrong place.
    (
        unset TERMINAL_CWD
        cd "$home/proj" || exit 3
        timeout "$TURN_TIMEOUT" "$HERMES_BIN" --in "$home/proj" chat -q "$prompt"
    ) > "$WORK/$label.log" 2>&1
    local rc=$?
    echo "--- $label: exit $rc"

    if [[ ! -f "$home/proj/retry.py" ]]; then
        # The model may pick a different filename; any Python file it wrote in
        # the sandbox counts as the produced artifact.
        produced="$(find "$home/proj" -maxdepth 2 -name '*.py' -type f -printf '%s %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)"
        if [[ -z "$produced" ]]; then
            echo "--- $label: FAIL, no Python file was written in $home/proj"
            tail -25 "$WORK/$label.log"
            return 2
        fi
        echo "--- $label: retry.py absent, using $produced instead"
    else
        produced="$home/proj/retry.py"
    fi

    echo "--- $label: produced $produced"
    # A turn that hit the timeout produced a partial file. Scoring it as a clean
    # pass is wrong: the control "passed" only because the timeout cut it short.
    if [[ $rc -ne 0 ]]; then
        echo "--- $label: WARNING turn did not exit cleanly (rc=$rc)"
        if [[ $rc -eq 124 ]]; then
            echo "--- $label: WARNING 900s timeout hit; the file is a PARTIAL turn"
        fi
    fi
    assess "$produced"
    local verdict=$?
    if [[ $verdict -eq 0 ]]; then
        echo "--- $label: PASS, nothing removable left"
    else
        echo "--- $label: FAIL, removable comments survived"
    fi
    return $verdict
}

if [[ "${1:-}" == "--arm" ]]; then
    # One arm, detached, verdict recorded for a later collector.
    ARM="$2"
    WORK="${4:?--arm needs a workdir}"
    mkdir -p "$WORK"
    run_case "$2" "$3"
    rc=$?
    echo "$rc" > "$WORK/$2.rc"
    echo "--- $2 arm finished: rc=$rc"
    exit $rc
fi

echo "=== control: gates off (prompt should provoke comments) ==="
run_case control off
control=$?

echo
echo "=== gates on ==="
run_case gated modify
gated=$?

echo
echo "control(off)=$control  gated(on)=$gated"
echo "logs: $WORK"
[[ $KEEP -eq 1 ]] && echo "kept: $WORK"

if [[ $gated -ne 0 ]]; then
    echo "RESULT: FAIL (gated run left removable comments)"
    exit 1
fi
if [[ $control -eq 0 ]]; then
    # A control with nothing removable cannot prove the prompt provokes
    # comments, so a clean gated run proves nothing either. That includes the
    # case where the control was cut short by the turn timeout.
    echo "RESULT: INCONCLUSIVE (control produced no removable comments, so this prompt"
    echo "        does not discriminate; rewrite fixtures/confused_prompt.md)"
    echo "        A truncated control turn counts here too -- raise the timeout."
    exit 2
fi
echo "RESULT: PASS (control provoked comments, gated run left none)"
exit 0