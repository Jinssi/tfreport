"""Deterministic visualization helpers for tfreport.

Pure Python, no runtime dependencies. Targets renderers that ship natively
with GitHub, GitLab, and Azure DevOps markdown:

* Mermaid (pie, xychart-beta, flowchart) for charts and graphs.
* Unicode sparkbars for inline meters and trends.
* Emoji severity badges for at-a-glance signals.

Every public helper returns either a string or list[str] of markdown-ready
lines so callers can ``lines.extend(viz.xxx())`` without bookkeeping.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

# -- Constants ----------------------------------------------------------------

_SPARK_TICKS = "▁▂▃▄▅▆▇█"

SEVERITY_EMOJI: dict[str | None, str] = {
    "high": "🟥",
    "medium": "🟧",
    "low": "🟨",
    None: "🟩",
}

ACTION_EMOJI: dict[str, str] = {
    "create": "🟢",
    "update": "🟡",
    "delete": "🔴",
    "replace": "🟣",
    "read": "⚪",
    "no-op": "⚪",
}

# Mermaid diagram caps so GitHub's ~50KB limit is respected and reports stay
# scannable even on huge plans.
MAX_PIE_SLICES = 8
MAX_BAR_ROWS = 10
MAX_FLOW_NODES = 30


# -- Sparkbars and badges -----------------------------------------------------


def sparkbar(values: Iterable[float], *, width: int | None = None) -> str:
    """Render numeric values as an inline Unicode sparkbar.

    Empty input returns an empty string. All-zero input returns flat low ticks
    so the caller can still show "we have data, all zero".
    """
    nums = [float(v) for v in values]
    if width is not None and len(nums) > width:
        nums = nums[-width:]
    if not nums:
        return ""
    hi = max(nums)
    if hi <= 0:
        return _SPARK_TICKS[0] * len(nums)
    last_idx = len(_SPARK_TICKS) - 1
    out: list[str] = []
    for v in nums:
        if v <= 0:
            out.append(_SPARK_TICKS[0])
            continue
        idx = int(round((v / hi) * last_idx))
        idx = max(0, min(last_idx, idx))
        out.append(_SPARK_TICKS[idx])
    return "".join(out)


def severity_badge(sev: str | None) -> str:
    return SEVERITY_EMOJI.get(sev, SEVERITY_EMOJI[None])


def action_badge(action: str) -> str:
    return ACTION_EMOJI.get(action, "⚪")


# -- Mermaid emitters ---------------------------------------------------------


def _mermaid_block(diagram_lines: list[str]) -> list[str]:
    return ["```mermaid", *diagram_lines, "```"]


def _sanitize_label(text: str) -> str:
    """Escape characters that break Mermaid node/slice labels."""
    return (
        text.replace('"', "'")
        .replace("[", "(")
        .replace("]", ")")
        .replace("{", "(")
        .replace("}", ")")
    )


def mermaid_pie(title: str, slices: Mapping[str, float]) -> list[str]:
    """Render a Mermaid pie chart. Zero-value slices are dropped."""
    items = [(k, float(v)) for k, v in slices.items() if v]
    if not items:
        return []
    items.sort(key=lambda kv: kv[1], reverse=True)
    if len(items) > MAX_PIE_SLICES:
        head = items[: MAX_PIE_SLICES - 1]
        rest_total = sum(v for _, v in items[MAX_PIE_SLICES - 1 :])
        items = head + [("other", rest_total)]
    body = [f'pie showData title {_sanitize_label(title)}']
    for label, value in items:
        body.append(f'    "{_sanitize_label(label)}" : {value:g}')
    return _mermaid_block(body)


def mermaid_bar(title: str, rows: list[tuple[str, float]], *, x_label: str = "") -> list[str]:
    """Render a Mermaid xychart-beta vertical bar chart."""
    items = [(k, float(v)) for k, v in rows if v]
    if not items:
        return []
    items = items[:MAX_BAR_ROWS]
    labels = ", ".join(f'"{_sanitize_label(k)}"' for k, _ in items)
    values = ", ".join(f"{v:g}" for _, v in items)
    body = [
        "xychart-beta",
        f'    title "{_sanitize_label(title)}"',
        f'    x-axis [{labels}]',
        f'    y-axis "{_sanitize_label(x_label) or "Count"}"',
        f"    bar [{values}]",
    ]
    return _mermaid_block(body)


def mermaid_module_tree(
    modules: list[Mapping[str, object]],
    *,
    title: str = "Module map",
) -> list[str]:
    """Render a Mermaid flowchart showing modules and their severity colour.

    Each item must expose ``module`` (str), ``resources`` (int), ``high``
    (int), ``medium`` (int). Nodes are coloured red / amber / green based on
    the highest severity. The root module is shown at the top.
    """
    if not modules:
        return []
    items = list(modules)[:MAX_FLOW_NODES]
    body = ["flowchart TD", '    classDef high fill:#ffb3b3,stroke:#b30000,color:#000;',
            "    classDef med fill:#ffd9a3,stroke:#cc6600,color:#000;",
            "    classDef low fill:#d4edda,stroke:#155724,color:#000;"]
    # Title via a comment node.
    body.append(f'    %% {_sanitize_label(title)}')
    for idx, item in enumerate(items):
        node_id = f"m{idx}"
        mod = str(item.get("module", "?"))
        resources = int(item.get("resources", 0) or 0)
        high = int(item.get("high", 0) or 0)
        medium = int(item.get("medium", 0) or 0)
        label = _sanitize_label(f"{mod}\\n{resources} res")
        body.append(f'    {node_id}["{label}"]')
        klass = "high" if high else ("med" if medium else "low")
        body.append(f"    class {node_id} {klass};")
    if len(modules) > MAX_FLOW_NODES:
        body.append(f'    more["... +{len(modules) - MAX_FLOW_NODES} more modules"]')
    return _mermaid_block(body)


# -- Composite dashboard helpers ---------------------------------------------


def render_dashboard(summary_dict: Mapping[str, object]) -> list[str]:
    """Render the top-of-report `## Dashboard` section.

    Reads from ``PlanSummary.to_dict()`` so this helper stays decoupled from
    the dataclass. Returns markdown-ready lines or an empty list when there
    is no signal to show (e.g. fully no-op plans).
    """
    stats = summary_dict.get("stats") or {}
    risks = summary_dict.get("risks_by_severity") or {}
    admin = summary_dict.get("admin_summary") or {}
    if not isinstance(stats, Mapping) or not isinstance(risks, Mapping):
        return []
    if not int(stats.get("total_changing", 0) or 0):
        return []

    lines: list[str] = ["## Dashboard", ""]

    # Headline badge row.
    headline_parts: list[str] = []
    sev_total = sum(int(risks.get(k, 0) or 0) for k in ("high", "medium", "low"))
    posture_sev = (
        "high" if int(risks.get("high", 0) or 0)
        else "medium" if int(risks.get("medium", 0) or 0)
        else "low" if int(risks.get("low", 0) or 0)
        else None
    )
    headline_parts.append(
        f"{severity_badge(posture_sev)} **{stats.get('total_changing', 0)} change(s)**"
    )
    headline_parts.append(f"🟢 {stats.get('create', 0)} create")
    headline_parts.append(f"🟡 {stats.get('update', 0)} update")
    headline_parts.append(f"🔴 {stats.get('delete', 0)} delete")
    headline_parts.append(f"🟣 {stats.get('replace', 0)} replace")
    headline_parts.append(f"⚠️ {sev_total} risk(s)")
    lines.append(" · ".join(headline_parts))
    lines.append("")

    # Action mix pie.
    action_slices = {
        "create": int(stats.get("create", 0) or 0),
        "update": int(stats.get("update", 0) or 0),
        "delete": int(stats.get("delete", 0) or 0),
        "replace": int(stats.get("replace", 0) or 0),
    }
    pie = mermaid_pie("Action mix", action_slices)
    if pie:
        lines.append("### Action mix")
        lines.append("")
        lines.extend(pie)
        lines.append("")

    # Risk severity bar.
    risk_rows: list[tuple[str, float]] = [
        ("high", float(risks.get("high", 0) or 0)),
        ("medium", float(risks.get("medium", 0) or 0)),
        ("low", float(risks.get("low", 0) or 0)),
    ]
    bar = mermaid_bar("Risks by severity", risk_rows, x_label="Resources")
    if bar and any(v for _, v in risk_rows):
        lines.append("### Risk profile")
        lines.append("")
        lines.extend(bar)
        lines.append("")

    # Domain impact heatmap (Unicode bars in a markdown table).
    domains = admin.get("affected_domains") if isinstance(admin, Mapping) else None
    if isinstance(domains, list) and domains:
        lines.append("### Impact heatmap")
        lines.append("")
        lines.append("| Area | Resources | Load |")
        lines.append("| --- | ---: | --- |")
        max_res = max(int(d.get("resources", 0) or 0) for d in domains) or 1
        for d in domains:
            res = int(d.get("resources", 0) or 0)
            bar_width = max(1, int(round((res / max_res) * 12))) if res else 0
            meter = "█" * bar_width if bar_width else "·"
            lines.append(f"| {d.get('label', d.get('domain', '?'))} | {res} | `{meter}` |")
        lines.append("")

    return lines
