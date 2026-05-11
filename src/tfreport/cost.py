"""Read an Infracost breakdown JSON and emit a compact cost-impact summary.

We do not depend on Infracost; we just consume its standard JSON shape
(`infracost breakdown --format json`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CostImpact:
    currency: str
    past_monthly: float
    monthly: float
    diff_monthly: float
    top_resources: list[dict[str, Any]]

    @property
    def has_impact(self) -> bool:
        return abs(self.diff_monthly) > 0.005


def load(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse(data: dict[str, Any]) -> CostImpact:
    currency = data.get("currency", "USD")
    past = _f(data.get("pastTotalMonthlyCost"))
    cur = _f(data.get("totalMonthlyCost"))
    diff = _f(data.get("diffTotalMonthlyCost", cur - past))

    top: list[dict[str, Any]] = []
    for project in data.get("projects", []) or []:
        diff_breakdown = project.get("diff") or {}
        for r in diff_breakdown.get("resources", []) or []:
            d = _f(r.get("monthlyCost"))
            if abs(d) < 0.005:
                continue
            top.append({"name": r.get("name", ""), "monthly_diff": d})
    top.sort(key=lambda r: abs(r["monthly_diff"]), reverse=True)

    return CostImpact(
        currency=currency,
        past_monthly=past,
        monthly=cur,
        diff_monthly=diff,
        top_resources=top[:5],
    )


def render_line(impact: CostImpact) -> str:
    if not impact.has_impact:
        return f"_Estimated cost change: ~0 {impact.currency}/mo (Infracost)._"
    sign = "+" if impact.diff_monthly > 0 else "−"
    return (
        f"**Estimated cost change: {sign}{abs(impact.diff_monthly):.2f} {impact.currency}/mo** "
        f"(Infracost; total {impact.monthly:.2f}/mo)"
    )


def render_section(impact: CostImpact) -> str:
    lines = [render_line(impact)]
    if impact.top_resources:
        lines.append("")
        lines.append("| Resource | Monthly Δ |")
        lines.append("| --- | ---: |")
        for r in impact.top_resources:
            sign = "+" if r["monthly_diff"] > 0 else "−"
            lines.append(
                f"| `{r['name']}` | {sign}{abs(r['monthly_diff']):.2f} {impact.currency} |"
            )
    return "\n".join(lines)
