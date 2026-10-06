# comment-sicko — Hermes plugin

Plan, v2. Public repo: `christophkroeppl/comment-sicko`.
Local checkout: `/media/data/projects/ACTIVE/comment-sicko`, symlinked to `~/.hermes/plugins/comment-sicko`.

MIT. Upstream persona text from `cursor/plugins` `pstack` (Lauren Tan, MIT), credited in
`NOTICE` with the pinned commit.

## Why this exists

`pstack/agents/comment-sicko.md` upstream is a persona prompt for a read-only reviewer
subagent, invoked post-hoc by `pstack/skills/no-comments`. It reviews a diff after the
comments already landed. The problem here is a model writing comments in the first place,
which is a write-time problem.

The Hermes pstack port in the catalog (`Cloeille/pstack`) does not include it — its `SKILLS`
tuple is `poteto-mode, unslop, how, interrogate, architect, swarm, setup-pstack`. No
`no-comments`, no `agents/`. Nothing to install; this is built from scratch.

Three enforcement layers, ordered by how early they act. All three implemented. 1 and 2 on by
default, 3 off until toggled.

## Layer 1 — write gate (`pre_tool_call`, default on)

`ctx.register_hook("pre_tool_call", ...)`. The directive contract at
`hermes_cli/plugins.py:2038` accepts `{"action": "modify", "args": {...}}`, shallow-merged into
the tool args before the block/approve gate and before execution.

Scope: `write_file` (`content`) and `patch` (`new_string`). Those two cover the path a model
actually uses to add a comment. Returns modified args; `transform_tool_result` appends what was
removed so the model learns rather than silently losing its prose.

Modes per layer: `off | modify | block | audit`.
- `modify` (default) — strip violating comment lines, report the removals.
- `block` — refuse the call with the offending lines named. For repos that want a hard stop.
- `audit` — touch nothing, record the finding.

`modify` beats `block` as the default: blocking mid-flow makes a model retry blindly, stripping
plus a result note teaches it once. `block` stays available.

`terminal` (heredocs, `sed -i`) and `execute_code` are string-manipulation problems rather than
comment problems and are out of scope for v1. They are covered indirectly by layer 2. Stated in
the README as a known gap rather than papered over.

## Layer 2 — verify gate (`pre_verify`, default on)

`ctx.register_hook("pre_verify", ...)`. Fires once per turn after code edits with
`changed_paths` (`agent/turn_stop_gates.py:52`). Scans only added lines via the same
`git_diff_lines()` logic as the audit script, returns
`{"action": "continue", "message": ...}` naming `file:line` for each comment still standing.

Bounded by `agent.max_verify_nudges`, so there is no stop-loop risk. Catches what layer 1 missed
and what the model wrote outside `write_file`/`patch`.

## Layer 3 — Comment Sicko proper (default OFF)

A plugin tool `comment_sicko`, registered always but refusing to spawn while disabled — that way
`/comment-sicko enable sicko` takes effect with no reload.

Spawns the upstream persona verbatim as a subagent via `ctx.subagent_lifecycle`
(`SubagentLaunchRequest`, `role="leaf"`), with the deterministic scan attached as ground truth so
the report is not pure vibes. Report-only. `MUST KILL` flags name the exact guilty symbol for
rename/extract/type/rearchitecture. Never edits application code.

Persona register is load-bearing: the theatrical voice is what lets the reviewer call
`IMPORTANT: do not touch` a confession rather than a constraint. Kept close to upstream.

## Scanner: reuse, single source of truth

`~/.hermes/skills/software-development/no-comments/scripts/comment_audit.py` (732 lines) already
solves the hard parts: `python_comments()` (tokenize-based), `generic_comments()` with
`mask_strings()`, `looks_like_commented_code()`, `classify()` with DELETE/REVIEW verdicts,
`git_diff_lines()`, JSON output and `--fail-level` exit codes. Tokenizer discipline is the whole
game — you cannot regex-strip comments without destroying `#` inside a string literal.

Decision (was an open fork, now settled): the scanner moves into the plugin package as the one
implementation. The skill's `comment_audit.py` becomes a thin shim that imports from the plugin
when importable and otherwise falls back to the vendored copy, so the skill keeps working
standalone and CI keeps passing. One behavior, no drift.

The plugin does not depend on the skill living at a fixed path — a published plugin cannot assume
anything about the user's `~/.hermes/skills/`.

## Config surface — `/comment-sicko`

Two entry points over one arg parser and one state resolver:

- `ctx.register_command("comment-sicko", ...)` → `/comment-sicko <command> <args>` in session.
  Verified to dispatch on CLI (`cli.py:1245`), gateway (`gateway/run_inbound.py:1117`) and TUI
  (`tui_gateway/methods_tools.py`); handler receives one raw-arg string.
- `ctx.register_cli_command("comment-sicko", ...)` → `hermes comment-sicko <command> <args>` for
  headless, scripting and CI.

```
/comment-sicko status                          all layers, modes, effective profile
/comment-sicko enable  <layer|all>              layer ∈ write-gate | verify-gate | sicko
/comment-sicko disable <layer|all>
/comment-sicko mode    <layer> <off|modify|block|audit>
/comment-sicko set     <key> <value>            any config_schema key
/comment-sicko defaults show                   effective settings + where each value came from
/comment-sicko defaults set   <key> <value> [--profile <name>]
/comment-sicko defaults reset [--profile <name>]
/comment-sicko scan    [paths...]               deterministic audit now, no LLM
/comment-sicko review  [paths...]               run the Comment Sicko subagent (needs layer 3 on)
/comment-sicko doctor                           hook registration + config health
```

`status` and `defaults show` print the resolution chain per setting, so "why is this off" is
answerable without reading config.yaml.

Every layer is toggleable through this surface — that was the explicit ask.

## Per-profile defaults

Already free, and worth being explicit about why: plugin settings live at
`plugins.entries.comment-sicko.settings.*` in `$HERMES_HOME/config.yaml`, and `$HERMES_HOME` is
per-profile (`hermes_cli/config.py:438`, `hermes_cli/profiles.py:363`). A profile switch is a
different config file, so per-profile defaults need no special machinery — each profile just has
its own values.

`--profile <name>` writes *another* profile's defaults from the current session, via
`set_hermes_home_override` around the standard `save_plugin_setting` writer so all managed-install
and managed-key refusals still apply. The flag is the explicit direction to touch that profile;
without it the command only ever touches the active one. Every cross-profile write echoes the
target profile's resolved home in its output.

Defaults in `plugin.yaml` `config_schema` (manifest v2, all optional):

| key | type | default | meaning |
|---|---|---|---|
| `write_gate_mode` | enum | `modify` | layer 1 |
| `verify_gate_mode` | enum | `modify` | layer 2 |
| `sicko_enabled` | bool | `false` | layer 3 |
| `protect_allowances` | bool | `true` | never strip the six numbered allowances |
| `audit_fail_level` | enum | `DELETE` | floor for `scan` |
| `report_to` | enum | `tool_result` | `tool_result` \| `off` |

`protect_allowances` defaults true because the failure mode of getting the allowance list wrong
is destroying a licence header or a protocol constraint — unrecoverable from a diff — while the
failure mode of leaving a comment in is cosmetic.

`config_schema` drives the Desktop settings form too, so these show up as a normal settings panel
with no extra work.

## Layout

```
comment-sicko/
  plugin.yaml            manifest v2, provides_hooks + provides_tools + config_schema
  __init__.py            register(ctx)
  gate.py                layers 1 and 2
  sicko.py               layer 3: persona, tool, subagent spawn
  config.py              settings resolution: manifest default → profile config → session override
  cli.py                 arg parser shared by /comment-sicko and `hermes comment-sicko`
  scanner/               the moved audit implementation
    __init__.py  classify.py  detect.py  diffscope.py  render.py
  tests/
  fixtures/
    confused_prompt.md   the layer-1/2 test prompt (see below)
  README.md  NOTICE  LICENSE  pyproject.toml
```

`hermes plugins doctor --ci` is the registration gate — it runs real `register(ctx)`, the hook
registry and the tool registry, and flags callbacks that do not accept `**kwargs`.

## Tests

**Unit — stripper.** `#` inside a Python and a JS string literal survives. Shebang survives.
Licence header survives. `prettier-ignore`, `# region`, `fmt: off` survive. Docstring on a public
symbol survives. Narration, banners, section dividers and commented-out code go.

**Unit — config resolution.** manifest default → profile value → session override, in that
order; a malformed value falls back rather than raising into a hook.

**Doctor.** `hermes plugins doctor --ci`.

**End-to-end, layer 1 + 2, the real test.** Scratch `HERMES_HOME`, plugin enabled, then a single
long deliberately-confusing prompt. Pass condition is on the filesystem, not in a log: the
written files contain zero added comment lines.

`fixtures/confused_prompt.md` is written to be maximally hostile to comment discipline. It has the
model change its mind three times mid-task and cite three approaches it already tried that did not
work — the shape that reliably makes a model narrate its own reasoning into the file, because each
reversal is something it wants to explain to the next reader. Three reversals, three dead ends,
then a fourth approach it settles on. The confusion is the trigger; a straightforward task would
pass without proving anything.

**End-to-end, layer 3.** With the layer off, `comment_sicko` refuses and says so. With it on, the
subagent returns a report with `MUST KILL` flags and the diff is unchanged — that check is
mechanical, so it is asserted rather than eyeballed.

## Build order

1. Repo, manifest, skeleton, doctor green.
2. Scanner moved; skill shimmed; skill tests still pass.
3. Layer 1.
4. Layer 2.
5. `/comment-sicko` surface, both entry points, per-profile.
6. Layer 3.
7. Fixture prompt, e2e runs, README.

## Decisions taken

- Standalone plugin, not a pstack fork. No comment-sicko upstream in the Hermes port, and the
  whole rigor stack is the wrong dependency for one agent. If pstack is installed later its
  `no-comments` skill is shadowed by this plugin, which is the outcome wanted anyway.
- v1 covers `write_file` + `patch` for layer 1. A gate that provably covers the common path beats
  one that claims `terminal` too.
- Persona text stays close to upstream.
- Public repo, per your instruction. Author commits as you via global git config, never an agent
  identity. Not pushed until you say so.
