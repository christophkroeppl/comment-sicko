"""Comment grouping and the DELETE / REVIEW / KEEP decision.

Moved from the no-comments skill's scripts/comment_audit.py (MIT, same
author), which is now a thin shim over this package.
"""
from __future__ import annotations
import re

from .detect import looks_like_commented_code


STOP = {
    "the", "and", "for", "that", "this", "with", "from", "into", "out", "are",
    "was", "were", "has", "have", "had", "not", "but", "you", "your", "its",
    "our", "their", "them", "then", "than", "when", "while", "will", "would",
    "can", "should", "could", "may", "might", "must", "each", "any", "all",
    "one", "two", "use", "used", "using", "make", "made", "get", "set", "add",
    "new", "old", "see", "also", "just", "only", "more", "most", "very",
    "here", "there", "does", "done", "via", "per", "etc", "let", "run",
    "call", "calls", "calling", "called", "returns", "return", "value",
}


NARRATIVE_VERB = {
    "increment", "decrement", "initialize", "initialise", "create", "fetch",
    "loop", "iterate", "check", "log", "print", "set", "add", "remove",
    "delete", "parse", "load", "save", "open", "close", "call", "assign",
    "sort", "filter", "map", "convert", "return", "reset", "update", "build",
    "append", "push", "pop", "count", "compute", "calculate", "invert",
    "reverse", "sum", "merge", "split", "join", "wrap", "unwrap", "clone",
    "copy", "move", "emit", "raise", "throw", "catch", "handle", "dispatch",
    "render", "draw", "flush", "clear", "insert", "select", "validate",
    "verify", "ensure", "assert", "apply", "collect", "extract", "normalize",
    # The verbs a model reaches for when narrating its own next line. Without
    # these the most common narration shape ("# make a request to the url")
    # missed NARRATIVE_RE and fell through to the KEEP catch-all, so an
    # own-line comment above code was never caught at all -- while the same
    # words as a trailing comment were.
    "make", "send", "get", "post", "write", "read", "run", "execute",
    "invoke", "start", "stop", "begin", "end", "find", "search", "show",
    "display", "output", "yield", "store", "give", "take", "use", "try",
    "wait", "sleep", "ask", "tell", "keep",
}


NARRATIVE_RE = re.compile(
    r"^\W*(" + "|".join(sorted(NARRATIVE_VERB, key=len, reverse=True))
    + r")(?:ing|s)?\b", re.I
)


CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")


def content_words(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)
    return {w.lower() for w in words if w.lower() not in STOP and not w.isdigit()}


def code_words(text: str) -> set[str]:
    """Identifiers from a code line, split on camelCase/snake_case."""
    out: set[str] = set()
    for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
        for piece in CAMEL.findall(ident):
            piece = piece.lower()
            if len(piece) >= 2 and piece not in STOP:
                out.add(piece)
    return out


def overlap_ratio(cw: set[str], code: str) -> float:
    """Share of comment content words echoed by the code line, substring-aware."""
    if not cw or not code:
        return 0.0
    low = code.lower()
    hits = sum(1 for w in cw if w in code or (len(w) >= 4 and w in low))
    return hits / len(cw)


PUNCT_ONLY = re.compile(r"[\s#/*+=\-~.]*")


BANNER = re.compile(
    r"^\s*(?:#|//+|/\*+|\*+|--+)?\s*[-=*_~#]{3,}"   # # ==== Section ==== */
    r"|^\s*(?:#|//)\s*$"                             # a bare '#' separator
)


def group_comments(pairs: list[tuple[int, int, str, bool]], lines: list[str],
                   lt: str | None) -> list[tuple[int, int, str, bool]]:
    """Merge contiguous standalone line-comments into one logical comment.

    A prose block, an ASCII table, and a wrapped sentence are single comments.
    Judging each physical line separately flags the blank '#' separators inside
    them and shreds the prose a human reads as one thought.

    A comment trailing real code (``x = 1  # note``) is never merged: the code
    on its line means it is a separate comment about that statement.
    """
    if not pairs:
        return []

    def is_trailing(ln: int) -> bool:
        if not lt or not (1 <= ln <= len(lines)):
            return False
        raw = lines[ln - 1]
        idx = raw.find(lt)
        return idx > 0 and raw[:idx].strip() != ""

    lines_only = [(a, b, t) for a, b, t, d in pairs if not d and b == a]
    if not lines_only:
        return pairs

    spans = [p for p in pairs if p[3] or p[1] != p[0]]
    groups: list[tuple[int, int, str, bool]] = []
    run_start, run_end, run_body = lines_only[0]
    run_open = not is_trailing(run_start)
    for start, end, body in lines_only[1:]:
        bridging = run_open and not is_trailing(start)
        if bridging:
            for ln in range(run_end + 1, start):
                if 1 <= ln <= len(lines) and lines[ln - 1].strip():
                    if not PUNCT_ONLY.fullmatch(lines[ln - 1]):
                        bridging = False
                        break
        if bridging:
            run_end = end
            run_body += " " + re.sub(r"^\s*(?:#|//)\s?", "", body.strip())
        else:
            groups.append((run_start, run_end, run_body.strip(), False))
            run_start, run_end, run_body = start, end, body
            run_open = not is_trailing(start)
    groups.append((run_start, run_end, run_body.strip(), False))
    return sorted(groups + spans, key=lambda t: t[0])


def code_share(lines: list[str], start: int, end: int, lt: str | None,
               ext: str) -> float:
    """Share of a comment block's physical lines that read as dead code."""
    total = 0
    code = 0
    for ln in range(start, end + 1):
        if not (1 <= ln <= len(lines)):
            continue
        raw = lines[ln - 1].strip()
        if not raw:
            continue
        total += 1
        if lt and raw.startswith(lt):
            raw = raw[len(lt):]
        if looks_like_commented_code(raw, ext):
            code += 1
    return code / total if total else 0.0


def next_code_line(lines: list[str], start: int, lt: str | None) -> str:
    """The first real code line at or after ``start``.

    Comment lines are skipped even when no line token is known. Python comments
    are extracted with the tokenizer, so ``lt`` is None there, and the lookup
    used to return the *next comment* instead of the code below it -- which made
    every repeated narration comment look like a perfect 1.0 echo of its twin
    and, worse, stopped it from being compared against the code it narrates.
    """
    for idx in range(start, min(start + 6, len(lines))):
        s = lines[idx].strip()
        if not s:
            continue
        if lt and s.startswith(lt):
            continue
        if not lt and _looks_like_comment(s):
            continue
        return s
    return ""


def _looks_like_comment(s: str) -> bool:
    """Cheap comment test for the generic (non-tokenised) path."""
    return bool(
        s.startswith("#")
        or s.startswith("//")
        or s.startswith("/*")
        or s.startswith("*")
        or s.startswith("--")
        or s.startswith(";")
        or s.startswith("<!--")
    )


SENTIMENT = [
    (r"\b(do not|don'?t|never)\s+(remove|delete|change|touch|edit|modify)\b", "imperative guard"),
    (r"\b(too risky|risky to|too dangerous)\b", "risk confession"),
    (r"\b(fine for now|for now|temporar(?:y|ily)|workaround|work around)\b", "temporary framing"),
    (r"\b(talk to|ask \w+|check with|coordinate with|get sign ?off)\b", "human process note"),
    (r"\b(IMPORTANT|CRITICAL|WARNING|NOTE)\s*[:!]", "scent marker"),
    (r"\bthis (?:is|was) (?:a )?(?:hack|fix|bug|workaround)\b", "sermon"),
    (r"\bjust in case|to be safe|should never happen|should not happen\b", "unproven claim"),
]


SUPPRESSION = [
    ("@ts-ignore", "ts-ignore"), ("@ts-expect-error", "ts-expect-error"),
    ("eslint-disable", "eslint-disable"), ("eslint-disable-next-line", "eslint-disable"),
    ("noqa", "noqa"), ("pylint: disable", "pylint-disable"),
    ("pylint:skip", "pylint-disable"), ("type: ignore", "type-ignore"),
    ("mypy: ignore", "type-ignore"), ("nolint", "nolint"),
    ("golangci-lint:disable", "golangci-disable"), ("nosec", "bandit-nosec"),
    ("coverage: ignore", "coverage-ignore"), ("pragma: no cover", "coverage-ignore"),
    ("istanbul ignore", "istanbul"), ("@SuppressWarnings", "java-suppress"),
    ("rubocop:disable", "rubocop-disable"), ("# pylint", "pylint-disable"),
    ("// @ts-nocheck", "ts-nocheck"), ("# type: ignore", "type-ignore"),
]


FORMATTER = [
    ("prettier-ignore", "prettier-ignore"), ("fmt: off", "fmt-off"),
    ("fmt:on", "fmt-on"), ("gofmt", "fmt-off"), ("rustfmt", "fmt-off"),
    ("clang-format", "fmt-off"), ("isort:skip", "isort"),
    ("# fmt:", "fmt-off"), ("@format", "fmt-off"),
]


KEEP_SIGNALS = [
    (r"\b(copyright|licensed under|spdx-license-identifier|apache license|mit license|"
     r"bsd license|mozilla public license|all rights reserved)\b", "legal header"),
    (r"https?://(?:github\.com|gitlab\.com|issues\.|jira\.|linear\.app|trac\.)", "issue link"),
    (r"^\s*(?:#!|//\s*@ts-nocheck|///\s*<reference|#\s*-\*-\s*coding)", "toolchain directive"),
    # markers consumed by an external tool, not by a human reader
    (r"(statement-breakpoint|sourceMappingURL|#\s*region\b|#\s*endregion\b|"
     r"\bgo:(generate|embed|build)\b|^\s*(?:>>>|\+\+\+|---)\s*$)", "tool directive"),
    # a CLI contract: usage synopses and bare parameter placeholders are the
    # documented interface, not narration
    (r"^\s*(?:#|//+|--|%|/\*+|\*+)\s*(usage|synopsis|arguments|params|options|"
     r"examples?)\b", "usage synopsis"),
    (r"^\s*<[a-z][\w.-]*>(\s*<[a-z][\w.-]*>)*\s*$", "usage synopsis"),
    # "per the spec", "spec 5.1", "according to RFC 2119" -- no trailing \b,
    # because these alternatives end in a space and a space has no boundary
    (r"\bper (?:the |this )?(?:spec|specification|rfc|standard|protocol|adr|"
     r"contract|man page|readme)\b"
     r"|\b(?:spec|specification|rfc|adr|issue|ticket|section)\s*[#§]?\s*[\d§]"
     r"|§\s*\d", "external spec"),
]


CODE_SHARE_DEAD = 0.5


def classify(path: str, line: int, body: str, next_line: str, ext: str,
             is_doc: bool = False, multi_line: bool = False,
             share_code: float = 0.0,
             lines: list[str] | None = None, end_line: int | None = None
             ) -> tuple[str, str]:
    """Return (level, rule) for a comment body.

    KEEP_SIGNALS are checked first: a legal header or an issue link survives
    regardless of how it reads. A docstring is treated more gently than a line
    comment -- it is a contract surface, so only the hard rules fire. A
    multi-line block is treated more gently still: the shape heuristics assume
    a one-line remark and misfire on wrapped prose.

    ``lines`` (with ``end_line``) is the physical text of the block. It is only
    needed to spot a repeated identical comment, because ``body`` is the block
    flattened into a single space-joined string.
    """
    s = body.strip()
    low = s.lower()
    cw = content_words(s)

    if BANNER.match(s):
        # an empty divider carries nothing; a named one ("-- Locales --")
        # carries a section label and is a judgement call
        return ("REVIEW", "named-divider") if re.search(r"[A-Za-z]{3,}", s) \
            else ("DELETE", "empty-divider")

    for sig, kind in KEEP_SIGNALS:
        if re.search(sig, s, re.I):
            return ("KEEP", kind)

    for needle, kind in SUPPRESSION:
        if needle.lower() in low:
            # A suppression comment is consumed by a tool, not by a reader.
            # Deleting it re-enables the very check it was silencing, so it is
            # an allowance (allowance 5: a directive a tool consumes).
            return ("KEEP", kind)

    for needle, kind in FORMATTER:
        if needle.lower() in low:
            return ("REVIEW", kind)

    if re.search(r"\b(TODO|FIXME|XXX|HACK)\b\s*[:(]?", s):
        return ("DELETE", "todo-marker")

    # A prose smell needs a reader, not a regex: an imperative guard or a
    # temporary-framing phrase marks a real constraint as often as a bad
    # rationalisation, so neither is a mechanical delete.
    for pattern, kind in SENTIMENT:
        if re.search(pattern, low):
            return ("REVIEW", kind)

    # A block that *looks* like dead code is a judgement call, not a mechanical
    # fact: a regex with an explanatory comment in it scores the same as a
    # genuinely abandoned code path.
    if not is_doc and share_code >= CODE_SHARE_DEAD:
        return ("REVIEW", "dead-code-block")

    # A multi-line block that is nothing but short narration is model noise
    # whatever it contains: `group_comments()` joins the lines into one string,
    # so NARRATIVE_RE sees only the first verb and a block of two narrative
    # lines would otherwise fall through to the KEEP catch-all. Judge every
    # physical line and drop the block if each one opens on an action verb.
    if not is_doc and multi_line and lines:
        last = end_line if end_line is not None else line
        parts = [ln.strip().lstrip("#").lstrip("/").lstrip("*").strip()
                 for ln in lines[line - 1:last]]
        parts = [p for p in parts if p]
        if len(parts) > 1 and all(
            len(p.split()) <= 8 and NARRATIVE_RE.match(p) for p in parts
        ):
            return ("DELETE", "narration-block")

    if not is_doc and not multi_line:
        if looks_like_commented_code(s, ext):
            return ("DELETE", "commented-out-code")

        # a comment that carries no content words at all looks like
        # documentation while documenting nothing -- worse than silence
        if not cw and len(s.split()) <= 5:
            return ("DELETE", "contentless")

        # narration: a short comment opening on an action verb, echoing the
        # line below it. A real explanation opens on a claim, not a verb.
        if cw and next_line:
            if NARRATIVE_RE.match(s) and len(s.split()) <= 8:
                return ("DELETE", "narration")
            ratio = overlap_ratio(cw, next_line)
            if ratio >= 0.75 and len(s.split()) <= 6:
                return ("DELETE", "narration")
            if ratio >= 0.5:
                return ("REVIEW", "narration")

    # A block that is nothing but the same short narration line repeated is the
    # clearest form of model noise, and it is NOT a multi-paragraph contract:
    # every physical line says the same thing. Judge it on its own merits so a
    # duplicated comment cannot hide behind the block exemption.
    if not is_doc and multi_line and not share_code and lines:
        # group_comments() flattens a block into one space-joined string, so the
        # per-line text is recovered from the file rather than from `body`.
        last = end_line if end_line is not None else line
        parts = [ln.strip() for ln in lines[line - 1:last] if ln.strip()]
        if (len(parts) > 1 and len(set(parts)) == 1
                and NARRATIVE_RE.match(parts[0]) and len(parts[0].split()) <= 8):
            return ("DELETE", "repeated-narration")

    # a docstring is judged on substance, not length: few content words means
    # it restates the signature. "A thing. Does a thing." carries none. A
    # multi-paragraph docstring is a contract, not a restatement, so only
    # the explicit-signature case applies to it.
    if is_doc:
        if re.match(r"^(Args|Params|Returns?|Yields|Raises)\s*[:.]\s*\S", s) \
                and len(s.split()) <= 14:
            return ("REVIEW", "signature-restatement")
        if not multi_line and len(cw) <= 2:
            return ("REVIEW", "contentless-docstring")
    return ("KEEP", "allowance")
