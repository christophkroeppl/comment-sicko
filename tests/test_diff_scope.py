#!/usr/bin/env python3
"""Diff-scoping tests: which lines of the working diff are scanned.

Run: ``python3 tests/test_diff_scope.py``

The mapping function is what keeps a review scoped to changed code, so a
malformed path in a diff has to be dropped rather than raised. Two real bugs
lived here:

- a ``/dev/null`` destination (a deleted file) registered a ``None`` key, and
  the caller then built ``Path(None)`` — killing the entire review over one
  deleted file;
- a path without a ``b/`` prefix was never stripped to a repo-relative path.

``git_diff_lines`` shells out to git, so the tests drive the real repo rather
than mocking it.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _load import load  # noqa: E402

cs = load()
from scanner.scan import git_diff_lines  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {label}")
    else:
        FAILURES.append(f"{label}: {detail}")
        print(f"  FAIL  {label}  {detail}")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout


def scratch_repo() -> Path:
    """A throwaway repo: one committed file, one deleted, one edited."""
    tmp = Path(tempfile.mkdtemp(prefix="cs-diff-"))
    git(tmp, "init", "-q")
    git(tmp, "config", "user.email", "t@example.invalid")
    git(tmp, "config", "user.name", "T")
    (tmp / "kept.txt").write_text("line1\nline2\n")
    (tmp / "gone.txt").write_text("will be deleted\n")
    git(tmp, "add", "-A")
    git(tmp, "commit", "-q", "-m", "base")
    # A deleted file, a new file, and an edited one: all three hunk shapes the
    # parser has to survive in one diff. The new file must be staged — an
    # untracked file is invisible to `git diff` by design, and pretending
    # otherwise would test the wrong thing.
    git(tmp, "rm", "-q", "gone.txt")
    (tmp / "kept.txt").write_text("line1\nline2\n# a comment\n")
    (tmp / "fresh.txt").write_text("# new file comment\n")
    git(tmp, "add", "-A")
    return tmp


def main() -> int:
    repo = scratch_repo()

    # A deleted file in the diff: /dev/null on the destination side.
    mapping = git_diff_lines_at(repo, None)

    check("deleted file registers no None key", None not in mapping,
          "keys=" + repr(sorted((repr(k) for k in mapping))))
    check("surviving file is mapped", "kept.txt" in mapping,
          f"keys={sorted(mapping)}")
    check("deleted file is absent", "gone.txt" not in mapping,
          f"keys={sorted(mapping)}")
    check("new file is mapped", "fresh.txt" in mapping,
          f"keys={sorted(mapping)}")
    check("edited file is mapped", "kept.txt" in mapping,
          f"keys={sorted(mapping)}")
    check("added lines are recorded for the edited file",
          bool(mapping.get("kept.txt")),
          f"lines={sorted(mapping.get('kept.txt', ()))}")

    # Every key must be usable as a Path — that was the crash.
    from pathlib import Path as P
    usable = True
    detail = ""
    for key in mapping:
        try:
            P(repo / key)
        except Exception as exc:
            usable = False
            detail = f"{key!r}: {exc}"
            break
    check("every mapping key is Path-safe", usable, detail)

    # A base that does not exist must not raise; it degrades to no diff.
    empty = git_diff_lines_at(repo, "no-such-branch-xyz")
    check("unknown base returns a mapping, not an error", isinstance(empty, dict),
          type(empty).__name__)

    print()
    if FAILURES:
        print(f"  {len(FAILURES)} FAILED")
        return 1
    print("  all diff-scope checks passed")
    return 0


def git_diff_lines_at(repo: Path, base: str | None) -> dict:
    """git_diff_lines, but pointed at ``repo`` instead of the cwd."""
    import os
    real = os.getcwd()
    os.chdir(repo)
    try:
        return git_diff_lines(base)
    finally:
        os.chdir(real)


if __name__ == "__main__":
    raise SystemExit(main())
