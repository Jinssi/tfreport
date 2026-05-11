"""Run-to-run delta against a baseline plan_summary.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Delta:
    new_addresses: list[str] = field(default_factory=list)
    removed_addresses: list[str] = field(default_factory=list)
    new_high_risk: list[str] = field(default_factory=list)
    new_medium_risk: list[str] = field(default_factory=list)
    stat_diff: dict[str, int] = field(default_factory=dict)
    baseline_path: str = ""

    def is_empty(self) -> bool:
        return not (
            self.new_addresses
            or self.removed_addresses
            or self.new_high_risk
            or self.new_medium_risk
            or any(self.stat_diff.values())
        )


def _index(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["address"]: c for c in (summary.get("changes") or [])}


def _highest(severities: list[dict[str, Any]] | None) -> str | None:
    if not severities:
        return None
    order = {"high": 3, "medium": 2, "low": 1}
    return max(severities, key=lambda r: order.get(r.get("severity", ""), 0)).get("severity")


def compute(current: dict[str, Any], baseline: dict[str, Any], baseline_path: str = "") -> Delta:
    cur_idx = _index(current)
    base_idx = _index(baseline)

    new_addrs = sorted(set(cur_idx) - set(base_idx))
    gone_addrs = sorted(set(base_idx) - set(cur_idx))

    new_high: list[str] = []
    new_med: list[str] = []
    for addr, c in cur_idx.items():
        cur_top = _highest(c.get("risks"))
        if cur_top not in ("high", "medium"):
            continue
        prev_top = _highest((base_idx.get(addr) or {}).get("risks"))
        if cur_top == "high" and prev_top != "high":
            new_high.append(addr)
        elif cur_top == "medium" and prev_top not in ("high", "medium"):
            new_med.append(addr)

    cur_stats = current.get("stats") or {}
    base_stats = baseline.get("stats") or {}
    keys = set(cur_stats) | set(base_stats)
    stat_diff = {
        k: int(cur_stats.get(k, 0) or 0) - int(base_stats.get(k, 0) or 0)
        for k in keys
        if int(cur_stats.get(k, 0) or 0) - int(base_stats.get(k, 0) or 0) != 0
    }

    return Delta(
        new_addresses=new_addrs,
        removed_addresses=gone_addrs,
        new_high_risk=sorted(new_high),
        new_medium_risk=sorted(new_med),
        stat_diff=stat_diff,
        baseline_path=baseline_path,
    )


def load_baseline(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def render_section(delta: Delta) -> str:
    if delta.is_empty():
        return "_No change versus baseline._"
    lines: list[str] = []
    if delta.stat_diff:
        bits = []
        for k in ("create", "update", "delete", "replace"):
            v = delta.stat_diff.get(k, 0)
            if v:
                sign = "+" if v > 0 else ""
                bits.append(f"{k} {sign}{v}")
        if bits:
            lines.append("- **Stat delta**: " + ", ".join(bits))
    if delta.new_high_risk:
        lines.append(
            "- **New HIGH-severity risks**: "
            + ", ".join(f"`{a}`" for a in delta.new_high_risk)
        )
    if delta.new_medium_risk:
        lines.append(
            "- New medium risks: " + ", ".join(f"`{a}`" for a in delta.new_medium_risk)
        )
    if delta.new_addresses:
        sample = delta.new_addresses[:10]
        more = (
            f" (+{len(delta.new_addresses) - 10} more)"
            if len(delta.new_addresses) > 10
            else ""
        )
        lines.append("- New resource changes: " + ", ".join(f"`{a}`" for a in sample) + more)
    if delta.removed_addresses:
        sample = delta.removed_addresses[:10]
        more = (
            f" (+{len(delta.removed_addresses) - 10} more)"
            if len(delta.removed_addresses) > 10
            else ""
        )
        lines.append(
            "- No longer changing: " + ", ".join(f"`{a}`" for a in sample) + more
        )
    return "\n".join(lines)
