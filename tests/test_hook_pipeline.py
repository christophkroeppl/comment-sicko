#!/usr/bin/env python3
"""Drive the write gate through Hermes' real pre_tool_call dispatcher.

tests/test_gates.py calls the hook function directly. That proves the hook's
logic but not that Hermes' hook machinery accepts its return value, so a wrong
dialect key would pass the suite while never modifying a write.

This test goes through ``invoke_hook`` (the function the agent loop actually
calls) for both hooks, with the plugin enabled in a temp profile.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERMES_ROOT = Path(os.environ.get("HERMES_REPO_ROOT", Path.home() / ".hermes/hermes-agent"))
REPO = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(HERMES_ROOT))
sys.path.insert(0, str(REPO))

NARRATION = (
    "import time\n"
    "\n"
    "# Loop over the retries\n"
    "# Loop over the retries\n"
    "def f():\n"
    "    # Return the value\n"
    "    return 1\n"
)
KEEP = (
    "#!/usr/bin/env python3\n"
    "# SPDX-License-Identifier: MIT\n"
    "# TODO(spec §7.3): drop this shim once upstream lands\n"
    "def g():\n"
    '    """Public contract: raises ValueError when x is empty."""\n'
    "    return '# not a comment'\n"
)


def _settings_yaml(mode: str) -> str:
    return (
        "plugins:\n"
        "  enabled:\n"
        "    - comment-sicko\n"
        "  disabled: []\n"
        "  entries:\n"
        "    comment-sicko:\n"
        "      settings:\n"
        f"        write_gate_mode: {mode}\n"
        f"        verify_gate_mode: {mode}\n"
        "        sicko_enabled: false\n"
    )


def _write_env(tmp: Path) -> None:
    home = tmp / "home"
    (home / "plugins").mkdir(parents=True)
    (home / "config.yaml").write_text(_settings_yaml("modify"), encoding="utf-8")
    # A plugin is enabled by listing it under plugins.enabled AND being linked
    # into $HERMES_HOME/plugins/. Both were needed: with only config.yaml the
    # discovery found no plugin dir, and with only the link it stayed disabled.
    (home / "plugins" / "comment-sicko").symlink_to(REPO, target_is_directory=True)
    os.environ["HERMES_HOME"] = str(home)


def _rediscover() -> None:
    """Re-run plugin discovery, then assert the hooks this suite depends on exist.

    ``discover_plugins()`` returns None on success, so the check is on the
    manager's ledger, not the return value. Without this assertion every check
    below would pass vacuously against a plugin that never loaded.
    """
    from hermes_cli import lifecycle, plugins as plugins_mod

    plugins_mod.discover_plugins(force=True)
    loaded = plugins_mod.get_plugin_manager()._plugins.get("comment-sicko")
    if loaded is None:
        raise SystemExit("comment-sicko was not discovered — the gate would be inert")
    if loaded.error:
        raise SystemExit(f"comment-sicko failed to load: {loaded.error}")
    for name in ("pre_tool_call", "pre_verify"):
        if not lifecycle.has_hook(name):
            raise SystemExit(f"{name} hook did not register")
    print(f"  discovered: hooks={loaded.hooks_registered} tools={loaded.tools_registered}")


def _dispatch(tool: str, args: dict):
    """Call the real dispatcher the tool executor uses.

    ``_pre_tool_block`` in agent/tool_executor.py is the only production caller:
    it returns ``(block_message, final_args)`` and applies ``final_args`` when it
    is not None. Driving that function proves the hook's return value is
    understood by Hermes, which calling the hook directly cannot.
    """
    return _dispatch_with_ids(tool, args, {"session_id": "e2e", "tool_call_id": "c1"})


def _dispatch_with_ids(tool: str, args: dict, ids: dict):
    from hermes_cli.plugins import _dispatch_pre_tool_call_hooks

    # The identity kwargs every tool hook call carries (agent/inline_tool_executors.py).
    full = {"task_id": "", "session_id": "", "tool_call_id": "",
            "turn_id": "", "api_request_id": ""}
    full.update(ids)
    return _dispatch_pre_tool_call_hooks(tool, args, middleware_trace=[], **full)


def _content_of(result, key: str, original: str) -> str:
    block_msg, modified = result
    if modified is None:
        return original
    return (modified or {}).get(key, original)


def check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  [{detail}]" if detail and not cond else ""))
    return cond


def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory() as td:
        _write_env(Path(td))
        _rediscover()
        print("temp profile:", os.environ["HERMES_HOME"])
        print("python:", sys.executable)

        print("\n[1] write_gate strips narration via the real dispatcher")
        result = _dispatch("write_file", {"path": "x.py", "content": NARRATION})
        block_msg, modified = result
        print(f"  block_message: {block_msg!r}")
        print(f"  modified is None: {modified is None}")
        ok &= check("not blocked", not block_msg, str(block_msg)[:120])
        ok &= check("args were modified", modified is not None)
        if modified:
            content = modified.get("content", "")
            print(f"  content now: {content!r}")
            ok &= check("narration removed", "# Loop over the retries" not in content, repr(content[:120]))
            ok &= check("code survived", "def f():" in content and "return 1" in content)
            ok &= check("path preserved", modified.get("path") == "x.py")

        print("\n[2] allowances survive the same path")
        block2, modified2 = _dispatch("write_file", {"path": "y.py", "content": KEEP})
        kept = _content_of((block2, modified2), "content", KEEP)
        print(f"  kept content: {kept!r}")
        for needle, label in (
            ("#!/usr/bin/env python3", "shebang"),
            ("SPDX-License-Identifier", "licence header"),
            ("spec §7.3", "spec link"),
            ("Public contract", "public docstring"),
            ("'# not a comment'", "hash inside string"),
        ):
            ok &= check(f"{label} kept", needle in kept)

        print("\n[3] patch path uses the same machinery")
        block3, modified3 = _dispatch("patch", {"path": "z.py", "new_string": NARRATION})
        new_str = _content_of((block3, modified3), "new_string", NARRATION)
        ok &= check(
            "patch narration removed",
            "# Loop over the retries" not in new_str,
            repr(new_str[:100]),
        )

        print("\n[4] off mode is a no-op (control: the hook must be inert)")
        cfg_yaml = Path(os.environ["HERMES_HOME"]) / "config.yaml"
        cfg_yaml.write_text(_settings_yaml("off"), encoding="utf-8")
        _rediscover()
        block4, modified4 = _dispatch("write_file", {"path": "x.py", "content": NARRATION})
        ok &= check("off mode does not block", not block4, str(block4)[:120])
        ok &= check(
            "off mode leaves args untouched",
            modified4 is None or modified4.get("content") == NARRATION,
            repr((modified4 or {}).get("content", "")[:80]),
        )
        # Proves step 1 was not vacuous: with the gate back on, the same
        # dispatcher DOES modify. If this fails, the "ok" lines above were
        # measuring a hook that never ran.
        cfg_yaml.write_text(_settings_yaml("modify"), encoding="utf-8")
        _rediscover()
        block5, modified5 = _dispatch("write_file", {"path": "x.py", "content": NARRATION})
        ok &= check(
            "gate is live again (step 1 was not vacuous)",
            bool(modified5) and "# Loop over the retries" not in modified5.get("content", ""),
            repr((modified5 or {}).get("content", "")[:80]),
        )

        print("\n[5b] layer 1 tells the model what it removed (transform_tool_result)")
        cfg_yaml.write_text(_settings_yaml("modify"), encoding="utf-8")
        _rediscover()
        ids = {"task_id": "", "session_id": "e2e-note", "tool_call_id": "c-note",
               "turn_id": "", "api_request_id": ""}
        block_n, mod_n = _dispatch_with_ids("write_file",
                                            {"path": "n.py", "content": NARRATION}, ids)
        from hermes_cli.plugins import invoke_hook as invoke_plugin_hook

        notes = invoke_plugin_hook("transform_tool_result", tool_name="write_file",
                                    result="wrote 1 file", **ids)
        print(f"  note: {notes}")
        ok &= check("a report rides back to the model", bool(notes), str(notes)[:120])
        joined = " ".join(str(n) for n in notes)
        ok &= check("note names what was removed",
                    "removed" in joined.lower() or "comment" in joined.lower(), joined[:140])

        print("\n[5] layer 2 through the real pre_verify entry point")
        cfg_yaml.write_text(_settings_yaml("modify"), encoding="utf-8")
        _rediscover()

        dirty = Path(td) / "dirty.py"
        dirty.write_text(
            "import time\n\n"
            "# Loop over the retries\n# Loop over the retries\n"
            "def f():\n    return 1\n",
            encoding="utf-8",
        )
        from hermes_cli.plugins import get_pre_verify_continue_message

        msg = get_pre_verify_continue_message(
            session_id="e2e", coding=True, attempt=0, changed_paths=[str(dirty)],
        )
        print(f"  continue message: {str(msg)[:160]}")
        ok &= check("layer 2 asks for cleanup", bool(msg))
        ok &= check("message names the file", bool(msg) and "dirty.py" in msg)

        clean = Path(td) / "clean.py"
        clean.write_text("import time\n\ndef f():\n    return 1\n", encoding="utf-8")
        msg2 = get_pre_verify_continue_message(
            session_id="e2e", coding=True, attempt=0, changed_paths=[str(clean)],
        )
        ok &= check("clean file passes layer 2", not msg2, str(msg2)[:120])

        cfg_yaml.write_text(_settings_yaml("off"), encoding="utf-8")
        _rediscover()
        msg3 = get_pre_verify_continue_message(
            session_id="e2e", coding=True, attempt=0, changed_paths=[str(dirty)],
        )
        ok &= check("layer 2 inert when off", not msg3, str(msg3)[:120])

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())