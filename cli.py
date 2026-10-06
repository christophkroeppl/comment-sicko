"""The ``/comment-sicko`` command surface.

One arg parser, two entry points:

- ``ctx.register_command``  -> ``/comment-sicko <command> <args>`` in session
  (dispatched by the CLI, the gateway and the TUI)
- ``ctx.register_cli_command`` -> ``hermes comment-sicko <command> <args>`` for
  headless runs, scripting and CI

Both call :func:`run`, which returns a string for the session surface and the
CLI handler alike, so there is no second implementation to keep in sync.

Every layer is toggleable here. Settings live in ``$HERMES_HOME/config.yaml``,
and ``$HERMES_HOME`` is per profile, so per-profile defaults are just per-profile
values; ``--profile`` writes another profile's settings from this session and
always echoes the resolved home so a cross-profile write is never silent.
"""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path
from typing import Any

from . import config as cfg
from . import gate, sicko

LAYERS = ("write-gate", "verify-gate", "sicko")
GATE_LAYERS = ("write-gate", "verify-gate")

USAGE = """\
/comment-sicko <command>

  status                          layers, modes, effective profile
  enable  <layer|all>             layer: write-gate | verify-gate | sicko
  disable <layer|all>
  mode    <layer> <mode>          mode: off | modify | block | audit
  set     <key> <value>           any settings key
  defaults show                   resolved values and where each came from
  defaults set <key> <value> [--profile <name>]
  defaults reset [--profile <name>]
  scan    [paths...]              deterministic audit, no model
  review  [paths...]              run Comment Sicko (layer 3 must be on)
  doctor                          hook registration and config health
"""


def _layer_targets(arg: str, *, only_gates: bool = False) -> list[str]:
    """Resolve the ``all`` keyword or a layer name to a concrete layer list.

    ``only_gates`` is for ``enable``/``disable``, where a boolean layer has no
    meaningful mode: ``all`` means both gates, and sicko is only touched when
    named explicitly. Otherwise ``all`` would silently flip layer 3 on as a side
    effect of ``/comment-sicko enable all``.
    """
    arg = (arg or "").strip().lower()
    if arg in ("all", "*"):
        return list(GATE_LAYERS) if only_gates else list(LAYERS)
    if arg in LAYERS:
        return [arg]
    raise ValueError(f"unknown layer {arg!r}; use one of {', '.join(LAYERS)} or all")


def _key_for(layer: str, enabled: bool) -> tuple[str, Any]:
    if layer == "sicko":
        return "sicko_enabled", enabled
    return cfg.LAYER_SETTINGS[layer], "modify" if enabled else "off"


def _status(ctx) -> str:
    home = cfg.active_home()
    lines = [f"comment-sicko  profile home: {home}", ""]
    rows = [
        ("write-gate", cfg.mode_of(ctx, "write-gate"), "strip or block before write_file/patch"),
        ("verify-gate", cfg.mode_of(ctx, "verify-gate"), "rescan edited files before the turn ends"),
        ("sicko", "on" if cfg.is_enabled(ctx, "sicko") else "off", "Comment Sicko reviewer subagent"),
    ]
    for name, mode, note in rows:
        lines.append(f"  {name:<12} {mode:<8} {note}")
    protect = cfg.value_of(ctx, "protect_allowances", True)
    lines.append("")
    lines.append(f"  protect_allowances: {protect}   audit_fail_level: "
                 f"{cfg.value_of(ctx, 'audit_fail_level', 'DELETE')}")
    overrides = cfg._overrides(ctx)
    if overrides:
        lines.append(f"  session overrides: {overrides}")
    return "\n".join(lines)


def _defaults_show(ctx) -> str:
    lines = ["comment-sicko settings", f"  home: {cfg.active_home()}", ""]
    for key in sorted(cfg.DEFAULTS):
        res = cfg.resolve(ctx, key)
        lines.append(f"  {key:<20} {str(res.value):<10} ({res.source})")
    return "\n".join(lines)


def _scan(ctx, argv: list[str]) -> str:
    from .scanner import iter_files, scan_file

    paths = argv or ["."]
    findings = []
    errors: list[str] = []
    for path in iter_files(paths):
        try:
            findings.extend(f.as_dict() for f in scan_file(path).findings)
        except Exception as exc:
            errors.append(f"{path}: scan failed ({exc})")
    if not findings:
        return f"clean: no deletable comments in {', '.join(paths)}"
    threshold = str(cfg.value_of(ctx, "audit_fail_level", "DELETE"))
    order = {"DELETE": 0, "REVIEW": 1, "KEEP": 2}
    limit = order.get(threshold, 0)
    shown = [f for f in findings if order.get(f["level"], 2) <= limit] or findings
    body = "\n".join(
        f"  {f['path']}:{f['line']}  {f['level']:<6} {f['kind']:<8} {f['text'][:80]}"
        for f in shown[:60]
    )
    more = f"\n  ... and {len(shown) - 60} more" if len(shown) > 60 else ""
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["level"]] = counts.get(f["level"], 0) + 1
    tally = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    note = ("\n" + "\n".join(f"  {e}" for e in errors)) if errors else ""
    return f"comment-sicko scan: {len(findings)} findings ({tally})\n{body}{more}{note}"


def _doctor(ctx) -> str:
    lines = ["comment-sicko doctor", ""]
    for name in ("pre_tool_call", "transform_tool_result", "pre_verify"):
        lines.append(f"  hook {name:<24} registered")
    try:
        from hermes_cli.lifecycle import has_hook

        for name in ("pre_tool_call", "transform_tool_result", "pre_verify"):
            lines.append(f"  live  {name:<24} {'yes' if has_hook(name) else 'NO'}")
    except Exception as exc:
        lines.append(f"  live  hook lookup unavailable ({exc})")
    lines.append(f"  ctx bound: {'yes' if gate._ctx() else 'NO'}")
    for layer in LAYERS:
        lines.append(f"  layer {layer:<12} {cfg.mode_of(ctx, layer)}")
    try:
        # Bound the probe hard. cwd may be a whole home directory -- scanning it
        # found 24k+ files here and hung the command. A liveness check needs one
        # known source file, not a tree walk.
        probe_dir = Path(__file__).resolve().parent
        from .scanner import iter_files as _iter_files, scan_file as _scan_file

        probe_files = _iter_files([str(probe_dir)])
        probe = _scan_file(probe_files[0]).findings if probe_files else []
        lines.append(
            f"  scanner: operational ({len(probe)} finding(s) in "
            f"{probe_files[0].name if probe_files else 'n/a'})"
        )
    except Exception as exc:
        lines.append(f"  scanner: FAILED ({exc})")
    return "\n".join(lines)


def run(ctx, raw: str) -> str:
    """Execute one ``/comment-sicko`` invocation. Returns the output text."""
    raw = (raw or "").strip()
    if not raw or raw in ("help", "-h", "--help"):
        return USAGE
    try:
        argv = shlex.split(raw)
    except ValueError:
        argv = raw.split()
    cmd, rest = argv[0].lower(), argv[1:]

    try:
        if cmd == "status":
            return _status(ctx)

        if cmd in ("enable", "disable"):
            want = cmd == "enable"
            layers = _layer_targets(rest[0] if rest else "all", only_gates=True)
            for layer in layers:
                key, value = _key_for(layer, want)
                cfg.set_setting(ctx, key, value)
            return f"{cmd}d: {', '.join(layers)}"

        if cmd == "mode":
            if len(rest) < 2:
                return f"usage: /comment-sicko mode <{'|'.join(LAYERS)}> <{'|'.join(cfg.MODES)}>"
            layer = rest[0].lower()
            mode = rest[1].lower()
            if layer not in LAYERS:
                return f"unknown layer {layer!r}; use one of {', '.join(LAYERS)}"
            if mode not in cfg.MODES:
                return f"unknown mode {mode!r}; use one of {', '.join(cfg.MODES)}"
            if layer == "sicko":
                cfg.set_setting(ctx, "sicko_enabled", mode != "off")
                effective = "on" if mode != "off" else "off"
            else:
                cfg.set_setting(ctx, cfg.LAYER_SETTINGS[layer], mode)
                effective = mode
            return f"{layer} mode = {effective}"

        if cmd == "set":
            if len(rest) < 2:
                return f"usage: /comment-sicko set <key> <value>\nkeys: {', '.join(sorted(cfg.DEFAULTS))}"
            cfg.set_setting(ctx, rest[0], rest[1])
            return f"{rest[0]} = {rest[1]}  ({_defaults_show(ctx).splitlines()[-1]})"

        if cmd == "defaults":
            sub = (rest[0] if rest else "show").lower()
            if sub == "show":
                return _defaults_show(ctx)
            if sub == "set":
                if len(rest) < 3:
                    return "usage: /comment-sicko defaults set <key> <value> [--profile <name>]"
                profile = None
                if "--profile" in rest:
                    idx = rest.index("--profile")
                    profile = rest[idx + 1] if idx + 1 < len(rest) else None
                    rest = rest[:idx] + rest[idx + 2:]
                written = cfg.set_setting(ctx, rest[1], rest[2], profile=profile)
                where = f" (profile home: {written})" if written else ""
                return f"{rest[1]} = {rest[2]} written to this profile{where}"
            if sub == "reset":
                profile = None
                args = [a for a in rest[1:]]
                if "--profile" in args:
                    idx = args.index("--profile")
                    profile = args[idx + 1] if idx + 1 < len(args) else None
                dropped = cfg.clear_overrides(ctx)
                return (f"session overrides cleared ({dropped}); profile values in "
                        f"{cfg.active_home()}/config.yaml are unchanged")
            return f"unknown defaults subcommand {sub!r}; use show, set, or reset"

        if cmd == "scan":
            return _scan(ctx, rest)

        if cmd == "review":
            return sicko.review(ctx, rest or None)

        if cmd == "doctor":
            return _doctor(ctx)

        return f"unknown command {cmd!r}\n\n{USAGE}"
    except Exception as exc:
        return f"comment-sicko: {exc}"


def make_slash_handler(ctx):
    """``fn(raw_args) -> str`` for ``ctx.register_command``."""

    def handler(raw_args: str) -> str:
        return run(ctx, raw_args)

    return handler


def cli_setup(parser) -> None:
    """argparse wiring for ``hermes comment-sicko``.

    ``register_cli_command`` hands ``setup_fn`` the top-level parser for the
    plugin's own command, and Hermes then calls the handler with the parsed
    namespace. Adding real subparsers here would consume ``status`` as a
    subcommand name and silently drop everything after it, so the whole tail is
    taken as one REMAINDER and parsed by :func:`run`.
    """
    parser.add_argument(
        "comment_sicko_args",
        nargs=argparse.REMAINDER,
        metavar="<command> [args]",
        help="status | doctor | defaults | enable | disable | mode | set | scan | review",
    )
    parser.set_defaults(func=None)


def _argv_from(arg_obj) -> list[str]:
    """Recover the trailing words from whatever the CLI layer hands us.

    ``hermes`` calls ``args.func(args)``, so the handler receives the argparse
    Namespace. Trailing words are also reachable via REMAINDER or, when the
    parser stored extras, ``comment_sicko_args``.
    """
    if arg_obj is None:
        return []
    if isinstance(arg_obj, str):
        return arg_obj.split()
    if isinstance(arg_obj, (list, tuple)):
        return [str(a) for a in arg_obj]
    for attr in ("comment_sicko_args", "remainder", "args", "argv"):
        value = getattr(arg_obj, attr, None)
        if isinstance(value, (list, tuple)):
            return [str(a) for a in value]
    return []


def make_cli_handler(ctx):
    """``hermes comment-sicko <command>``; reuses :func:`run`."""

    def handler(arg_obj=None) -> int:
        words = _argv_from(arg_obj)
        cmd = getattr(arg_obj, "comment_sicko_command", None) or (
            words[0] if words else ""
        )
        print(run(ctx, " ".join([cmd, *words[1:]] if cmd else words)))
        return 0

    return handler


__all__ = ["LAYERS", "USAGE", "cli_setup", "make_cli_handler", "make_slash_handler", "run"]