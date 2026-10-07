"""Layer 3: Comment Sicko, the adversarial reviewer.

Upstream this is a persona prompt (``pstack/agents/comment-sicko.md``, MIT,
Lauren Tan) that a host agent hands to a fresh read-only reviewer subagent. The
Hermes pstack port ships no equivalent, so it is built here: the persona text is
carried verbatim as ``PERSONA``, and the deterministic scanner's findings are
attached as ground truth so the review is anchored rather than vibes.

Report-only by construction. The child gets the ``file`` toolset (read and
search, plus the write tools that are inherent to that toolset) and is told
explicitly never to edit; the stronger guarantee is that nothing in this module
edits anything, and the mechanical check in the test suite asserts the diff is
unchanged after a review.

Off by default, because a spawning reviewer costs a model call. The tool is
registered unconditionally and refuses while disabled, so toggling it takes
effect immediately without a reload.
"""

from __future__ import annotations

import logging
from typing import Any

from . import config as cfg
from .scanner import git_diff_lines, iter_files, scan_file

logger = logging.getLogger(__name__)

PLUGIN_ID = "comment-sicko"

PERSONA = """\
Yes... Ha ha ha... Yes!

I hate comments. Feed me the parent scoped files or diff. If none exists, feed \
me the current diff against `main`. Narration, banners, commented-out corpses, \
workaround sermons. I want them all.

Only these exceptions get to crawl away.

- Legal or license headers.
- Non-obvious behavior forced by an external dependency, platform, vendor, or \
protocol we cannot reshape. Surprises in our own code are meat. Kill them and \
mark the exact symbol `MUST KILL` for rename, extract, type, or rearchitecture \
that makes the behavior obvious without prose.
- `// prettier-ignore`. Lint suppressions survive only when their rule is faulty, \
pedantic, or style-only.
- Doc comments that define a public API contract.
- Issue or RFC links that explain a constraint code cannot express.

That list is my only leash. When I am not sure a keep clause applies, the \
comment dies. Everything else is meat.

`eslint-disable`, `@ts-ignore`, `@ts-expect-error`, and similar suppressions \
stink. Look up the rule. If it catches real bugs or protects correctness or \
safety, kill the suppression and mark the exact guilty symbol `MUST KILL`.

`IMPORTANT`, `do not remove`, `too risky`, `fine for now`, and long \
justifications are scent, not conviction. Before judging, I read nearby code. \
If its claim is not obvious there, it dies. Only a foreign keep-list gotcha \
proven true today on a live path crawls away. Our-code surprises die with the \
reshape flag above. Doubt after the hunt is meat.

A long justification without a proven keep-list exception is a confession. Kill \
it. Never polish meat into a shorter alibi. Mark the exact guilty symbol \
`MUST KILL`. My kill ends there. I do not touch the code.

Every flag names code inside the scope and tells the truth. I invent nothing. I \
touch comments and identify refactor targets. I never write application code.

Report only. Name touched files, deletion count, `MUST KILL` flags with one line \
each, and skips.
"""

DISABLED_HINT = (
    "comment-sicko: layer 3 is disabled. Enable it with "
    "/comment-sicko enable sicko (or hermes comment-sicko enable sicko)."
)


def collect_findings(paths: list[str] | None = None, *, use_diff: bool = True,
                     base: str | None = None) -> list[dict[str, Any]]:
    """Deterministic findings the reviewer is anchored to."""
    out: list[dict[str, Any]] = []
    if use_diff:
        mapping = git_diff_lines(base)
        for path, lines in mapping.items():
            from pathlib import Path

            fp = Path(path)
            if fp.is_file():
                for f in scan_file(fp, only_lines=lines).findings:
                    out.append(f.as_dict())
        if out:
            return out
    targets = paths or ["."]
    for path in iter_files(targets):
        for f in scan_file(path).findings:
            out.append(f.as_dict())
    return out


def _ground_truth(paths: list[str] | None, use_diff: bool, base: str | None,
                  limit: int = 120) -> str:
    findings = collect_findings(paths, use_diff=use_diff, base=base)
    if not findings:
        return (
            "DETERMINISTIC SCAN: no findings. The scanner only catches mechanical "
            "cases, so read the code yourself and do not treat this as clearance."
        )
    lines = [
        f"  {f['path']}:{f['line']}  {f['level']}  {f['kind']}  {f['text'][:100]}"
        for f in findings[:limit]
    ]
    more = f"\n  ... and {len(findings) - limit} more" if len(findings) > limit else ""
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["level"]] = counts.get(f["level"], 0) + 1
    tally = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    return (
        f"DETERMINISTIC SCAN ({len(findings)} findings: {tally}). Treat these as "
        f"confirmed, not candidates: each is already classified.\n" + "\n".join(lines) + more
    )


def review(ctx, paths: list[str] | None = None, *, use_diff: bool = True,
           base: str | None = None) -> str:
    """Run Comment Sicko over a scope. Returns its report, or why it did not run."""
    if not cfg.is_enabled(ctx, "sicko"):
        return DISABLED_HINT

    scope = " ".join(paths) if paths else "(no paths given; use the working diff)"
    goal = (
        "You are Comment Sicko. Report only. Do not edit any file, do not write "
        "application code, and do not run formatters or fixers.\n\n"
        f"{PERSONA}\n\n"
        f"SCOPE: {scope}\n\n"
        f"{_ground_truth(paths, use_diff, base)}\n\n"
        "Now judge every comment in scope against the keep-list above, including "
        "ones the scanner rated KEEP or missed entirely. Report: touched files, "
        "deletion count, MUST KILL flags with one line each naming the exact symbol, "
        "and skips with the allowance each one claimed."
    )
    context = (
        "Read-only review. Use the read and search tools to inspect the scoped code. "
        "Do not modify anything. Your final message is the report; it is the only "
        "thing the caller reads."
    )
    try:
        from agent.subagent_lifecycle import SubagentLaunchRequest

        service = ctx.subagent_lifecycle
        handle = service.launch(
            SubagentLaunchRequest(
                goal=goal,
                context=context,
                role="leaf",
                correlation_id="comment-sicko-review",
                allowed_toolsets=("file",),
            )
        )
    except Exception as exc:
        logger.debug("comment-sicko: subagent launch failed", exc_info=True)
        return (
            f"comment-sicko: could not launch the reviewer ({exc}). It must be called "
            "from inside an active agent turn."
        )

    try:
        service.wait(handle, timeout_seconds=900)
    except Exception:
        logger.debug("comment-sicko: wait failed", exc_info=True)
    try:
        return str(service.result(handle) or "comment-sicko: reviewer returned nothing.")
    except Exception as exc:
        return f"comment-sicko: reviewer did not complete ({exc})."


def tool_schema() -> dict[str, Any]:
    return {
        "name": "comment_sicko",
        "description": (
            "Run the Comment Sicko reviewer over the working diff or given paths. "
            "Report-only: it names comments to delete and flags symbols MUST KILL "
            "for reshape. Disabled by default; enable with /comment-sicko enable sicko."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files or directories to review. Empty means the working diff.",
                },
                "use_diff": {
                    "type": "boolean",
                    "description": "Prefer added lines of the working diff (default true).",
                    "default": True,
                },
                "base": {
                    "type": "string",
                    "description": "Diff base, e.g. 'main'. Empty means HEAD.",
                },
            },
        },
    }


def make_handler(ctx):
    """Bind the plugin context into a tool handler.

    The dispatcher calls ``handler(args, **kwargs)`` with the tool arguments as
    a positional dict (tools/registry.py:909), so the first parameter must be
    positional — a ``**kwargs``-only signature raises
    "takes 0 positional arguments but 1 was given" on every call.
    """

    def handler(args: Any = None, **kwargs: Any) -> str:
        if not isinstance(args, dict):
            args = {k: v for k, v in kwargs.items() if k in {"paths", "use_diff", "base"}}
        paths = args.get("paths") or None
        if isinstance(paths, str):
            paths = [paths]
        return review(
            ctx,
            [str(p) for p in paths] if paths else None,
            use_diff=bool(args.get("use_diff", True)),
            base=str(args.get("base") or "") or None,
        )

    return handler


__all__ = ["DISABLED_HINT", "PERSONA", "collect_findings", "make_handler",
           "review", "tool_schema"]