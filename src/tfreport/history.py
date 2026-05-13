"""Run-to-run history tracking and trend rendering.

Each tfreport invocation can append a compact snapshot to an JSONL history
file. Snapshots are intentionally small (no per-resource detail) so the file
stays grep-friendly and PR-friendly.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def snapshot(summary_dict: Mapping[str, Any], *, cost_diff: float | None = None) -> dict[str, Any]:
    """Build a JSON-serializable history entry from a PlanSummary.to_dict()."""
    stats = summary_dict.get("stats") or {}
    risks = summary_dict.get("risks_by_severity") or {}
    compliance = summary_dict.get("compliance") or {}
    return {
        "timestamp": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "stats": {
            "total_changing": int(stats.get("total_changing", 0) or 0),
            "create": int(stats.get("create", 0) or 0),
            "update": int(stats.get("update", 0) or 0),
            "delete": int(stats.get("delete", 0) or 0),
            "replace": int(stats.get("replace", 0) or 0),
        },
        "risks": {
            "high": int(risks.get("high", 0) or 0),
            "medium": int(risks.get("medium", 0) or 0),
            "low": int(risks.get("low", 0) or 0),
        },
        "compliance_score": compliance.get("score"),
        "cost_diff_monthly": cost_diff,
        "ci_run": os.environ.get("GITHUB_RUN_ID") or os.environ.get("BUILD_BUILDID"),
    }


def append(path: str | Path, entry: Mapping[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load(path: str | Path, *, max_entries: int = 20) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        return []
    entries: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries[-max_entries:]


def render_section(entries: list[dict[str, Any]]) -> list[str]:
    from . import viz as viz_mod

    if len(entries) < 2:
        return []
    lines: list[str] = [
        f"## Trend (last {len(entries)} runs)",
        "",
        "| Metric | Trend | Latest |",
        "| --- | --- | ---: |",
    ]
    series_changes = [int(e.get("stats", {}).get("total_changing", 0) or 0) for e in entries]
    series_high = [int(e.get("risks", {}).get("high", 0) or 0) for e in entries]
    series_med = [int(e.get("risks", {}).get("medium", 0) or 0) for e in entries]
    series_cost = [
        float(e.get("cost_diff_monthly") or 0.0)
        for e in entries
    ]
    series_score = [
        float(e["compliance_score"]) if e.get("compliance_score") is not None else 0.0
        for e in entries
    ]
    lines.append(
        f"| Changes | `{viz_mod.sparkbar(series_changes)}` | {series_changes[-1]} |"
    )
    lines.append(
        f"| High risks | `{viz_mod.sparkbar(series_high)}` | {series_high[-1]} |"
    )
    lines.append(
        f"| Medium risks | `{viz_mod.sparkbar(series_med)}` | {series_med[-1]} |"
    )
    if any(series_cost):
        lines.append(
            f"| Cost Δ/mo | `{viz_mod.sparkbar([abs(v) for v in series_cost])}` | {series_cost[-1]:+.2f} |"
        )
    if any(s for s in series_score):
        latest = series_score[-1]
        lines.append(
            f"| Compliance | `{viz_mod.sparkbar(series_score)}` | {int(latest * 100)}% |"
        )
    lines.append("")
    return lines
