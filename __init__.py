"""comment-sicko — stop models from filling code with comments.

Three enforcement layers, ordered by how early they act:

1. **Write gate** (``pre_tool_call``, default on). Hermes applies the ``modify``
   directive before the block/approve gate and before execution, so the comment
   lines are gone before the file is written, and the model is told which ones
   went so it stops writing them.
2. **Verify gate** (``pre_verify``, default on). Once per turn after code edits,
   over the edited files. Catches what layer 1 could not reach.
3. **Comment Sicko** (``comment_sicko`` tool, default off). The upstream pstack
   persona as a read-only reviewer subagent, anchored to the deterministic scan.

The scanner in :mod:`scanner` is the single implementation of comment detection
and classification; the ``no-comments`` skill imports from it rather than
keeping a copy.
"""

from __future__ import annotations

PLUGIN_ID = "comment-sicko"
SKILLS = ("comment-sicko",)


def register(ctx):
    """Register hooks, the layer 3 tool, the slash command and the CLI subcommand."""
    from . import cli, gate, sicko

    gate.install_hooks(ctx)

    ctx.register_tool(
        name="comment_sicko",
        toolset="comment-sicko",
        schema=sicko.tool_schema(),
        handler=sicko.make_handler(ctx),
        description="Run the Comment Sicko reviewer over the working diff.",
        emoji="🔪",
    )

    ctx.register_command(
        "comment-sicko",
        cli.make_slash_handler(ctx),
        description="comment-sicko: status, enable/disable/mode per layer, scan, review",
        args_hint="<command> [args]",
        argument_mode="mixed",
    )

    ctx.register_cli_command(
        "comment-sicko",
        help="comment-sicko: status, enable/disable/mode per layer, scan, review",
        description="comment-sicko control surface",
        setup_fn=cli.cli_setup,
        handler_fn=cli.make_cli_handler(ctx),
    )

    for name in SKILLS:
        try:
            ctx.register_skill(name, f"skills/{name}/SKILL.md")
        except Exception:
            # A missing optional skill must not stop the gates from loading.
            pass