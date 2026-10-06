#!/usr/bin/env python3
"""Prove the no-comments skill's vendored scanner cannot drift from the plugin's.

The skill's ``comment_audit.py`` delegates to the plugin's ``scanner/`` package
when it is installed and to a vendored copy otherwise. That fallback only stays
honest if the two copies are identical, so this checks both halves:

1. the vendored tree is byte-identical to the plugin's, and
2. running the shim over a wide corpus equals running the vendored scanner
   directly over the same corpus.

The historical pre-plugin script is deliberately NOT the reference: its
classification of tool-consumed suppression comments (``# type: ignore``,
``# noqa``) was wrong, and pinning to it would re-introduce that bug.
"""

from __future__ import annotations

import filecmp
import json
import subprocess
import sys
from pathlib import Path

PLUGIN = Path("/media/data/projects/ACTIVE/comment-sicko")
SHIM_DIR = Path(
    "/home/rumlyne/.hermes/skills/software-development/no-comments/scripts"
)
SHIM = SHIM_DIR / "comment_audit.py"
VENDORED = SHIM_DIR / "scanner"
CORPUS = [
    "/home/rumlyne/.hermes/hermes-agent/tools",
    "/home/rumlyne/.hermes/hermes-agent/agent",
    "/home/rumlyne/.hermes/hermes-agent/plugins",
]

PROBE = '''
import json, sys
sys.path.insert(0, {vendored!r})
from scanner import iter_files, scan_file
findings = []
for p in iter_files({corpus!r}):
    findings.extend(f.as_dict() for f in scan_file(p).findings)
print(json.dumps(sorted(findings, key=lambda d: (d["path"], d["line"],
      d["level"], d["kind"], d["text"]))))
'''


def check_identical() -> list[str]:
    problems: list[str] = []
    if not VENDORED.is_dir():
        return [f"vendored scanner missing at {VENDORED}"]
    plugin_files = {
        p.relative_to(PLUGIN / "scanner")
        for p in (PLUGIN / "scanner").rglob("*.py")
    }
    vendored_files = {p.relative_to(VENDORED) for p in VENDORED.rglob("*.py")}
    for missing in sorted(plugin_files - vendored_files):
        problems.append(f"vendored copy is missing {missing}")
    for extra in sorted(vendored_files - plugin_files):
        problems.append(f"vendored copy has an extra file {extra}")
    for rel in sorted(plugin_files & vendored_files):
        if not filecmp.cmp(PLUGIN / "scanner" / rel, VENDORED / rel, shallow=False):
            problems.append(f"vendored copy differs from plugin: {rel}")
    return problems


def run_shim() -> list[dict]:
    proc = subprocess.run(
        [sys.executable, str(SHIM), "--json", *CORPUS],
        capture_output=True, text=True,
    )
    if proc.returncode not in (0, 1):
        print(f"shim failed rc={proc.returncode}\n{proc.stderr[:800]}")
        raise SystemExit(2)
    return sorted(
        json.loads(proc.stdout)["findings"],
        key=lambda d: (d["path"], d["line"], d["level"], d["kind"], d["text"]),
    )


def run_vendored() -> list[dict]:
    proc = subprocess.run(
        [sys.executable, "-c", PROBE.format(vendored=str(VENDORED), corpus=CORPUS)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"vendored probe failed rc={proc.returncode}\n{proc.stderr[:800]}")
        raise SystemExit(2)
    return json.loads(proc.stdout)


def main() -> int:
    problems = check_identical()
    if problems:
        print("vendored copy has DRIFTED:")
        for p in problems:
            print(f"  {p}")
        print("\nfix: rsync -a --delete "
              f"{PLUGIN}/scanner/ {SHIM_DIR}/scanner/")
        return 1
    print("vendored copy is byte-identical to the plugin's scanner")

    a, b = run_shim(), run_vendored()
    print(f"shim findings    : {len(a)}")
    print(f"vendored findings: {len(b)}")
    if a != b:
        only_shim = [d for d in a if d not in b]
        only_vend = [d for d in b if d not in a]
        print(f"MISMATCH: only-shim={len(only_shim)} only-vendored={len(only_vend)}")
        for d in only_shim[:5]:
            print(f"  shim {d['path']}:{d['line']} {d['kind']}")
        for d in only_vend[:5]:
            print(f"  vend {d['path']}:{d['line']} {d['kind']}")
        return 1
    print("IDENTICAL: shim and vendored scanner agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
