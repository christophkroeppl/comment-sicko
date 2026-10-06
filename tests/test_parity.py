#!/usr/bin/env python3
"""Differential test: the scanner package must behave like the original script.

Compares JSON findings from ``comment_audit.py --json`` against the same scan
driven through ``scanner.scan_file`` over a corpus of real files plus fixtures.
Any difference in level, kind, line, or text is a regression, because the
``no-comments`` skill and its CI gate both depend on this exact behavior.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ORIGINAL = Path("/home/rumlyne/.hermes/skills/software-development/no-comments/scripts/comment_audit.py")

sys.path.insert(0, str(ROOT))
from scanner import iter_files, scan_file  # noqa: E402


def via_original(paths: list[str]) -> list[dict]:
    proc = subprocess.run(
        [sys.executable, str(ORIGINAL), "--json", *paths],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    if proc.returncode not in (0, 1):
        raise AssertionError(f"original script failed: {proc.returncode}\n{proc.stderr}")
    return json.loads(proc.stdout)["findings"]


def via_package(paths: list[str]) -> list[dict]:
    out = []
    for path in iter_files(paths):
        for f in scan_file(path).findings:
            out.append(f.as_dict())
    return sorted(out, key=lambda d: (d["path"], d["line"], d["level"], d["kind"]))


def main() -> int:
    if not ORIGINAL.is_file():
        print(f"SKIP: original script not present at {ORIGINAL}")
        return 0

    targets = [str(HERE.parent / "scanner"), str(HERE.parent / "fixtures")]
    findings_a = sorted(via_original(targets), key=lambda d: (d["path"], d["line"]))
    findings_b = sorted(via_package(targets), key=lambda d: (d["path"], d["line"]))

    if findings_a == findings_b:
        print(f"identical: {len(findings_a)} findings agree across both implementations")
        return 0

    print(f"MISMATCH: original {len(findings_a)} vs package {len(findings_b)}")
    only_a = [d for d in findings_a if d not in findings_b]
    only_b = [d for d in findings_b if d not in findings_a]
    for label, rows in (("only original", only_a), ("only package", only_b)):
        for row in rows[:20]:
            print(f"  {label}: {row['path']}:{row['line']} {row['level']} {row['kind']} {row['text'][:70]!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())