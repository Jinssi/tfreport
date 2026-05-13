"""Self-contained HTML exporter for plan summaries.

Produces a single .html file with no external network calls. Uses inline
CSS, plain SVG bar charts (rendered server-side), and the report markdown
converted to minimal HTML. No JS dependencies are bundled at this time;
the layout is print- and PR-comment-friendly.
"""

from __future__ import annotations

import html as _html
from collections.abc import Mapping
from typing import Any

_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; margin: 2em; color: #1f2328; max-width: 1100px; }
h1, h2, h3 { border-bottom: 1px solid #d0d7de; padding-bottom: 0.3em; }
.badges { display: flex; gap: 0.5em; flex-wrap: wrap; margin: 0.5em 0 1.5em; }
.badge { padding: 0.25em 0.75em; border-radius: 12px; font-size: 0.85em; font-weight: 600; }
.b-create { background: #dafbe1; color: #1a7f37; }
.b-update { background: #fff8c5; color: #9a6700; }
.b-delete { background: #ffebe9; color: #cf222e; }
.b-replace { background: #fbefff; color: #8250df; }
.b-noop { background: #eaeef2; color: #57606a; }
.b-high { background: #ffebe9; color: #cf222e; }
.b-medium { background: #fff8c5; color: #9a6700; }
.b-low { background: #dafbe1; color: #1a7f37; }
table { border-collapse: collapse; margin: 1em 0; }
th, td { border: 1px solid #d0d7de; padding: 4px 8px; text-align: left; }
th { background: #f6f8fa; }
.bar-row { display: flex; align-items: center; gap: 0.5em; margin: 2px 0; font-family: monospace; }
.bar { background: #218bff; height: 12px; border-radius: 2px; }
.muted { color: #57606a; }
pre { background: #f6f8fa; padding: 12px; border-radius: 6px; overflow: auto; }
"""


def _esc(s: Any) -> str:
    return _html.escape(str(s))


def _bar_chart(rows: list[tuple[str, float]], max_width_px: int = 400) -> str:
    if not rows:
        return ""
    mx = max(v for _, v in rows) or 1.0
    out = ['<div class="chart">']
    for label, v in rows:
        w = int(max(2, (v / mx) * max_width_px))
        out.append(
            f'<div class="bar-row"><span style="width:220px;display:inline-block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{_esc(label)}</span>'
            f'<span class="bar" style="width:{w}px"></span>'
            f'<span class="muted">{v:g}</span></div>'
        )
    out.append("</div>")
    return "\n".join(out)


def render(summary: Mapping[str, Any], *, title: str = "Terraform Plan Report") -> str:
    stats = summary.get("stats") or {}
    risks_by_sev = summary.get("risks_by_severity") or {}
    risks = summary.get("risks") or []
    changes = summary.get("changes") or []
    compliance = summary.get("compliance") or {}
    policy_changes = summary.get("policy_changes") or []

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append(
        f'<html><head><meta charset="utf-8"><title>{_esc(title)}</title><style>{_CSS}</style></head><body>'
    )
    parts.append(f"<h1>{_esc(title)}</h1>")

    # Headline badges.
    parts.append('<div class="badges">')
    parts.append(f'<span class="badge b-create">+{stats.get("create", 0)} create</span>')
    parts.append(f'<span class="badge b-update">~{stats.get("update", 0)} update</span>')
    parts.append(f'<span class="badge b-delete">-{stats.get("delete", 0)} delete</span>')
    parts.append(f'<span class="badge b-replace">↻{stats.get("replace", 0)} replace</span>')
    parts.append(f'<span class="badge b-high">{risks_by_sev.get("high", 0)} high</span>')
    parts.append(f'<span class="badge b-medium">{risks_by_sev.get("medium", 0)} medium</span>')
    parts.append(f'<span class="badge b-low">{risks_by_sev.get("low", 0)} low</span>')
    if compliance.get("enabled"):
        score = compliance.get("score")
        if score is not None:
            parts.append(f'<span class="badge b-noop">Compliance {int(score * 100)}%</span>')
    parts.append("</div>")

    # Action mix bar chart.
    action_rows = [
        ("create", float(stats.get("create", 0) or 0)),
        ("update", float(stats.get("update", 0) or 0)),
        ("delete", float(stats.get("delete", 0) or 0)),
        ("replace", float(stats.get("replace", 0) or 0)),
    ]
    parts.append("<h2>Action mix</h2>")
    parts.append(_bar_chart([(l, v) for l, v in action_rows if v > 0]))

    # Risks.
    if risks:
        parts.append("<h2>Risks</h2>")
        parts.append("<table><thead><tr><th>Severity</th><th>Rule</th><th>Address</th><th>Message</th></tr></thead><tbody>")
        for r in risks[:50]:
            sev = (r.get("severity") or "low").lower()
            parts.append(
                f'<tr><td><span class="badge b-{_esc(sev)}">{_esc(sev)}</span></td>'
                f"<td>{_esc(r.get('rule_id', ''))}</td>"
                f"<td><code>{_esc(r.get('address', ''))}</code></td>"
                f"<td>{_esc(r.get('message', ''))}</td></tr>"
            )
        parts.append("</tbody></table>")

    # Compliance findings.
    findings = (compliance.get("findings") or []) if isinstance(compliance, Mapping) else []
    if findings:
        parts.append("<h2>Compliance findings</h2>")
        parts.append("<table><thead><tr><th>Rule</th><th>Severity</th><th>Address</th><th>Message</th></tr></thead><tbody>")
        for f in findings[:50]:
            sev = (f.get("severity") or "low").lower()
            parts.append(
                f"<tr><td>{_esc(f.get('rule', ''))}</td>"
                f'<td><span class="badge b-{_esc(sev)}">{_esc(sev)}</span></td>'
                f"<td><code>{_esc(f.get('address', ''))}</code></td>"
                f"<td>{_esc(f.get('message', ''))}</td></tr>"
            )
        parts.append("</tbody></table>")

    # Policy changes.
    if policy_changes:
        parts.append("<h2>Policy changes</h2>")
        parts.append("<table><thead><tr><th>Address</th><th>Effect</th><th>Tightening</th></tr></thead><tbody>")
        for p in policy_changes[:50]:
            tight = "🟥" if p.get("effect_tightening") else ""
            parts.append(
                f"<tr><td><code>{_esc(p.get('address', ''))}</code></td>"
                f"<td>{_esc(p.get('effect_before') or '—')} → {_esc(p.get('effect_after') or '—')}</td>"
                f"<td>{tight}</td></tr>"
            )
        parts.append("</tbody></table>")

    # Top changes.
    if changes:
        parts.append("<h2>Changes</h2>")
        parts.append("<table><thead><tr><th>Address</th><th>Action</th><th>Type</th><th>Module</th></tr></thead><tbody>")
        for c in changes[:200]:
            parts.append(
                f"<tr><td><code>{_esc(c.get('address', ''))}</code></td>"
                f"<td>{_esc(c.get('action', ''))}</td>"
                f"<td>{_esc(c.get('resource_type', ''))}</td>"
                f"<td>{_esc(c.get('module', '(root)') or '(root)')}</td></tr>"
            )
        parts.append("</tbody></table>")
        if len(changes) > 200:
            parts.append(f'<p class="muted">Showing first 200 of {len(changes)} changes.</p>')

    parts.append('<hr><p class="muted">Generated by tfreport.</p>')
    parts.append("</body></html>")
    return "\n".join(parts)
