---
name: comment-sicko
description: Use when comments are being written into code and should not be, or to run the Comment Sicko reviewer over a diff. The plugin enforces this automatically.
version: 0.1.0
author: Christoph Kroeppl (christophkroeppl)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [comments, code-style, lint, review, guardrails]
    related_skills: [no-comments, simplify-code, code-review]
---

# comment-sicko

Models narrate code they just wrote: `# Loop over items`, `# Loop over items`,
`# Loop over items` above three lines that say the same thing. This plugin stops
that at three points, earliest first.

## What is already running

Nothing here is needed for normal work. With the plugin enabled (the default),
layers 1 and 2 are active:

- **Write gate** — before every `write_file` and `patch`, deletable comment lines
  are stripped and you are told which ones went. `#` inside a string literal, a
  shebang, a licence header, a `prettier-ignore`, a `#region`, a substantive
  docstring and an issue link all survive.
- **Verify gate** — before a turn that edited code ends, the edited files are
  rescanned. Anything still standing is named `file:line` and the turn is asked to
  clean it up.

So if you are reading this because you were told a comment was removed: do not
re-add it. The code was expected to explain itself.

## Running the reviewer

Layer 3 is the Comment Sicko persona, off by default because it costs a model
call.

```bash
/comment-sicko enable sicko
/comment-sicko review            # working diff
/comment-sicko review src/ tests/
```

It reports only. It never edits. Its output is touched files, a deletion count,
`MUST KILL` flags naming the exact symbol that should be renamed, extracted,
typed or restructured instead, and the skips with the allowance each claimed.

## Controlling the layers

```bash
/comment-sicko status                    # all layers, modes, effective profile
/comment-sicko mode write-gate block     # refuse writes with comments instead
/comment-sicko disable verify-gate
/comment-sicko enable sicko
```

Modes: `off` (never acts), `modify` (strips and reports), `block` (refuses the
write and names the lines), `audit` (observes, changes nothing).

`modify` is the default because blocking mid-flow makes a model retry blindly,
whereas stripping plus a note teaches it once.

## Per profile

Settings live in `$HERMES_HOME/config.yaml`, and `$HERMES_HOME` is the profile,
so each profile keeps its own defaults. To write another profile's settings from
here:

```bash
/comment-sicko defaults show
/comment-sicko defaults set write_gate_mode block --profile research
```

The command echoes the resolved home it wrote, so a cross-profile write is never
silent.

## When a comment is right

The six allowances, which are never removed:

1. A legal or licence header.
2. Behaviour forced by something we cannot change — a vendor library, a wire
   protocol, a platform quirk, a binding ADR.
3. A public API contract: docstrings on exported symbols, CLI usage, parameter
   contracts.
4. An issue, ADR, ticket or `spec §7.3` link explaining a constraint code cannot
   express.
5. A directive a tool consumes: `statement-breakpoint`, `# region`,
   `-- sourceMappingURL`.
6. A formatter directive (`prettier-ignore`, `fmt: off`) silencing a rule that is
   faulty, pedantic or style-only.

When a comment is load-bearing and none of the six fits, the durable form is the
cheapest in-scope test, type or CI lint that fails when the rule breaks. Offer
it; do not add the comment.

## Auditing without the plugin

```bash
/comment-sicko scan src/
```

The same scanner backs the `no-comments` skill's `comment_audit.py` and this
plugin, so `scan` and the skill's script agree by construction. For CI:

```bash
python3 tests/test_parity.py     # proves the shared implementation still matches
```

## Known gap

The write gate covers `write_file` and `patch`. Code written through `terminal`
(heredocs, `sed -i`) or `execute_code` reaches the file unstripped, and the
verify gate is what catches it at end of turn. Extending the write gate to
heredocs is a string-interpretation problem rather than a comment one, so it is
not claimed here.