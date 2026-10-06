"""Lexical comment detection per language.

Moved from the no-comments skill's scripts/comment_audit.py (MIT, same
author), which is now a thin shim over this package.
"""
from __future__ import annotations
import ast
import re
import tokenize
import warnings
from dataclasses import dataclass, field
from pathlib import Path


SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build",
    "target", "vendor", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".next", ".nuxt", "coverage", ".idea", ".gradle", ".terraform",
}


MAX_BYTES = 2_000_000


LEVELS = ["DELETE", "REVIEW", "KEEP"]


MIN_LINES_FOR_DENSITY = 20


MIN_CODE_FOR_DENSITY = 10


@dataclass
class Finding:
    path: str
    line: int
    level: str
    kind: str
    text: str

    def as_dict(self) -> dict:
        return {"path": self.path, "line": self.line, "level": self.level,
                "kind": self.kind, "text": self.text}


@dataclass
class FileReport:
    path: str
    findings: list[Finding] = field(default_factory=list)
    comment_lines: int = 0
    code_lines: int = 0
    parsed: bool = True

    @property
    def density(self) -> float:
        total = self.comment_lines + self.code_lines
        return self.comment_lines / total if total else 0.0

    @property
    def density_meaningful(self) -> bool:
        """Small files have meaningless ratios -- one header over two lines."""
        return (self.comment_lines + self.code_lines >= MIN_LINES_FOR_DENSITY
                and self.code_lines >= MIN_CODE_FOR_DENSITY)


LINE_COMMENT = {
    ".py": "#", ".pyi": "#", ".sh": "#", ".bash": "#", ".zsh": "#", ".fish": "#",
    ".yaml": "#", ".yml": "#", ".toml": "#", ".cfg": "#", ".ini": "#", ".conf": "#",
    ".rb": "#", ".r": "#", ".pl": "#", ".pm": "#", ".nix": "#", ".cmake": "#",
    ".mk": "#", ".tf": "#", ".tfvars": "#", ".env": "#", ".properties": "#",
    ".js": "//", ".jsx": "//", ".mjs": "//", ".cjs": "//", ".ts": "//", ".tsx": "//",
    ".go": "//", ".rs": "//", ".java": "//", ".c": "//", ".h": "//", ".cc": "//",
    ".cpp": "//", ".hpp": "//", ".cs": "//", ".swift": "//", ".kt": "//",
    ".scala": "//", ".php": "//", ".dart": "//", ".zig": "//", ".sol": "//",
    ".sql": "--", ".hs": "--", ".lua": "--", ".adb": "--", ".ml": "#",
}


BLOCK_COMMENT = {
    ".js": ("/*", "*/"), ".jsx": ("/*", "*/"), ".mjs": ("/*", "*/"),
    ".cjs": ("/*", "*/"), ".ts": ("/*", "*/"), ".tsx": ("/*", "*/"),
    ".go": ("/*", "*/"), ".rs": ("/*", "*/"), ".java": ("/*", "*/"),
    ".c": ("/*", "*/"), ".h": ("/*", "*/"), ".cc": ("/*", "*/"),
    ".cpp": ("/*", "*/"), ".hpp": ("/*", "*/"), ".cs": ("/*", "*/"),
    ".swift": ("/*", "*/"), ".kt": ("/*", "*/"), ".scala": ("/*", "*/"),
    ".php": ("/*", "*/"), ".dart": ("/*", "*/"), ".zig": ("/*", "*/"),
    ".css": ("/*", "*/"), ".sql": ("/*", "*/"), ".ml": ("(*", "*)"),
    ".xml": ("<!--", "-->"), ".html": ("<!--", "-->"), ".svg": ("<!--", "-->"),
    ".vue": ("<!--", "-->"), ".md": ("<!--", "-->"), ".rst": ("..", ""),
    ".ex": "#", ".exs": "#",
}


BASENAME_COMMENT = {
    "Dockerfile": "#", "Makefile": "#", "Rakefile": "#", "Gemfile": "#",
    ".gitignore": "#", ".dockerignore": "#", ".editorconfig": "#", "Justfile": "#",
    "CMakeLists.txt": "#",
}


PY_BLOCK = ('"""', '"""')


def line_token(path: Path) -> str | None:
    if path.name in BASENAME_COMMENT:
        return BASENAME_COMMENT[path.name]
    ext = path.suffix.lower()
    if ext in LINE_COMMENT:
        return LINE_COMMENT[ext]
    return None


def block_pair(path: Path):
    ext = path.suffix.lower()
    return BLOCK_COMMENT.get(ext)


def blank_keep_newlines(text: str) -> str:
    """Blank text while preserving newlines.

    Replacing a span with plain spaces silently deletes its newlines and
    shifts every following line number -- which silently corrupts every
    file:line this tool reports.
    """
    return "".join("\n" if ch == "\n" else " " for ch in text)


def mask_strings(text: str, quotes: tuple[str, ...] = ("'", '"', "`")) -> str:
    """Blank out string contents, preserving length and newlines."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in quotes:
            quote = c
            out[i] = " "
            i += 1
            while i < n:
                if text[i] == "\\":
                    if out[i] != "\n":
                        out[i] = " "
                    if i + 1 < n and out[i + 1] != "\n":
                        out[i + 1] = " "
                    i += 2
                    continue
                if text[i] == quote:
                    out[i] = " "
                    i += 1
                    break
                if text[i] == "\n" and quote != "`":
                    break
                if out[i] != "\n":
                    out[i] = " "
                i += 1
            continue
        i += 1
    return "".join(out)


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def python_comments(text: str) -> list[tuple[int, int, str, bool]]:
    """Exact COMMENT tokens plus docstrings, via the stdlib tokenizer.

    Each entry is (start_line, end_line, body, is_docstring). A docstring is
    one entry spanning its lines, judged on its summary line alone -- a
    multi-paragraph contract is not "too short to matter".
    """
    found: list[tuple[int, int, str, bool]] = []
    try:
        for tok in tokenize.generate_tokens(iter(text.splitlines(keepends=True)).__next__):
            if tok.type == tokenize.COMMENT:
                found.append((tok.start[0], tok.start[0], tok.string, False))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass

    # docstrings: first string literal of a module/class/def, recursively
    try:
        with warnings.catch_warnings():
            # A scanned file that uses a non-raw docstring escapes would emit
            # SyntaxWarning per parse; the audit result is unaffected.
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                start = first.lineno
                end = getattr(first, "end_lineno", first.lineno) or first.lineno
                raw = ast.get_docstring(node, clean=False) or ""
                summary = next((ln.strip() for ln in raw.splitlines()
                                if ln.strip()), "")
                found.append((start, end, summary, True))
    found.sort(key=lambda t: (t[0], not t[3]))
    return found


def generic_comments(text: str, lt: str | None, bp) -> list[tuple[int, int, str, bool]]:
    if not lt and not bp:
        return []
    found: list[tuple[int, int, str, bool]] = []

    if bp:
        bs, be = bp
        if be:
            masked = mask_strings(text)
            pattern = re.compile(re.escape(bs) + r"(.*?)" + re.escape(be), re.S)
            for m in pattern.finditer(masked):
                body = text[m.start():m.end()]
                start = line_of(text, m.start())
                found.append((start, line_of(text, m.end() - 1), body, True))
            # remove block spans so line comments inside are not double counted
            masked = pattern.sub(
                lambda m: blank_keep_newlines(text[m.start():m.end()]), masked)
        else:
            masked = mask_strings(text)
    else:
        masked = mask_strings(text)

    if lt:
        for m in re.finditer(re.escape(lt) + r"[^\n]*", masked):
            ln = line_of(masked, m.start())
            nl = text.find("\n", m.start())
            body = text[m.start(): nl if nl != -1 else len(text)]
            found.append((ln, ln, body, False))
    found.sort(key=lambda t: t[0])
    return found


CODEY_AT_START = re.compile(
    r"^(?:const|let|var|function|class|def|import|export|from|return|if|for|while|"
    r"try|catch|except|raise|throw|async|await|public|private|static|void|func|fn|"
    r"print|println|console\.log|require|SELECT|INSERT|UPDATE|DELETE FROM)\b"
)


CODEY_CALL = re.compile(
    r"\b\w+\.(?:log|println|printf|map|filter|reduce|forEach|push|pop|sort)\s*\("
)


def looks_like_commented_code(body: str, ext: str) -> bool:
    """True when a comment body is plausibly dead code rather than prose.

    Only strong signals count. A weak signal here is expensive: wrapped prose
    routinely has unbalanced parentheses, so a paren check alone flagged
    dozens of legitimate explanatory comments.
    """
    s = body.strip().lstrip("/#*<!- \t").strip()
    if not s or " " not in s:
        return False
    if re.match(r"^(https?://|www\.)", s):
        return False
    if CODEY_AT_START.match(s) or CODEY_CALL.search(s):
        return True
    if re.search(r"[;{}]\s*$", s):
        return True
    if ext in {".py", ".pyi"}:
        try:
            ast.parse(s)
            return True
        except (SyntaxError, ValueError):
            pass
    return False
