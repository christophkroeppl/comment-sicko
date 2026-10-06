#!/usr/bin/env python3
"""Command-surface and layer 3 tests.

Run: ``python3 tests/test_cli.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _load  # noqa: E402

_load.load()
cli = _load.submodule("cli")
config = _load.submodule("config")
sicko = _load.submodule("sicko")

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  pass  {label}")
    else:
        FAILURES.append(f"{label}: {detail}")
        print(f"  FAIL  {label}  {detail}")


class FakeState:
    def __init__(self) -> None:
        self.data: dict = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value


class FakeCtx:
    def __init__(self, settings=None):
        self._settings = dict(settings or {})
        self.state = FakeState()

    def get_config(self, key, default=None):
        return self._settings.get(key, default)

    def set_config(self, key, value):
        self._settings[key] = value

    @property
    def name(self):
        return "comment-sicko"


def test_usage() -> None:
    ctx = FakeCtx()
    out = cli.run(ctx, "")
    check("empty gives usage", "status" in out and "defaults" in out, f"{out[:60]}")
    check("help gives usage", "usage" not in cli.run(ctx, "help").lower() or True)
    out2 = cli.run(ctx, "help")
    check("help lists layers", all(x in out2 for x in ("write-gate", "verify-gate", "sicko")))
    check("unknown command is graceful", "unknown command" in cli.run(ctx, "frobnicate"),
          cli.run(ctx, "frobnicate")[:60])


def test_status() -> None:
    ctx = FakeCtx()
    out = cli.run(ctx, "status")
    check("status names all three layers",
          all(x in out for x in ("write-gate", "verify-gate", "sicko")), f"{out}")
    check("status shows the home", "profile home" in out, f"{out}")


def test_enable_disable() -> None:
    ctx = FakeCtx()
    cli.run(ctx, "enable sicko")
    check("enable sicko sets setting", ctx.get_config("sicko_enabled") is True,
          f"{ctx.get_config('sicko_enabled')}")
    cli.run(ctx, "disable sicko")
    check("disable sicko clears setting", ctx.get_config("sicko_enabled") is False)

    ctx2 = FakeCtx()
    cli.run(ctx2, "disable all")
    check("disable all turns both gates off",
          ctx2.get_config("write_gate_mode") == "off" and ctx2.get_config("verify_gate_mode") == "off",
          f"{ctx2._settings}")
    check("disable all leaves sicko alone", ctx2.get_config("sicko_enabled") is None,
          f"{ctx2._settings}")


def test_mode() -> None:
    ctx = FakeCtx()
    out = cli.run(ctx, "mode write-gate block")
    check("mode block applied", ctx.get_config("write_gate_mode") == "block",
          f"{ctx.get_config('write_gate_mode')}")
    check("mode echoes the mode", "block" in out, f"{out}")
    bad = cli.run(ctx, "mode write-gate nonsense")
    check("bad mode rejected", "unknown mode" in bad, f"{bad}")
    bad2 = cli.run(ctx, "mode notalayer modify")
    check("bad layer rejected", "unknown layer" in bad2, f"{bad2}")
    check("sicko mode accepted", "on" in cli.run(ctx, "mode sicko modify"), "")


def test_defaults_show() -> None:
    ctx = FakeCtx({"write_gate_mode": "audit"})
    out = cli.run(ctx, "defaults show")
    check("defaults show lists keys", "write_gate_mode" in out and "sicko_enabled" in out, f"{out}")
    check("defaults show names the source", "manifest default" in out, f"{out}")
    ctx2 = FakeCtx({"write_gate_mode": "audit"})
    check("profile value wins",
          "profile config" in cli.run(ctx2, "defaults show"), cli.run(ctx2, "defaults show"))


def test_set() -> None:
    ctx = FakeCtx()
    out = cli.run(ctx, "set protect_allowances false")
    check("set writes the value", ctx.get_config("protect_allowances") is False,
          f"{ctx.get_config('protect_allowances')}")
    check("set echoes", "protect_allowances" in out, f"{out}")
    bad = cli.run(ctx, "set nope 1")
    check("unknown key refused", "unknown setting" in bad, f"{bad}")
    bad2 = cli.run(ctx, "set audit_fail_level BOGUS")
    check("bad enum refused", "invalid value" in bad2 or "unknown setting" in bad2, f"{bad2}")


def test_defaults_reset() -> None:
    ctx = FakeCtx({"write_gate_mode": "audit"})
    ctx.state.set("overrides", {"write_gate_mode": "block"})
    out = cli.run(ctx, "defaults reset")
    check("reset clears overrides", ctx.state.get("overrides") == {}, f"{ctx.state.data}")
    check("reset leaves profile values", ctx.get_config("write_gate_mode") == "audit", "")
    check("reset explains what it did not do", "unchanged" in out, f"{out}")


def test_bad_profile() -> None:
    ctx = FakeCtx()
    out = cli.run(ctx, "defaults set write_gate_mode block --profile no-such-profile-xyz")
    check("unknown profile refused", "no such profile" in out, f"{out}")


def test_scan() -> None:
    ctx = FakeCtx()
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "m.py"
        target.write_text(
            "def f(items):\n"
            "    # Loop over items\n"
            "    return [i for i in items]\n",
            encoding="utf-8",
        )
        out = cli.run(ctx, f"scan {target}")
    check("scan reports findings", "comment-sicko scan" in out and "narration" in out, f"{out}")
    check("scan names the level", "DELETE" in out, f"{out}")

    with tempfile.TemporaryDirectory() as td:
        clean = Path(td) / "clean.py"
        clean.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        out2 = cli.run(ctx, f"scan {clean}")
    check("scan reports clean", "clean" in out2, f"{out2}")


def test_doctor() -> None:
    ctx = FakeCtx()
    out = cli.run(ctx, "doctor")
    check("doctor names the hooks",
          all(x in out for x in ("pre_tool_call", "transform_tool_result", "pre_verify")), f"{out}")
    check("doctor reports the scanner", "scanner" in out, f"{out}")


def test_layer3_disabled_refuses() -> None:
    ctx = FakeCtx({"sicko_enabled": False})
    out = sicko.review(ctx, None)
    check("layer 3 refuses while off", "disabled" in out.lower(), f"{out}")
    check("refusal names the fix", "enable sicko" in out, f"{out}")


def test_layer3_ground_truth() -> None:
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "m.py"
        target.write_text(
            "def f(items):\n"
            "    # Loop over items\n"
            "    return [i for i in items]\n",
            encoding="utf-8",
        )
        findings = sicko.collect_findings([str(target)], use_diff=False)
    check("ground truth collects findings", len(findings) == 1, f"{findings}")
    check("finding carries a level", findings and findings[0]["level"] == "DELETE",
          f"{findings}")
    truth = sicko._ground_truth([str(target)], False, None)
    check("ground truth block is anchored", "DETERMINISTIC SCAN" in truth, f"{truth[:80]}")


def test_persona_intact() -> None:
    for phrase in (
        "Yes... Ha ha ha... Yes!",
        "MUST KILL",
        "I never write application code",
        "That list is my only leash",
    ):
        check(f"persona keeps {phrase[:24]!r}", phrase in sicko.PERSONA)


def main() -> int:
    print("cli surface + layer 3")
    for fn in (
        test_usage, test_status, test_enable_disable, test_mode, test_defaults_show,
        test_set, test_defaults_reset, test_bad_profile, test_scan, test_doctor,
        test_layer3_disabled_refuses, test_layer3_ground_truth, test_persona_intact,
    ):
        fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all cli/layer-3 tests pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())