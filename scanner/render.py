"""Human-readable and JSON rendering.

Moved from the no-comments skill's scripts/comment_audit.py (MIT, same
author), which is now a thin shim over this package.
"""
from __future__ import annotations
import re

from .detect import LEVELS


def render(reports: list[FileReport], max_density: float) -> None:
    total = {lvl: 0 for lvl in LEVELS}
    flagged: list[FileReport] = []
    for rep in reports:
        dense = rep.density > max_density and rep.density_meaningful
        if not rep.findings and not dense:
            continue
        flagged.append(rep)
        for f in rep.findings:
            total[f.level] += 1

    if not flagged:
        print("clean: no deletable comments found")
        return

    for rep in sorted(flagged, key=lambda r: (-len(r.findings), r.path)):
        for f in sorted(rep.findings, key=lambda f: f.line):
            loc = f"{f.path}:{f.line}"
            body = re.sub(r"\s+", " ", f.text)[:110]
            print(f"{loc}  {f.level:<6}  {f.kind:<8}  {body}")
        if dense:
            print(f"{rep.path}  REVIEW  density  {rep.density:.0%} comment lines")

    print()
    print(f"DELETE {total['DELETE']}   REVIEW {total['REVIEW']}   "
          f"files {len(flagged)}/{len(reports)}")
