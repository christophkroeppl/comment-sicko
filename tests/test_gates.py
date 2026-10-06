#!/usr/bin/env python3
"""In-process gate tests: do layers 1 and 2 fire, and do they fire correctly?

Uses a fake plugin context so no model and no Hermes runtime is needed. This
proves the hook bodies behave; the e2e script proves the hooks are actually
invoked by Hermes during a real turn.

Run: ``python3 tests/test_gates.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _load  # noqa: E402

gate = _load.submodule("gate")

bind_ctx = gate.bind_ctx
on_pre_tool_call = gate.on_pre_tool_call
on_pre_verify = gate.on_pre_verify
on_transform_tool_result = gate.on_transform_tool_result

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

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value


class FakeCtx:
    """The slice of PluginContext the gates actually use."""

    def __init__(self, settings: dict[str, object] | None = None) -> None:
        self._settings = dict(settings or {})
        self.state = FakeState()

    def get_config(self, key: str, default=None):
        return self._settings.get(key, default)

    def set_config(self, key: str, value) -> None:
        self._settings[key] = value

    @property
    def name(self) -> str:
        return "comment-sicko"


NARRATION = (
    "import time\n"
    "\n"
    "\n"
    "def wait(secs):\n"
    "    # Loop over the retries\n"
    "    for _ in range(3):\n"
    "        time.sleep(secs)\n"
    "\n"
    "def total(x):\n"
    "    # Loop over the items\n"
    "    return sum(x)\n"
)

CLEAN = "def add(a, b):\n    return a + b\n"


def test_write_gate_modify() -> None:
    bind_ctx(FakeCtx({"write_gate_mode": "modify", "protect_allowances": True}))
    out = on_pre_tool_call(
        tool_name="write_file",
        args={"path": "/tmp/m.py", "content": NARRATION},
        session_id="s1", tool_call_id="t1",
    )
    check("modify directive returned", out is not None and out.get("action") == "modify", f"{out}")
    check("content stripped", out and "Loop over" not in out["args"]["content"],
          f"{out['args']['content'][:80]!r}" if out else "")
    check("code preserved", out and "time.sleep(secs)" in out["args"]["content"])
    check("line count preserved", out and
          len(out["args"]["content"].splitlines()) == len(NARRATION.splitlines()),
          f"{len(NARRATION.splitlines())} -> {len(out['args']['content'].splitlines()) if out else '?'}")
    check("source and reason recorded",
          out is not None
          and out.get("source") == "comment-sicko"
          and "removed" in (out.get("reason") or ""),
          f"source={out.get('source') if out else None} reason={out.get('reason') if out else None}")


def test_write_gate_block() -> None:
    bind_ctx(FakeCtx({"write_gate_mode": "block", "protect_allowances": True}))
    out = on_pre_tool_call(
        tool_name="write_file",
        args={"path": "/tmp/m.py", "content": NARRATION},
        session_id="s1", tool_call_id="t2",
    )
    check("block directive returned", out is not None and out.get("action") == "block", f"{out}")
    check("block message names lines", out and "deletable comment" in (out.get("message") or ""),
          f"{out}")


def test_write_gate_off() -> None:
    bind_ctx(FakeCtx({"write_gate_mode": "off"}))
    out = on_pre_tool_call(
        tool_name="write_file", args={"path": "/tmp/m.py", "content": NARRATION},
        session_id="s1", tool_call_id="t3",
    )
    check("off returns None", out is None, f"{out}")


def test_write_gate_clean_file() -> None:
    bind_ctx(FakeCtx({"write_gate_mode": "modify"}))
    out = on_pre_tool_call(
        tool_name="write_file", args={"path": "/tmp/m.py", "content": CLEAN},
        session_id="s1", tool_call_id="t4",
    )
    check("clean file untouched", out is None, f"{out}")


def test_write_gate_ignores_other_tools() -> None:
    bind_ctx(FakeCtx({"write_gate_mode": "modify"}))
    for tool, args in (
        ("read_file", {"path": "/tmp/m.py"}),
        ("search_files", {"pattern": "#"}),
        ("terminal", {"command": "echo hi"}),
        ("execute_code", {"code": "x=1"}),
    ):
        out = on_pre_tool_call(tool_name=tool, args=args, session_id="s1", tool_call_id="t5")
        check(f"{tool} ignored", out is None, f"{out}")


def test_write_gate_markdown_untouched() -> None:
    bind_ctx(FakeCtx({"write_gate_mode": "modify"}))
    md = "# Real heading\n\nProse with a #hashtag.\n"
    out = on_pre_tool_call(
        tool_name="write_file", args={"path": "/tmp/README.md", "content": md},
        session_id="s1", tool_call_id="t6",
    )
    check("markdown untouched", out is None, f"{out}")


def test_report_transform() -> None:
    bind_ctx(FakeCtx({"write_gate_mode": "modify", "report_to": "tool_result"}))
    on_pre_tool_call(
        tool_name="write_file", args={"path": "/tmp/m.py", "content": NARRATION},
        session_id="s2", tool_call_id="t7",
    )
    out = on_transform_tool_result(
        tool_name="write_file", result='{"success": true}', session_id="s2", tool_call_id="t7",
    )
    check("report appended to result", out is not None and "comment-sicko" in out, f"{out!r}")
    check("original result kept", out and "success" in out)
    again = on_transform_tool_result(
        tool_name="write_file", result="{}", session_id="s2", tool_call_id="t7",
    )
    check("report consumed once", again is None, f"{again!r}")


def test_report_transform_off() -> None:
    bind_ctx(FakeCtx({"write_gate_mode": "modify", "report_to": "off"}))
    on_pre_tool_call(
        tool_name="write_file", args={"path": "/tmp/m.py", "content": NARRATION},
        session_id="s3", tool_call_id="t8",
    )
    out = on_transform_tool_result(
        tool_name="write_file", result="{}", session_id="s3", tool_call_id="t8",
    )
    check("silent when report_to off", out is None, f"{out!r}")


def test_patch_field() -> None:
    bind_ctx(FakeCtx({"write_gate_mode": "modify"}))
    out = on_pre_tool_call(
        tool_name="patch",
        args={"path": "/tmp/m.ts", "old_string": "x", "new_string": "// Loop over stuff\nconst x = 1;\n"},
        session_id="s1", tool_call_id="t9",
    )
    check("patch new_string stripped", out is not None and "Loop over stuff"
          not in out["args"]["new_string"], f"{out}")
    check("patch keeps other args", out and out["args"].get("old_string") == "x")


def test_verify_gate() -> None:
    bind_ctx(FakeCtx({"verify_gate_mode": "modify", "protect_allowances": True}))
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "m.py"
        target.write_text(NARRATION, encoding="utf-8")
        out = on_pre_verify(changed_paths=[str(target)], coding=True, attempt=0)
    check("verify gate continues turn", out is not None and out.get("action") == "continue", f"{out}")
    check("verify message names locations", out and "m.py" in (out.get("message") or ""), f"{out}")


def test_verify_gate_clean() -> None:
    bind_ctx(FakeCtx({"verify_gate_mode": "modify", "protect_allowances": True}))
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "clean.py"
        target.write_text(CLEAN, encoding="utf-8")
        out = on_pre_verify(changed_paths=[str(target)], coding=True, attempt=0)
    check("verify gate silent on clean file", out is None, f"{out}")


def test_verify_gate_off() -> None:
    bind_ctx(FakeCtx({"verify_gate_mode": "off"}))
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "m.py"
        target.write_text(NARRATION, encoding="utf-8")
        out = on_pre_verify(changed_paths=[str(target)], coding=True, attempt=0)
    check("verify gate off stays silent", out is None, f"{out}")


def test_verify_gate_no_paths() -> None:
    bind_ctx(FakeCtx({"verify_gate_mode": "modify"}))
    out = on_pre_verify(changed_paths=[], coding=True, attempt=0)
    check("verify gate silent without changed paths", out is None, f"{out}")


def main() -> int:
    print("gates (in-process)")
    for fn in (
        test_write_gate_modify, test_write_gate_block, test_write_gate_off,
        test_write_gate_clean_file, test_write_gate_ignores_other_tools,
        test_write_gate_markdown_untouched, test_report_transform,
        test_report_transform_off, test_patch_field, test_verify_gate,
        test_verify_gate_clean, test_verify_gate_off, test_verify_gate_no_paths,
    ):
        fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all gate tests pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())