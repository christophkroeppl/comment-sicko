"""File-level scanning and git diff scoping.

Moved from the no-comments skill's scripts/comment_audit.py (MIT, same
author), which is now a thin shim over this package.
"""
from __future__ import annotations
import re
import subprocess
from pathlib import Path

from .classify import classify, code_share, group_comments, next_code_line
from .detect import (BLOCK_COMMENT, BASENAME_COMMENT, Finding, LINE_COMMENT,
                       MAX_BYTES, SKIP_DIRS, FileReport, block_pair,
                       generic_comments, line_token, python_comments)


def next_code_for(lines: list[str], line_no: int, lt: str | None) -> str:
    return next_code_line(lines, line_no, lt)


def scan_file(path: Path, only_lines: set[int] | None = None) -> FileReport:
    rep = FileReport(path=str(path))
    try:
        if path.stat().st_size > MAX_BYTES:
            rep.parsed = False
            return rep
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        rep.parsed = False
        return rep

    ext = path.suffix.lower()
    lt = line_token(path)
    bp = block_pair(path)

    if ext in {".py", ".pyi"}:
        pairs = python_comments(text)
    else:
        pairs = generic_comments(text, lt, bp)
        if not pairs and not lt:
            rep.parsed = False
            return rep

    lines = text.splitlines()
    comment_line_numbers: set[int] = set()
    for start, end, body, is_doc in group_comments(pairs, lines, lt):
        comment_line_numbers.update(range(start, min(end, len(lines)) + 1))
        if only_lines is not None and not (only_lines & set(range(start, end + 1))):
            continue
        nxt = next_code_for(lines, end, lt)
        level, rule = classify(str(path), start, body, nxt, ext, is_doc,
                              multi_line=(end > start),
                              share_code=code_share(lines, start, end, lt, ext),
                              lines=lines, end_line=end)
        if level == "KEEP":
            continue
        if is_doc:
            kind = "docstring:" + rule if ext in {".py", ".pyi"} else "block"
        else:
            kind = rule
        rep.findings.append(Finding(str(path), start, level, kind,
                                    body.strip()[:160]))

    rep.comment_lines = len(comment_line_numbers)
    rep.code_lines = sum(
        1 for idx, l in enumerate(lines, 1)
        if l.strip() and idx not in comment_line_numbers
    )
    return rep


def iter_files(targets: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in targets:
        p = Path(raw)
        if p.is_file():
            out.append(p)
            continue
        for root, dirs, files in os_walk(p):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for f in files:
                fp = Path(root) / f
                if fp.suffix.lower() in LINE_COMMENT or fp.suffix.lower() in BLOCK_COMMENT \
                        or f in BASENAME_COMMENT:
                    out.append(fp)
    return sorted(set(out))


def os_walk(root: Path):
    import os as _os
    return _os.walk(root)


def git_diff_lines(base: str | None) -> dict[str, set[int]]:
    """Map path -> added line numbers for the working diff."""
    if base:
        cmds = [["git", "diff", "--unified=0", f"{base}...HEAD"],
                ["git", "diff", "--unified=0", base]]
    else:
        cmds = [["git", "diff", "--unified=0", "HEAD"], ["git", "diff", "--unified=0"]]
    out = ""
    for cmd in cmds:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if out.strip():
            break
    result: dict[str, set[int]] = {}
    current = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            current = None if p == "/dev/null" else p[2:] if p.startswith("b/") else p
            # A hunk whose path is /dev/null has no file to scan. Registering a
            # None key here used to make the caller do `Path(None)`, which
            # raises "expected str or os.PathLike, not NoneType" and killed the
            # whole review over a deleted file in the diff.
            if current is not None:
                result.setdefault(current, set())
            continue
        m = re.match(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)
        if m and current:
            start = int(m.group(1))
            count = int(m.group(2) or 1)
            result[current].update(range(start, start + max(count, 1)))
    return result
