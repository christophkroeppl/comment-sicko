# e2e status and what the harness can and cannot prove

Last run: `/home/rumlyne/.hermes/cache/scratch/comment-sicko-e2e.2692094`
Both arms exited 0 (no truncation). Verdict printed: INCONCLUSIVE.

## What the run showed

The gate fired in the gated arm, exactly once:

    removed 1 comment line(s) before writing [15: delete]

and the model reacted to it in its own reasoning ("The import got stripped?").
That is the layer-1 loop working on a live turn: the note reached the model.

The control wrote 85 comment lines, the gated arm 83. Neither had a deletable
finding at the default `audit_fail_level: DELETE`; the control's two findings
were both `REVIEW dead-code-block`, which the gate holds back on purpose.

## Why the harness is INCONCLUSIVE, and why that is not fixable by retrying

The two arms are **not a controlled comparison**. Each arm is an independent
model turn, so each produced a *different program*:

    control  retry.py md5 5ad79d0d3c09   6 write_file, 28 patch mentions
    gated    retry.py md5 e6b0e750ec9d   9 write_file, 17 patch mentions

Comment-line counts from two unrelated files cannot isolate the gate's effect.
A non-discriminating control is the honest result, and no amount of rerunning
changes that: the prompt provokes comments, but mostly at REVIEW level, and the
file each arm writes is not held constant.

Two things would make this a real experiment, neither of which belongs in a CI
e2e:

1. **Hold the output fixed.** Give both arms byte-identical content to write and
   diff that, rather than comparing two free-running generations. That measures
   the gate, not the model's mood.
2. **Widen the floor** to `audit_fail_level: REVIEW` so the control has
   something the gate is supposed to remove. That changes what is being
   asserted, so it belongs in a separate opt-in arm, not the default.

## What is actually proven elsewhere

The controlled evidence is `tests/test_hook_pipeline.py`, which drives
`_dispatch_pre_tool_call_hooks` and `get_pre_verify_continue_message` — the real
production entry points — against a temp profile. 19 checks, PASS:

- narration stripped on the `write_file` and `patch` paths
- all five allowances preserved (shebang, SPDX, spec link, public docstring,
  `#` inside a string literal)
- `off` inert for both layers
- layer 2 names the file and line of what survived
- layer 1's `transform_tool_result` note rides back to the model
- a re-enable check proving the first case was not vacuous

Do not treat this file's INCONCLUSIVE as evidence the gate is broken; it is
evidence the *experiment* is underpowered. The gate demonstrably fires.