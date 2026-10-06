"""Comment scanner for the comment-sicko plugin.

Four layers, in dependency order:

- ``detect``    lexical comment detection per language (tokenizer for Python,
                masked-string regexes elsewhere)
- ``classify``  grouping and the DELETE / REVIEW / KEEP decision
- ``scan``      file-level scanning and git diff scoping
- ``render``    human-readable and JSON output
- ``strip``     the write-time remover built on the same detection, used by
                layer 1 to rewrite tool arguments

The detection and classification rules are one implementation shared with the
``no-comments`` skill, which imports from here.
"""

from .classify import classify, code_share, group_comments, looks_like_commented_code
from .detect import (
    BASENAME_COMMENT,
    BLOCK_COMMENT,
    LINE_COMMENT,
    LEVELS,
    Finding,
    FileReport,
    block_pair,
    generic_comments,
    line_token,
    mask_strings,
    python_comments,
)
from .render import render
from .scan import git_diff_lines, iter_files, os_walk, scan_file

__all__ = [
    "BASENAME_COMMENT",
    "BLOCK_COMMENT",
    "Finding",
    "FileReport",
    "LEVELS",
    "LINE_COMMENT",
    "block_pair",
    "classify",
    "code_share",
    "generic_comments",
    "git_diff_lines",
    "group_comments",
    "iter_files",
    "line_token",
    "looks_like_commented_code",
    "mask_strings",
    "os_walk",
    "python_comments",
    "render",
    "scan_file",
]