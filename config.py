"""Settings resolution for the comment-sicko plugin.

One resolver, three entry points (``/comment-sicko``, ``hermes comment-sicko``,
the Desktop settings form) so every surface agrees on what a setting is.

Resolution order, highest first:

1. session override  -- ``ctx.state`` key ``overrides``, set by ``/comment-sicko mode``
2. profile config    -- ``plugins.entries.comment-sicko.settings.<key>`` in
                        ``$HERMES_HOME/config.yaml``; ``$HERMES_HOME`` is per
                        profile, so per-profile defaults need no extra machinery
3. manifest default  -- ``plugin.yaml`` ``config_schema`` ``default:``

A malformed stored value never raises into a hook: it logs once and falls
through to the next source. A hook that raises is a hook that does not run.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODES = ("off", "modify", "block", "audit")

# Manifest defaults. Keep in sync with plugin.yaml config_schema; the manifest is
# the source of truth and these are the fallback when it cannot be read.
DEFAULTS: dict[str, Any] = {
    "write_gate_mode": "modify",
    "verify_gate_mode": "modify",
    "sicko_enabled": False,
    "protect_allowances": True,
    # REVIEW, not DELETE: across the Hermes tree 98.3% of findings classify as
    # REVIEW (narration, dead-code-block, temporary framing) and only 1.7% as
    # DELETE, so a DELETE threshold leaves the gate inert on the comments it
    # exists to remove. Set DELETE to restrict it to the mechanical findings.
    "audit_fail_level": "REVIEW",
    "report_to": "tool_result",
}

# Per-layer setting name -> the layer label used on the command surface.
LAYER_SETTINGS: dict[str, str] = {
    "write-gate": "write_gate_mode",
    "verify-gate": "verify_gate_mode",
}

# Layer label -> enabled-ness implied by its mode.
_ACTIVE_MODES = {"modify", "block", "audit"}

_lock = threading.Lock()
_warned: set[str] = set()


@dataclass(frozen=True)
class Resolved:
    """One setting, with the provenance of the value that won."""

    key: str
    value: Any
    source: str


def _coerce(key: str, raw: Any, fallback: Any) -> Any:
    """Validate a stored value against its declared type; return the fallback on mismatch."""
    if key.endswith("_mode") or key == "report_to" or key == "audit_fail_level":
        allowed = MODES if key.endswith("_mode") else (
            ("DELETE", "REVIEW", "KEEP") if key == "audit_fail_level" else ("tool_result", "off")
        )
        text = _norm_mode(raw) if key.endswith("_mode") else str(raw).strip().lower()
        if text in allowed:
            return text
    elif key == "sicko_enabled" or key == "protect_allowances":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("true", "yes", "on", "1"):
            return True
        if text in ("false", "no", "off", "0"):
            return False
    else:
        return raw
    with _lock:
        first = key not in _warned
        if first:
            _warned.add(key)
    if first:
        logger.warning("comment-sicko: invalid value %r for %r; using %r", raw, key, fallback)
    return fallback


def _manifest_defaults() -> dict[str, Any]:
    """Read declared defaults from plugin.yaml, falling back to DEFAULTS."""
    try:
        from pathlib import Path

        import yaml

        path = Path(__file__).resolve().parent / "plugin.yaml"
        schema = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("config_schema") or {}
        out = dict(DEFAULTS)
        for key, spec in schema.items():
            if isinstance(spec, dict) and "default" in spec:
                out[key] = spec["default"]
        return out
    except Exception:
        logger.debug("comment-sicko: plugin.yaml unreadable; using built-in defaults", exc_info=True)
        return dict(DEFAULTS)


def _overrides(ctx) -> dict[str, Any]:
    try:
        value = ctx.state.get("overrides", {})
        return dict(value) if isinstance(value, dict) else {}
    except Exception:
        return {}


def resolve(ctx, key: str) -> Resolved:
    """Resolve one setting with its provenance."""
    manifest = _manifest_defaults()
    fallback = manifest.get(key, DEFAULTS.get(key))

    stored = None
    try:
        stored = ctx.get_config(key)
    except Exception:
        logger.debug("comment-sicko: get_config(%r) failed", key, exc_info=True)
    if stored is not None:
        return Resolved(key, _coerce(key, stored, fallback), "profile config")

    override = _overrides(ctx).get(key)
    if override is not None:
        return Resolved(key, _coerce(key, override, fallback), "session override")

    return Resolved(key, fallback, "manifest default")


def value_of(ctx, key: str, default: Any = None) -> Any:
    """Resolved value only, for hot paths that do not care about provenance."""
    result = resolve(ctx, key)
    return default if result.value is None else result.value


def _norm_mode(value: Any) -> str:
    """Normalise a mode value to one of MODES.

    YAML reads a bare ``off``/``no``/``on`` as a boolean, so a config written as
    ``write_gate_mode: off`` arrives as ``False`` and ``str(False)`` is
    ``"false"`` -- which is not a mode, so the layer neither acted nor was
    recognised as disabled. Map the boolean spellings back onto modes.
    """
    if isinstance(value, bool):
        return "modify" if value else "off"
    text = str(value).strip().lower()
    if text in ("false", "no", "0", "none", "disabled"):
        return "off"
    if text in ("true", "yes", "1", "enabled"):
        return "modify"
    return text


def is_enabled(ctx, layer: str) -> bool:
    """True when the layer should act this turn."""
    if layer == "sicko":
        return bool(value_of(ctx, "sicko_enabled", False))
    key = LAYER_SETTINGS.get(layer)
    if key is None:
        return False
    return _norm_mode(value_of(ctx, key, "off")) in _ACTIVE_MODES


def mode_of(ctx, layer: str) -> str:
    """``off | modify | block | audit`` for a layer."""
    if layer == "sicko":
        return "modify" if is_enabled(ctx, "sicko") else "off"
    key = LAYER_SETTINGS.get(layer, "")
    return _norm_mode(value_of(ctx, key, "off"))


def _has_profiles_module() -> bool:
    try:
        import hermes_cli.profiles  # noqa: F401

        return True
    except Exception:
        return False


def _profile_dir_guess(name: str) -> Path:
    """Conventional profile home, used when ``hermes_cli.profiles`` is absent.

    Mirrors ``hermes_cli/profiles.py``: ``default`` is the base home, anything
    else lives under ``<home>/profiles/<name>``.
    """
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return home if name == "default" else home / "profiles" / name


def set_setting(ctx, key: str, value: Any, *, profile: str | None = None) -> Path | None:
    """Write one setting, optionally into another profile.

    Returns the resolved HERMES_HOME that was written, so the caller can echo
    where a cross-profile write landed. Raises on unknown key or bad value --
    the command surface turns that into a message.
    """
    from pathlib import Path

    if key not in DEFAULTS:
        raise ValueError(f"unknown setting {key!r}; known: {', '.join(sorted(DEFAULTS))}")

    coerced = _coerce(key, value, DEFAULTS[key])
    if coerced == DEFAULTS[key] and str(value).strip().lower() not in str(DEFAULTS[key]).lower():
        # _coerce fell back; reject rather than silently writing the default.
        raise ValueError(f"invalid value {value!r} for {key!r}; see /comment-sicko defaults show")

    if profile:
        name = str(profile).strip()
        if not _has_profiles_module():
            home = _profile_dir_guess(name)
            if not home.is_dir():
                raise ValueError(f"no such profile: {name}")
        else:
            from hermes_cli.profiles import get_profile_dir, profile_exists

            try:
                if not profile_exists(name):
                    raise ValueError(f"no such profile: {name}")
                home = get_profile_dir(name)
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(f"cannot resolve profile {name!r}: {exc}") from exc
        # Imported last: the override helpers only exist inside a Hermes runtime,
        # and a bad profile name must be reported as such rather than as an
        # ImportError about the runtime.
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        token = set_hermes_home_override(str(home))
        try:
            ctx.set_config(key, coerced)
        finally:
            reset_hermes_home_override(token)
        return Path(home)

    ctx.set_config(key, coerced)
    return None


def clear_overrides(ctx) -> int:
    """Drop every session override, returning how many were dropped."""
    try:
        current = _overrides(ctx)
        if not current:
            return 0
        ctx.state.set("overrides", {})
        return len(current)
    except Exception:
        logger.debug("comment-sicko: clearing overrides failed", exc_info=True)
        return 0


def set_override(ctx, key: str, value: Any) -> None:
    """Record a session-only override, shadowing the profile value."""
    coerced = _coerce(key, value, DEFAULTS.get(key))
    with _lock:
        current = _overrides(ctx)
        current[key] = coerced
    ctx.state.set("overrides", current)


def active_home() -> Path:
    """The HERMES_HOME these settings resolve against.

    Falls back to ``~/.hermes`` when Hermes' own module is not importable, so
    the command surface still answers in a test or a bare interpreter instead of
    raising on every ``status``.
    """
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home()
    except Exception:
        logger.debug("comment-sicko: hermes_constants unavailable", exc_info=True)
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")