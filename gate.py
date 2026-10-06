"""Layer 1 (write gate) and layer 2 (verify gate).

Layer 1 hooks ``pre_tool_call``. Hermes applies the ``modify`` directive
*before* the block/approve gate and before execution
(``hermes_cli/plugins.py`` ``_get_pre_tool_call_directive_details``), so the
rewritten text is what lands on disk, and what guardrails and approvals see.

Layer 2 hooks ``pre_verify``, which fires once per turn after code edits and
carries ``changed_paths`` (``agent/turn_stop_gates.py``). It catches what layer 1
missed -- anything written through a tool it does not cover -- by rescanning the
edited files and asking the turn to continue.

Hook payloads are keyword-only and carry no plugin context, so the context is
bound once at registration into a module slot. Callbacks take ``**kwargs``
because that is how a plugin opts into additive payload fields;
``hermes plugins doctor`` rejects a narrower signature.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from . import config as cfg
from .scanner import scan_file
from .scanner.strip import strip_comments

logger = logging.getLogger(__name__)

# The two tools a model actually writes code through. `terminal` (heredocs,
# sed -i) and `execute_code` are string-manipulation surfaces rather than comment
# surfaces; layer 2 covers what they let through, and saying so beats pretending.
WRITE_TOOLS = {"write_file": "content", "patch": "new_string"}

_state = threading.local()
_lock = threading.Lock()
_reports: dict[str, str] = {}
_BOUND_CTX = None


def bind_ctx(ctx) -> None:
    """Called once from ``register(ctx)``; makes the context reachable from hooks.

    Deliberately a plain module global, not ``threading.local``: Hermes loads
    plugins on a loader thread but invokes hook callbacks on the agent's thread,
    so a thread-local context is invisible to every real hook call and the gates
    silently no-op. The PluginContext is read-only here (settings lookups and
    the subagent handle), so sharing one instance across threads is safe.
    """
    global _BOUND_CTX
    _BOUND_CTX = ctx


def _ctx():
    return _BOUND_CTX


def _pending(session_id: str, tool_call_id: str) -> str:
    return f"{session_id or 'anon'}::{tool_call_id or 'anon'}"


def _strip(ctx, text: str, path: str):
    return strip_comments(
        text,
        path,
        protect_allowances=bool(cfg.value_of(ctx, "protect_allowances", True)),
        fail_level=str(cfg.value_of(ctx, "audit_fail_level", "DELETE")),
    )


def _write_gate(ctx, kwargs: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = str(kwargs.get("tool_name") or "")
    if tool_name not in WRITE_TOOLS or ctx is None:
        return None
    try:
        mode = cfg.mode_of(ctx, "write-gate")
        if mode in ("off", "audit"):
            return None
        args = kwargs.get("args")
        if not isinstance(args, dict):
            return None
        text = args.get(WRITE_TOOLS[tool_name])
        if not isinstance(text, str) or not text:
            return None

        # The extension decides the comment syntax, so a write to a .md path
        # must never be treated as Python.
        path = str(args.get("path") or "")
        result = _strip(ctx, text, path)
        if not result.changed:
            return None

        lines = ", ".join(f"{ln}: {kind}" for ln, kind, _t in result.removed[:8])
        if mode == "block":
            return {
                "action": "block",
                "message": (
                    f"comment-sicko: {result.removed_count} deletable comment(s) in "
                    f"{path or 'this write'} [{lines}]. Re-send without them, or run "
                    f"/comment-sicko mode write-gate modify to have them stripped instead."
                ),
            }

        patched = dict(args)
        patched[WRITE_TOOLS[tool_name]] = result.text
        key = _pending(str(kwargs.get("session_id") or ""), str(kwargs.get("tool_call_id") or ""))
        with _lock:
            _reports[key] = result.summary()
        return {
            "action": "modify",
            "args": patched,
            "source": "comment-sicko",
            "reason": f"removed {result.removed_count} deletable comment(s) before write",
        }
    except Exception:
        logger.debug("comment-sicko write gate failed; passing through", exc_info=True)
        return None


def on_pre_tool_call(**kwargs: Any) -> dict[str, Any] | None:
    """Layer 1. Rewrite a code write before it happens, or block it."""
    return _write_gate(_ctx(), kwargs)


def on_transform_tool_result(**kwargs: Any) -> Any:
    """Tell the model what layer 1 removed, so it stops writing them."""
    try:
        ctx = _ctx()
        if ctx is None:
            return None
        if str(cfg.value_of(ctx, "report_to", "tool_result")) == "off":
            return None
        key = _pending(str(kwargs.get("session_id") or ""), str(kwargs.get("tool_call_id") or ""))
        with _lock:
            note = _reports.pop(key, "")
        if not note:
            return None
        result = kwargs.get("result")
        if isinstance(result, str) and result.strip():
            return f"{note}\n{result}"
        return note
    except Exception:
        logger.debug("comment-sicko report transform failed", exc_info=True)
        return None


def on_pre_verify(**kwargs: Any) -> dict[str, Any] | None:
    """Layer 2. Ask the turn to continue when added lines still carry comments."""
    try:
        ctx = _ctx()
        if ctx is None:
            return None
        if cfg.mode_of(ctx, "verify-gate") in ("off", "audit"):
            return None
        changed = kwargs.get("changed_paths")
        if not isinstance(changed, (list, tuple)) or not changed:
            return None

        protect = bool(cfg.value_of(ctx, "protect_allowances", True))
        fail_level = str(cfg.value_of(ctx, "audit_fail_level", "DELETE"))
        hits: list[str] = []
        for raw in list(changed)[:40]:
            path = Path(str(raw))
            if not path.is_file():
                continue
            try:
                report = scan_file(path)
            except Exception:
                continue
            for finding in report.findings:
                # With protections on only mechanical DELETE findings count: a
                # REVIEW is somebody's judgement call, not the gate's.
                if protect and finding.level != "DELETE":
                    continue
                if fail_level == "REVIEW" or finding.level == "DELETE":
                    hits.append(f"{finding.path}:{finding.line} {finding.level} {finding.kind}")
        if not hits:
            return None

        shown = ", ".join(hits[:10])
        more = f" (+{len(hits) - 10} more)" if len(hits) > 10 else ""
        return {
            "action": "continue",
            "message": (
                f"comment-sicko: {len(hits)} deletable comment(s) remain in files you just "
                f"edited: {shown}{more}. Remove them now, or justify each against an "
                f"allowance: legal header, behaviour forced by something we cannot change, "
                f"public API contract, issue/ADR link, tool directive, or formatter directive."
            ),
        }
    except Exception:
        logger.debug("comment-sicko verify gate failed", exc_info=True)
        return None


def install_hooks(ctx) -> None:
    """Register both gates and bind the context the callbacks need."""
    bind_ctx(ctx)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("transform_tool_result", on_transform_tool_result)
    ctx.register_hook("pre_verify", on_pre_verify)


__all__ = ["bind_ctx", "install_hooks", "on_pre_tool_call", "on_pre_verify",
           "on_transform_tool_result"]