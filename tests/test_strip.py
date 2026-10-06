#!/usr/bin/env python3
"""Stripper tests: what must survive, what must go, and what must not shift.

Run: ``python3 tests/test_strip.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scanner.strip import strip_comments  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {label}")
    else:
        FAILURES.append(f"{label}: {detail}")
        print(f"  FAIL  {label}  {detail}")


def strip(text: str, path: str, **kw) -> tuple[str, list]:
    r = strip_comments(text, path, **kw)
    return r.text, r.removed


def lines_of(text: str) -> list[str]:
    return text.splitlines()


# --- must survive -----------------------------------------------------------

def test_hash_inside_string() -> None:
    src = (
        'URL = "http://example.com/x#fragment"\n'
        'PATTERN = re.compile(r"^\\s*#")\n'
        'TEMPLATE = """line # one\nline # two"""\n'
        "MARKER = '#' * 3\n"
    )
    out, removed = strip(src, "conf.py")
    check("hash inside python strings survives", out == src, f"removed={removed} out={out!r}")


def test_hash_inside_js_string() -> None:
    src = (
        'const url = "https://x.dev/a#frag";\n'
        "const color = '#' + 'fff';\n"
        'const q = `select ${col} # from t`;\n'
    )
    out, removed = strip(src, "app.ts")
    check("hash inside js strings survives", out == src, f"removed={removed}")


def test_shebang_survives() -> None:
    src = "#!/usr/bin/env python3\nprint(1)\n"
    out, removed = strip(src, "run.py")
    check("shebang survives", out == src, f"removed={removed} out={out!r}")


def test_licence_header_survives() -> None:
    src = (
        "# Copyright 2026 Example Ltd\n"
        "# SPDX-License-Identifier: MIT\n"
        "\n"
        "def f():\n"
        "    return 1\n"
    )
    out, removed = strip(src, "mod.py")
    check("legal header survives", "Copyright 2026" in out and "SPDX" in out,
          f"removed={removed} out={out!r}")


def test_formatter_directive_survives() -> None:
    src = (
        "matrix = [\n"
        "    1, 0,\n"
        "    # fmt: off\n"
        "    2,   0,\n"
        "    # fmt: on\n"
        "]\n"
    )
    out, _ = strip(src, "m.py")
    check("fmt: off/on survive", "fmt: off" in out and "fmt: on" in out, f"out={out!r}")

    js = "// prettier-ignore\nconst ugly = [1,2,3];\n"
    out_js, _ = strip(js, "u.ts")
    check("prettier-ignore survives", "prettier-ignore" in out_js, f"out={out_js!r}")


def test_tool_directive_survives() -> None:
    src = '/* #region helpers */\nfunction a() {}\n/* #endregion */\n'
    out, _ = strip(src, "h.ts")
    check("#region/#endregion survive", "#region" in out and "#endregion" in out, f"out={out!r}")


def test_public_docstring_survives() -> None:
    src = (
        'def total_cents(items):\n'
        '    """Sum item prices in integer cents.\n'
        "\n"
        "    Returns the total in the smallest currency unit so no rounding\n"
        "    error can accumulate across many additions.\n"
        '    """\n'
        "    return sum(i.cents for i in items)\n"
    )
    out, removed = strip(src, "m.py")
    check("substantive docstring survives", '"""Sum item prices in integer cents.' in out,
          f"removed={removed} out={out!r}")


def test_issue_link_survives() -> None:
    src = (
        "# See https://github.com/org/repo/issues/412 for the protocol requirement\n"
        "def f():\n"
        "    return 1\n"
    )
    out, _ = strip(src, "m.py")
    check("issue link survives", "issues/412" in out, f"out={out!r}")


# --- must go ----------------------------------------------------------------

def test_narration_goes() -> None:
    # The narration rule caps at 8 words on purpose: a short comment opening on
    # an action verb and echoing the next line is narration, while a longer
    # explanation may legitimately be claiming something. A 9-word version of
    # this sentence is deliberately KEEP, so the fixture below stays short.
    src = (
        "def f(items):\n"
        "    # Loop over items\n"
        "    return [i for i in items]\n"
    )
    out, removed = strip(src, "m.py")
    check("narration removed", "Loop over items" not in out, f"out={out!r}")
    check("narration was reported", len(removed) == 1, f"removed={removed}")
    check("code intact after narration", "[i for i in items]" in out)


def test_long_explanation_is_kept() -> None:
    # Counterpart to the above: past the word cap, prose is a claim, not narration.
    src = (
        "def f(items):\n"
        "    # Loop over items and filter the active ones before the caller sees them\n"
        "    return [i for i in items if i.active]\n"
    )
    out, removed = strip(src, "m.py")
    check("long explanation kept", "filter the active ones" in out, f"removed={removed}")


def test_commented_out_code_goes() -> None:
    src = (
        "def f():\n"
        "    # if legacy_mode: return 2\n"
        "    return 1\n"
    )
    # A whole-block dead-code share is a REVIEW verdict upstream, so reaching it
    # is a matter of fail_level, not of the allowances switch.
    out_gone, removed_gone = strip(src, "m.py", fail_level="REVIEW")
    check("commented-out code removed at REVIEW",
          "legacy_mode" not in out_gone and removed_gone, f"removed={removed_gone}")
    out_held, _ = strip(src, "m.py", fail_level="DELETE")
    check("commented-out code held at DELETE",
          "legacy_mode" in out_held, f"out={out_held!r}")


def test_named_divider_goes() -> None:
    src = (
        "def f():\n"
        "    # ---- Helper section ----\n"
        "    return 1\n"
    )
    out, removed = strip(src, "m.py", fail_level="REVIEW")
    check("named divider removed at REVIEW",
          "Helper section" not in out and removed, f"removed={removed}")
    out_kept, _ = strip(src, "m.py", fail_level="DELETE")
    check("named divider held at DELETE",
          "Helper section" in out_kept, f"out={out_kept!r}")


def test_empty_divider_goes() -> None:
    src = "def f():\n    # ----\n    return 1\n"
    out, removed = strip(src, "m.py")
    check("empty divider removed with protections on",
          "----" not in out and removed, f"out={out!r} removed={removed}")


def test_todo_goes() -> None:
    src = (
        "def f():\n"
        "    # TODO: handle the empty case\n"
        "    return 1\n"
    )
    out, _ = strip(src, "m.py")
    check("TODO removed", "TODO" not in out, f"out={out!r}")


def test_suppression_survives() -> None:
    # A suppression comment is consumed by a tool. Deleting `# type: ignore`
    # re-enables the mypy error it silences, and deleting `# noqa` re-enables
    # the ruff rule: both are behaviour changes, so both are allowances.
    src = "x = 1  # type: ignore\ny = 2  # noqa: E501\n"
    out, _ = strip(src, "m.py")
    check("type: ignore survives", "type: ignore" in out, f"out={out!r}")
    check("noqa survives", "noqa" in out, f"out={out!r}")


# --- line-number stability --------------------------------------------------

def test_line_numbers_preserved() -> None:
    # "Loop over" is a narrative-verb opener, so these are DELETE. ("Narrate"
    # is not in the verb table and would correctly classify as KEEP.)
    src = (
        "import os\n"
        "# Loop over the inputs\n"
        "A = 1\n"
        "# Loop over the outputs\n"
        "B = 2\n"
        "# Loop over the rest\n"
        "C = 3\n"
    )
    out, removed = strip(src, "m.py")
    check("line count unchanged", len(lines_of(out)) == len(lines_of(src)),
          f"{len(lines_of(src))} -> {len(lines_of(out))}")
    check("non-comment lines kept at same index",
          lines_of(out)[0] == "import os" and lines_of(out)[2] == "A = 1"
          and lines_of(out)[4] == "B = 2" and lines_of(out)[6] == "C = 3",
          f"out={lines_of(out)}")
    check("blanked lines are whitespace only",
          all(not ln.strip() for ln in lines_of(out)[1::2]), f"out={lines_of(out)}")


def test_trailing_newline_preserved() -> None:
    src = "# narrate\nA = 1\n"
    out, _ = strip(src, "m.py")
    check("trailing newline preserved", out.endswith("\n"), f"out={out!r}")


def test_no_trailing_newline_not_added() -> None:
    src = "# narrate\nA = 1"
    out, _ = strip(src, "m.py")
    check("no trailing newline invented", not out.endswith("\n"), f"out={out!r}")


# --- refusals ---------------------------------------------------------------

def test_unknown_extension_declines() -> None:
    src = "# heading\nsome text\n"
    r = strip_comments(src, "notes.unknown")
    check("unknown extension declines", r.skipped_reason != "" and not r.changed,
          f"reason={r.skipped_reason!r}")
    check("unknown extension leaves text alone", r.text == src)


def test_no_path_declines() -> None:
    src = "# heading\ntext\n"
    r = strip_comments(src, "")
    check("no path declines", not r.changed and r.skipped_reason != "")


def test_markdown_not_mangled() -> None:
    src = "# Real heading\n\nSome prose with a #hashtag.\n"
    r = strip_comments(src, "README.md")
    check("markdown left alone", r.text == src, f"out={r.text!r}")


def test_review_verdict_respects_protections() -> None:
    # "IMPORTANT:" is a KEEP_SIGNALS hit, so it stays KEEP at every setting --
    # a scent marker upstream is an allowance, not a judgement call. Verify
    # that, then use a genuine REVIEW verdict (dead-code block) for the
    # fail_level knob.
    keep_src = (
        "def f():\n"
        "    # IMPORTANT: do not reorder this, it depends on init order\n"
        "    return init_order\n"
    )
    for protect in (True, False):
        for level in ("DELETE", "REVIEW"):
            r = strip_comments(keep_src, "m.py", protect_allowances=protect,
                               fail_level=level)
            check(f"KEEP held with protections={protect} fail_level={level}",
                  not r.removed and r.protected, f"removed={r.removed}")

    review_src = (
        "def f():\n"
        "    # if legacy_mode: return 2\n"
        "    return 1\n"
    )
    r_at_delete = strip_comments(review_src, "m.py", fail_level="DELETE")
    r_at_review = strip_comments(review_src, "m.py", fail_level="REVIEW")
    check("REVIEW held at fail_level=DELETE", not r_at_delete.removed,
          f"removed={r_at_delete.removed}")
    check("REVIEW removed at fail_level=REVIEW",
          "legacy_mode" not in r_at_review.text and r_at_review.removed,
          f"removed={r_at_review.removed}")
    # protect_allowances no longer gates REVIEW: allowances are KEEP, and KEEP
    # is unconditional. Reaching a judgement call is fail_level's job alone.
    r_prot_off = strip_comments(review_src, "m.py", protect_allowances=False,
                                fail_level="REVIEW")
    check("protect_allowances=False does not change REVIEW reach",
          r_prot_off.removed == r_at_review.removed,
          f"removed={r_prot_off.removed}")


def test_clean_file_untouched() -> None:
    src = "def add(a, b):\n    return a + b\n"
    r = strip_comments(src, "m.py")
    check("clean file untouched", r.text == src and not r.changed)


def main() -> int:
    print("stripper")
    for fn in (
        test_hash_inside_string, test_hash_inside_js_string, test_shebang_survives,
        test_licence_header_survives, test_formatter_directive_survives,
        test_tool_directive_survives, test_public_docstring_survives,
        test_issue_link_survives, test_narration_goes, test_commented_out_code_goes,
        test_named_divider_goes, test_todo_goes, test_suppression_survives,
        test_line_numbers_preserved, test_trailing_newline_preserved,
        test_no_trailing_newline_not_added, test_unknown_extension_declines,
        test_no_path_declines, test_markdown_not_mangled,
        test_review_verdict_respects_protections, test_clean_file_untouched,
    ):
        fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all stripper tests pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())