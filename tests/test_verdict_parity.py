#!/usr/bin/env python3
"""Verdict parity: the stripper must reach the same verdict as the scanner.

For every grouped comment span in every file given, ``strip_comments``'s
internal verdict must equal ``scan_file``'s verdict for the same span. The
stripper and the audit share one classifier; this asserts they share one
*decision*, not just one code path.

Run: ``python3 tests/test_verdict_parity.py [paths...]``
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scanner.classify import classify, code_share, group_comments, next_code_line  # noqa: E402
from scanner.detect import (  # noqa: E402
    Finding,
    FileReport,
    block_pair,
    generic_comments,
    line_token,
    python_comments,
)
from scanner.strip import _verdict, strip_comments  # noqa: E402


def scanner_verdicts(text: str, path: Path) -> dict[int, tuple[str, bool]]:
    """start_line -> (level, is_docstring) as scan_file would judge it."""
    suffix = path.suffix.lower()
    if suffix in {".py", ".pyi"}:
        pairs = python_comments(text)
    else:
        pairs = generic_comments(text, line_token(path), block_pair(path))
    lines = text.splitlines()
    lt = line_token(path)
    out: dict[int, tuple[str, bool]] = {}
    for start, end, body, is_doc in group_comments(pairs, lines, lt):
        level, _rule = classify(
            str(path), start, body, next_code_line(lines, end, lt), suffix, is_doc,
            multi_line=(end > start),
            share_code=code_share(lines, start, end, lt, suffix),
        )
        out[start] = (level, is_doc)
    return out


def strip_verdicts(text: str, path: str) -> dict[int, tuple[str, bool]]:
    """start_line -> (level, is_docstring) as the stripper would judge it."""
    suffix = Path(path).suffix.lower()
    if suffix in {".py", ".pyi"}:
        pairs = python_comments(text)
    else:
        p = Path(path)
        pairs = generic_comments(text, line_token(p), block_pair(p))
    lines = text.splitlines()
    lt = line_token(Path(path))
    out: dict[int, tuple[str, bool]] = {}
    for start, end, body, is_doc in group_comments(pairs, lines, lt):
        out[start] = (_verdict(text, (start, end, body, is_doc), suffix), is_doc)
    return out


def main(argv: list[str]) -> int:
    targets = argv or [
        str(Path(__file__).resolve().parent.parent / "scanner"),
        str(Path(__file__).resolve().parent.parent / "tests"),
        "/home/rumlyne/.hermes/hermes-agent/tools",
        "/home/rumlyne/.hermes/hermes-agent/plugins",
    ]
    from scanner import iter_files

    files = iter_files(targets)
    if not files:
        print("no files found")
        return 1

    total = 0
    mismatches: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        a = scanner_verdicts(text, path)
        b = strip_verdicts(text, str(path))
        if set(a) != set(b):
            mismatches.append(f"{path}: span sets differ {sorted(set(a) ^ set(b))[:5]}")
            continue
        for line in sorted(a):
            total += 1
            if a[line] != b[line]:
                mismatches.append(f"{path}:{line} scanner={a[line]} stripper={b[line]}")

    print(f"{len(files)} files, {total} comment spans, {len(mismatches)} verdict mismatches")
    for m in mismatches[:20]:
        print(f"  {m}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))