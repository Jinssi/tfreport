"""Summarise a Terraform apply run.

Inputs:
  --log                 path to captured stdout/stderr from `terraform apply`
  --plan-summary-json   optional JSON from summarize_plan; enriches the report

Outputs:
  --out                 Markdown report
  --history             append a JSONL entry to this path (always, not just on failure)

Exit codes:
  0 = apply reported success
  1 = apply reported failure (advisory)
  2 = parse / IO error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import provenance as prov


_APPLY_RESULT_RE = re.compile(
    r"Apply complete!\s+Resources:\s+(?P<added>\d+)\s+added,\s+"
    r"(?P<changed>\d+)\s+changed,\s+(?P<destroyed>\d+)\s+destroyed\.?",
)
_ERROR_RE = re.compile(r"^(Error|╷)", re.MULTILINE)
_RES_ACTION_RE = re.compile(
    r"^(?P<addr>[\w\.\[\]\"\-]+):\s+(?P<verb>Creating|Modifying|Destroying|Reading|"
    r"Creation complete|Modifications complete|Destruction complete|Refreshing state)"
    r"(?:\s+after\s+(?P<duration>[\dhms\.]+))?",
    re.MULTILINE,
)


@dataclass
class ApplyResult:
    succeeded: bool
    added: int
    changed: int
    destroyed: int
    errors: list[str]
    per_resource: list[dict[str, str]]


def parse_apply_log(text: str) -> ApplyResult:
    m = _APPLY_RESULT_RE.search(text)
    succeeded = m is not None
    added = int(m.group("added")) if m else 0
    changed = int(m.group("changed")) if m else 0
    destroyed = int(m.group("destroyed")) if m else 0

    errors: list[str] = []
    for em in _ERROR_RE.finditer(text):
        start = em.start()
        chunk = text[start : start + 4000]
        end = chunk.find("\n\n\n")
        if end != -1:
            chunk = chunk[:end]
        errors.append(chunk.strip())

    per_resource: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for am in _RES_ACTION_RE.finditer(text):
        key = (am.group("addr"), am.group("verb"))
        if key in seen:
            continue
        seen.add(key)
        per_resource.append(
            {
                "address": am.group("addr"),
                "verb": am.group("verb"),
                "duration": am.group("duration") or "",
            }
        )

    return ApplyResult(
        succeeded=succeeded and not errors,
        added=added,
        changed=changed,
        destroyed=destroyed,
        errors=errors,
        per_resource=per_resource,
    )


def _duration_seconds(text: str) -> float:
    """Parse Terraform-style durations like '3m12s', '500ms', '12.3s'."""
    if not text:
        return 0.0
    text = text.strip()
    # Try a pure float seconds variant first.
    if text.endswith("ms"):
        try:
            return float(text[:-2]) / 1000.0
        except ValueError:
            return 0.0
    total = 0.0
    matched = False
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)([hms])", text):
        matched = True
        v = float(value)
        if unit == "h":
            total += v * 3600
        elif unit == "m":
            total += v * 60
        elif unit == "s":
            total += v
    if matched:
        return total
    try:
        return float(text)
    except ValueError:
        return 0.0


def _resource_durations(result: ApplyResult) -> list[tuple[str, float]]:
    """Return (address, seconds) for completed resources, longest first."""
    rows: list[tuple[str, float]] = []
    for r in result.per_resource:
        verb = (r.get("verb") or "").lower()
        if "complete" not in verb:
            continue
        secs = _duration_seconds(r.get("duration", ""))
        if secs > 0:
            rows.append((r["address"], secs))
    rows.sort(key=lambda kv: kv[1], reverse=True)
    return rows


def _render_apply_dashboard(result: ApplyResult, plan_summary: dict[str, Any] | None) -> list[str]:
    """Render the apply-mode visual dashboard."""
    lines: list[str] = ["## Apply dashboard", ""]
    status = "✅ **Apply succeeded**" if result.succeeded else "🟥 **Apply did not complete cleanly**"
    parts = [
        status,
        f"🟢 {result.added} added",
        f"🟡 {result.changed} changed",
        f"🔴 {result.destroyed} destroyed",
        f"⚠️ {len(result.errors)} error(s)",
    ]
    lines.append(" · ".join(parts))
    lines.append("")
    if plan_summary:
        s = plan_summary.get("stats", {})
        from . import viz as viz_mod

        rows = [
            ("planned create", float(s.get("create", 0) or 0)),
            ("applied add", float(result.added)),
            ("planned update", float(s.get("update", 0) or 0)),
            ("applied changed", float(result.changed)),
            ("planned delete", float(s.get("delete", 0) or 0)),
            ("applied destroy", float(result.destroyed)),
        ]
        bar = viz_mod.mermaid_bar("Planned vs Applied", rows, x_label="Resources")
        if bar:
            lines.extend(bar)
            lines.append("")
    return lines


def _render_gantt(result: ApplyResult) -> list[str]:
    """Render a Mermaid gantt of the slowest completed resources."""
    durations = _resource_durations(result)
    if not durations:
        return []
    top = durations[:20]
    body = ["gantt", "    dateFormat X", "    axisFormat %S s", "    title Apply timeline (top 20 longest)"]
    cursor = 0.0
    for addr, secs in top:
        label = addr.replace(":", "_").replace('"', "'")
        # Truncate to keep gantt readable.
        if len(label) > 40:
            label = label[:37] + "..."
        body.append(f"    {label} :a, {int(cursor)}, {int(max(1, round(secs)))}s")
        cursor += max(1, round(secs))
    return ["```mermaid", *body, "```"]


def render_markdown(
    result: ApplyResult,
    *,
    plan_summary: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    metadata = metadata or {}
    lines: list[str] = []
    lines.append("# Terraform Apply Summary")
    lines.append("")
    footer = prov.render_footer(metadata)
    if footer:
        lines.append(f"_{footer}_")
        lines.append("")

    lines.extend(_render_apply_dashboard(result, plan_summary))

    if result.succeeded:
        lines.append(
            f"**Apply succeeded.** {result.added} added, "
            f"{result.changed} changed, {result.destroyed} destroyed."
        )
    else:
        lines.append("**Apply did not complete cleanly.**")
        if result.added or result.changed or result.destroyed:
            lines.append("")
            lines.append(
                f"Partial result: {result.added} added, "
                f"{result.changed} changed, {result.destroyed} destroyed."
            )
    lines.append("")

    if result.errors:
        lines.append("## Errors")
        lines.append("")
        for i, err in enumerate(result.errors, 1):
            lines.append(f"### Error {i}")
            lines.append("")
            lines.append("```")
            lines.append(err)
            lines.append("```")
            lines.append("")

    if plan_summary:
        s = plan_summary.get("stats", {})
        lines.append("## Planned vs Applied")
        lines.append("")
        lines.append("| Metric | Planned | Applied |")
        lines.append("| --- | ---: | ---: |")
        lines.append(f"| Create  | {s.get('create', 0)}  | {result.added} |")
        lines.append(f"| Update  | {s.get('update', 0)}  | {result.changed} |")
        lines.append(f"| Delete  | {s.get('delete', 0)}  | {result.destroyed} |")
        lines.append(f"| Replace | {s.get('replace', 0)} | _(counted in add+destroy)_ |")
        lines.append("")

    if result.per_resource:
        lines.append("## Per-resource activity (heuristic)")
        lines.append("")
        lines.append("| Resource | Action | Duration |")
        lines.append("| --- | --- | --- |")
        completes = [r for r in result.per_resource if "complete" in r["verb"].lower()]
        rows = completes or result.per_resource
        for r in rows[:200]:
            lines.append(f"| `{r['address']}` | {r['verb']} | {r['duration']} |")
        if len(rows) > 200:
            lines.append("")
            lines.append(f"_Showing first 200 of {len(rows)} resource events._")
        lines.append("")

    # Timing analytics (only when durations are present in the log).
    durations = _resource_durations(result)
    if durations:
        lines.append("## Slowest resources")
        lines.append("")
        lines.append("| Resource | Duration (s) |")
        lines.append("| --- | ---: |")
        for addr, secs in durations[:10]:
            lines.append(f"| `{addr}` | {secs:.1f} |")
        lines.append("")
        gantt = _render_gantt(result)
        if gantt:
            lines.extend(gantt)
            lines.append("")

    lines.append("---")
    lines.append("_Generated by tfreport (advisory)._")
    return "\n".join(lines) + "\n"


def append_history(
    path: str | Path,
    *,
    result: ApplyResult,
    plan_summary: dict[str, Any] | None,
    metadata: dict[str, Any],
) -> None:
    """Append a single-line JSON entry to a history ledger."""
    entry: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "succeeded": result.succeeded,
        "added": result.added,
        "changed": result.changed,
        "destroyed": result.destroyed,
        "error_count": len(result.errors),
        "ci": metadata.get("ci", {}),
        "terraform_version": metadata.get("terraform_version", ""),
        "tfreport_version": metadata.get("tfreport_version", ""),
    }
    if plan_summary:
        entry["planned_stats"] = plan_summary.get("stats", {})
        entry["risks_by_severity"] = plan_summary.get("risks_by_severity", {})
        entry["change_addresses"] = [c.get("address") for c in (plan_summary.get("changes") or [])]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Summarise a Terraform apply log.")
    p.add_argument("--log", required=True, help="Path to captured apply stdout/stderr.")
    p.add_argument(
        "--plan-summary-json",
        default=None,
        help="Optional JSON file from `tf-report-plan --json-out`.",
    )
    p.add_argument("--out", default="apply_summary.md", help="Output Markdown path.")
    p.add_argument(
        "--history",
        default=None,
        help="Append a JSONL entry to this path on every run.",
    )
    args = p.parse_args(argv)

    try:
        with open(args.log, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        print(f"error: failed to read apply log: {e}", file=sys.stderr)
        return 2

    plan_summary = None
    if args.plan_summary_json:
        try:
            with open(args.plan_summary_json, "r", encoding="utf-8") as f:
                plan_summary = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"warning: failed to load plan summary JSON: {e}", file=sys.stderr)

    result = parse_apply_log(text)
    metadata = (plan_summary or {}).get("provenance") or prov.gather()
    md = render_markdown(result, plan_summary=plan_summary, metadata=metadata)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(md)

    if args.history:
        try:
            append_history(args.history, result=result, plan_summary=plan_summary, metadata=metadata)
        except OSError as e:
            print(f"warning: failed to append history: {e}", file=sys.stderr)

    print(
        f"wrote {args.out} (succeeded={result.succeeded}, errors={len(result.errors)})"
    )
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
