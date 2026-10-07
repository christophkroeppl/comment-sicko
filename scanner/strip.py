"""Write-time comment removal, built on the same detection the audit uses.

Given the text a model is about to write, return the text with deletable
comment lines gone and a record of exactly what was removed.

Why this cannot be a regex: ``url = "http://x#frag"`` and ``re.compile(r"^\\s*#")``
both break under naive stripping. Detection therefore reuses
``scanner.detect``, which tokenizes Python and masks string contents elsewhere,
and strips only the line ranges the detector reports.

Two invariants, both load-bearing:

- **Newlines are never collapsed.** Removing text in place, rather than
  deleting lines, keeps every line number after the edit identical, so the
  model's own ``file:line`` references and any in-flight patch context stay
  valid.
- **Allowances survive.** Legal headers, formatter directives, tool
  directives and issue links are ``KEEP`` verdicts upstream and are never
  candidates here. ``protect_allowances`` also holds back ``REVIEW`` findings,
  which are judgement calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .classify import classify, code_share, group_comments, next_code_line
from .detect import (
    BLOCK_COMMENT,
    LEVELS,
    LINE_COMMENT,
    MAX_BYTES,
    block_pair,
    generic_comments,
    line_token,
    mask_strings,
    python_comments,
)

# Python keeps docstrings; stripping one changes runtime introspection, which is
# a behaviour change, not a comment cleanup. Everything else is fair game.
_DOC_KEEP = "docstring"


@dataclass
class StripResult:
    """What ``strip_comments`` would do, without doing it."""

    text: str
    removed: list[tuple[int, str, str]] = field(default_factory=list)
    protected: list[tuple[int, str, str]] = field(default_factory=list)
    skipped_reason: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.removed)

    @property
    def removed_count(self) -> int:
        return len(self.removed)

    def summary(self) -> str:
        if self.skipped_reason:
            return f"comment-sicko: {self.skipped_reason}"
        if not self.removed:
            return "comment-sicko: no deletable comments"
        lines = ", ".join(f"{line}: {kind}" for line, kind, _t in self.removed[:8])
        more = f" (+{len(self.removed) - 8} more)" if len(self.removed) > 8 else ""
        return (
            f"comment-sicko: removed {len(self.removed)} comment line(s) before writing"
            f" [{lines}{more}]. Do not re-add them; the code must explain itself."
        )


def _blocks(text: str, suffix: str, basename: str) -> list[tuple[int, int, str, bool]]:
    """Grouped comment spans as (start, end, body, is_docstring), 1-indexed."""
    if suffix in {".py", ".pyi"}:
        pairs = python_comments(text)
    else:
        path = Path(basename or f"x{suffix}")
        pairs = generic_comments(text, line_token(path), block_pair(path))
    return group_comments(pairs, text.splitlines(), line_token(Path(basename or f"x{suffix}")))


def _verdict(text: str, span: tuple[int, int, str, bool], suffix: str) -> str:
    """DELETE / REVIEW / KEEP for one grouped span, via the shared classifier."""
    start, end, body, is_doc = span
    lines = text.splitlines()
    lt = line_token(Path(f"x{suffix}"))
    bp = block_pair(Path(f"x{suffix}"))
    nxt = next_code_line(lines, end, lt)
    is_trailing = False
    if start == end and 1 <= start <= len(lines):
        col = _comment_token_col(lines[start - 1], lt, bp)
        if col is not None and col > 0 and lines[start - 1][:col].strip():
            is_trailing = True
    level, _rule = classify(
        "", start, body, nxt, suffix, is_doc,
        multi_line=(end > start),
        share_code=code_share(lines, start, end, lt, suffix),
        lines=lines, end_line=end,
        is_trailing=is_trailing,
    )
    return level


def strip_comments(
    text: str,
    path: str = "",
    *,
    protect_allowances: bool = True,
    fail_level: str = "DELETE",
) -> StripResult:
    """Return *text* with comment lines at or above *fail_level* removed.

    ``fail_level`` reuses the audit's own vocabulary: ``DELETE`` (the default)
    removes mechanical findings only, ``REVIEW`` also removes the judgement
    calls. Comment markers are replaced with blanks rather than the lines being
    dropped, so line numbers are preserved exactly.
    """
    result = StripResult(text=text)
    if not text.strip():
        return result

    suffix = Path(path).suffix.lower() if path else ""
    basename = Path(path).name if path else ""
    if not suffix and not basename:
        # Nothing to key the syntax off. Guessing here would strip `#` out of
        # markdown and shell heredocs, so decline rather than guess.
        result.skipped_reason = "no file extension; cannot tell comments from content"
        return result
    if suffix not in LINE_COMMENT and suffix not in BLOCK_COMMENT and basename not in {
        "Dockerfile", "Makefile", "Rakefile", "Gemfile", "Justfile", "CMakeLists.txt",
    }:
        result.skipped_reason = f"{suffix or basename} has no known comment syntax; left alone"
        return result
    if len(text) > MAX_BYTES:
        result.skipped_reason = "file too large to scan safely; left alone"
        return result

    try:
        spans = _blocks(text, suffix, basename)
    except Exception:
        result.skipped_reason = "could not parse comments; left alone"
        return result

    lt = line_token(Path(basename or f"x{suffix}"))
    bp = block_pair(Path(basename or f"x{suffix}"))
    threshold = LEVELS.index(fail_level if fail_level in LEVELS else "DELETE")
    chars = list(text)
    for start, end, body, is_doc in spans:
        level = _verdict(text, (start, end, body, is_doc), suffix)
        tag = f"{'docstring:' if is_doc else ''}{level.lower()}"

        if level == "KEEP":
            result.protected.append((start, tag, body.strip()[:80]))
            continue
        if is_doc:
            result.protected.append((start, "docstring", body.strip()[:80]))
            continue

        at_or_above = LEVELS.index(level) <= threshold
        permitted = at_or_above
        if not permitted:
            result.protected.append((start, tag, body.strip()[:80]))
            continue

        result.removed.append((start, tag, body.strip()[:80]))
        for lineno in range(start, min(end, len(text.splitlines())) + 1):
            _blank_line_or_trailing(chars, text, lineno, lt, bp)

    result.text = "".join(chars).rstrip("\n") + ("\n" if text.endswith("\n") else "")
    return result


def _comment_token_col(line: str, lt: str | None, bp) -> int | None:
    """Return the column where the comment token starts, or None."""
    masked = mask_strings(line)
    if lt:
        m = re.search(r"(?:(?<=\s)|^)" + re.escape(lt), masked)
        if m:
            return m.start()
    if bp:
        bs, be = bp if isinstance(bp, tuple) else (bp, "")
        for token in (bs, be):
            if token:
                idx = masked.find(token)
                if idx >= 0:
                    return idx
    return None


def _blank_line_or_trailing(chars: list[str], text: str, lineno: int,
                             lt: str | None, bp) -> None:
    """Blank a comment line, preserving any code before a trailing comment."""
    lines = text.splitlines(keepends=True)
    if not (1 <= lineno <= len(lines)):
        return
    line = lines[lineno - 1]
    col = _comment_token_col(line, lt, bp)
    if col is not None and col > 0 and line[:col].strip():
        offset = sum(len(l) for l in lines[:lineno - 1])
        for pos in range(offset + col, offset + len(line)):
            if chars[pos] != "\n":
                chars[pos] = " "
    else:
        _blank_line(chars, text, lineno)


def _blank_line(chars: list[str], text: str, lineno: int) -> None:
    """Blank one physical line in place, keeping the newline itself."""
    offset = 0
    for idx, line in enumerate(text.splitlines(keepends=True), start=1):
        if idx == lineno:
            for pos in range(offset, offset + len(line)):
                if chars[pos] != "\n":
                    chars[pos] = " "
            return
        offset += len(line)


def diff_added_lines(diff_text: str) -> dict[str, set[int]]:
    """Parse ``git diff -U0`` output into path -> added line numbers."""
    import re

    result: dict[str, set[int]] = {}
    current = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            raw = line[4:].strip()
            current = None if raw == "/dev/null" else (raw[2:] if raw.startswith("b/") else raw)
            result.setdefault(current, set())
            continue
        match = re.match(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)
        if match and current:
            start = int(match.group(1))
            count = int(match.group(2) or 1)
            result[current].update(range(start, start + max(count, 1)))
    return result


__all__ = ["StripResult", "diff_added_lines", "strip_comments", "_blocks", "_verdict"]