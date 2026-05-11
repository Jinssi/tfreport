"""Summarise a Terraform plan JSON file (output of `terraform show -json tfplan`).

Produces a Markdown report and an optional structured JSON summary.

Sections (in order):
  # Terraform Plan Summary
  [provenance line]
  **N changes**: ...
  [cost line, if --cost-json]

  ## Stats
  ## Compared to baseline   (if --baseline)
  ## Cost impact            (if --cost-json and details)
  ## Changes                (grouped by module if config.group_by_module)
  ## Tag-only updates       (collapsed; if config.demote_tag_only)
  ## Risks
  ## Resource details       (collapsible per resource; if config.diff_details)
  ## Narrative              (optional, LLM)

CLI exit codes:
  0 = success (advisory mode — never fails on risk)
  2 = parse / IO error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from . import cost as cost_mod
from . import delta as delta_mod
from . import provenance as prov
from .config import Config, load as load_config
from .diff import (
    attr_diffs,
    changed_top_level_keys,
    is_tag_only,
    keyed_block_diff,
    list_element_diff,
    module_of,
    replace_paths,
)
from .risk_rules import (
    ACTION_CREATE,
    ACTION_DELETE,
    ACTION_NOOP,
    ACTION_READ,
    ACTION_REPLACE,
    ACTION_UPDATE,
    classify,
    normalize_actions,
)


@dataclass
class ResourceChange:
    address: str
    type: str
    name: str
    provider: str
    action: str
    module: str = "(root)"
    risks: list[dict[str, str]] = field(default_factory=list)
    changed_attrs: list[str] = field(default_factory=list)
    attr_diffs: list[dict[str, str]] = field(default_factory=list)
    list_diffs: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    block_diffs: dict[str, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)
    replace_paths: list[str] = field(default_factory=list)
    tag_only: bool = False
    ignored: bool = False

    @property
    def highest_severity(self) -> str | None:
        order = {"high": 3, "medium": 2, "low": 1}
        if not self.risks:
            return None
        return max(self.risks, key=lambda r: order.get(r["severity"], 0))["severity"]


@dataclass
class PlanSummary:
    terraform_version: str
    stats: dict[str, int]
    changes: list[ResourceChange]
    risks_by_severity: dict[str, int]
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "terraform_version": self.terraform_version,
            "stats": self.stats,
            "risks_by_severity": self.risks_by_severity,
            "provenance": self.provenance,
            "changes": [asdict(c) for c in self.changes],
        }


def parse_plan(plan: dict[str, Any], config: Config | None = None) -> PlanSummary:
    config = config or Config()
    rcs = plan.get("resource_changes") or []
    changes: list[ResourceChange] = []
    counter: Counter[str] = Counter()

    for rc in rcs:
        change = rc.get("change") or {}
        actions = change.get("actions") or []
        action = normalize_actions(actions)
        if action in (ACTION_NOOP, ACTION_READ):
            counter[action] += 1
            continue
        rtype = rc.get("type", "")
        addr = rc.get("address", "")
        rules = classify(rtype, action)
        risks = [{"name": r.name, "severity": r.severity, "reason": r.reason} for r in rules]
        before = change.get("before")
        after = change.get("after")
        keys = changed_top_level_keys(before, after)
        diffs = attr_diffs(
            before,
            after,
            change.get("before_sensitive"),
            change.get("after_sensitive"),
        )
        # Compute list-element diffs and keyed-block diffs for each changed key.
        list_diffs: dict[str, dict[str, list[str]]] = {}
        block_diffs: dict[str, dict[str, list[dict[str, Any]]]] = {}
        if isinstance(before, dict) and isinstance(after, dict):
            for k in keys:
                b_val = before.get(k)
                a_val = after.get(k)
                bd = keyed_block_diff(k, b_val, a_val)
                if bd is not None:
                    block_diffs[k] = bd
                    continue
                ld = list_element_diff(b_val, a_val)
                if ld is not None:
                    list_diffs[k] = ld
        rpaths = replace_paths(change)
        tag_only = action == ACTION_UPDATE and is_tag_only(keys)
        ignored = config.is_ignored(addr)
        changes.append(
            ResourceChange(
                address=addr,
                type=rtype,
                name=rc.get("name", ""),
                provider=rc.get("provider_name", ""),
                action=action,
                module=module_of(addr),
                risks=risks,
                changed_attrs=keys,
                attr_diffs=diffs,
                list_diffs=list_diffs,
                block_diffs=block_diffs,
                replace_paths=rpaths,
                tag_only=tag_only,
                ignored=ignored,
            )
        )
        counter[action] += 1

    stats = {
        "create": counter.get(ACTION_CREATE, 0),
        "update": counter.get(ACTION_UPDATE, 0),
        "delete": counter.get(ACTION_DELETE, 0),
        "replace": counter.get(ACTION_REPLACE, 0),
        "read": counter.get(ACTION_READ, 0),
        "no_op": counter.get(ACTION_NOOP, 0),
        "total_changing": (
            counter.get(ACTION_CREATE, 0)
            + counter.get(ACTION_UPDATE, 0)
            + counter.get(ACTION_DELETE, 0)
            + counter.get(ACTION_REPLACE, 0)
        ),
    }

    sev_counter: Counter[str] = Counter()
    for c in changes:
        if c.ignored:
            continue
        for r in c.risks:
            sev_counter[r["severity"]] += 1
    risks_by_severity = {
        "high": sev_counter.get("high", 0),
        "medium": sev_counter.get("medium", 0),
        "low": sev_counter.get("low", 0),
    }

    return PlanSummary(
        terraform_version=plan.get("terraform_version", "unknown"),
        stats=stats,
        changes=changes,
        risks_by_severity=risks_by_severity,
        provenance=prov.gather(plan),
    )


# -- rendering helpers --------------------------------------------------------

_ACTION_SYM = {
    ACTION_CREATE: "+",
    ACTION_UPDATE: "~",
    ACTION_DELETE: "-",
    ACTION_REPLACE: "+/-",
}
_ACTION_ORDER = {ACTION_REPLACE: 0, ACTION_DELETE: 1, ACTION_UPDATE: 2, ACTION_CREATE: 3}


def _severity_badge(sev: str | None) -> str:
    if sev == "high":
        return "**HIGH**"
    if sev == "medium":
        return "MEDIUM"
    if sev == "low":
        return "low"
    return ""


def _why_text(c: ResourceChange) -> str:
    bits: list[str] = []
    if c.action == ACTION_REPLACE and c.replace_paths:
        bits.append("replace because: " + ", ".join(f"`{p}`" for p in c.replace_paths[:3]))
    if c.changed_attrs:
        shown = c.changed_attrs[:5]
        more = f" (+{len(c.changed_attrs) - 5})" if len(c.changed_attrs) > 5 else ""
        bits.append("changed: " + ", ".join(f"`{k}`" for k in shown) + more)
    return "; ".join(bits)


def _render_changes_table(changes: list[ResourceChange]) -> list[str]:
    if not changes:
        return ["_No changing resources._"]
    lines = ["| Action | Resource | Type | Risk | Why |", "| --- | --- | --- | --- | --- |"]
    sorted_changes = sorted(changes, key=lambda c: (_ACTION_ORDER.get(c.action, 99), c.address))
    for c in sorted_changes:
        sym = _ACTION_SYM.get(c.action, c.action)
        addr = f"~~`{c.address}`~~ _(ignored)_" if c.ignored else f"`{c.address}`"
        lines.append(
            f"| `{sym}` | {addr} | `{c.type}` | "
            f"{_severity_badge(c.highest_severity)} | {_why_text(c)} |"
        )
    return lines


def render_markdown(
    summary: PlanSummary,
    *,
    config: Config | None = None,
    narrative: str | None = None,
    delta: delta_mod.Delta | None = None,
    cost: cost_mod.CostImpact | None = None,
    heading: str | None = None,
) -> str:
    config = config or Config()
    s = summary.stats
    lines: list[str] = []
    lines.append("# Terraform Plan Summary")
    if heading:
        lines.append("")
        lines.append(f"## {heading}")
    lines.append("")
    footer = prov.render_footer(summary.provenance)
    if footer:
        lines.append(f"_{footer}_")
        lines.append("")

    lines.append(
        f"**{s['total_changing']} resource change(s)**: "
        f"{s['create']} create, {s['update']} update, "
        f"{s['delete']} delete, {s['replace']} replace."
    )
    if cost:
        lines.append("")
        lines.append(cost_mod.render_line(cost))
    lines.append("")

    # -- Stats --------------------------------------------------------------
    lines.append("## Stats")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("| --- | ---: |")
    lines.append(f"| Create | {s['create']} |")
    lines.append(f"| Update | {s['update']} |")
    lines.append(f"| Delete | {s['delete']} |")
    lines.append(f"| Replace | {s['replace']} |")
    lines.append(f"| Read | {s['read']} |")
    lines.append(f"| No-op | {s['no_op']} |")
    lines.append("")
    rb = summary.risks_by_severity
    lines.append(
        f"_Risks: {rb['high']} high, {rb['medium']} medium, {rb['low']} low (advisory only)._"
    )
    lines.append("")

    # -- Baseline delta -----------------------------------------------------
    if delta is not None:
        lines.append("## Compared to baseline")
        if delta.baseline_path:
            lines.append("")
            lines.append(f"_Baseline: `{delta.baseline_path}`_")
        lines.append("")
        lines.append(delta_mod.render_section(delta))
        lines.append("")

    # -- Cost section -------------------------------------------------------
    if cost and cost.top_resources:
        lines.append("## Cost impact")
        lines.append("")
        lines.append(cost_mod.render_section(cost))
        lines.append("")

    # -- Partition changes --------------------------------------------------
    visible = [c for c in summary.changes if not c.ignored]
    tag_only_changes: list[ResourceChange] = []
    if config.demote_tag_only:
        tag_only_changes = [c for c in visible if c.tag_only]
        visible = [c for c in visible if not c.tag_only]
    ignored_changes = [c for c in summary.changes if c.ignored]

    # -- Changes section ----------------------------------------------------
    lines.append("## Changes")
    lines.append("")
    if not visible:
        lines.append("_No changing resources._")
    elif config.group_by_module:
        groups: dict[str, list[ResourceChange]] = defaultdict(list)
        for c in visible:
            groups[c.module].append(c)
        # Stable order: root first, then alphabetical.
        keys = sorted(groups.keys(), key=lambda k: (0 if k == "(root)" else 1, k))
        for k in keys:
            grp = groups[k]
            lines.append(f"### {k} ({len(grp)})")
            lines.append("")
            lines.extend(_render_changes_table(grp))
            lines.append("")
    else:
        lines.extend(_render_changes_table(visible))
        lines.append("")

    # -- Tag-only collapsed -------------------------------------------------
    if tag_only_changes:
        lines.append(f"<details><summary>Tag-only updates ({len(tag_only_changes)})</summary>")
        lines.append("")
        lines.extend(_render_changes_table(tag_only_changes))
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # -- Ignored ------------------------------------------------------------
    if ignored_changes:
        lines.append(f"<details><summary>Ignored by config ({len(ignored_changes)})</summary>")
        lines.append("")
        lines.extend(_render_changes_table(ignored_changes))
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # -- Risks --------------------------------------------------------------
    lines.append("## Risks")
    lines.append("")
    risky = [c for c in visible if c.risks]
    if not risky:
        lines.append("_No risks flagged._")
    else:
        order = {"high": 0, "medium": 1, "low": 2}
        risky.sort(key=lambda c: order.get(c.highest_severity or "low", 9))
        for c in risky:
            top = c.risks[0]
            extra = ""
            if len(c.risks) > 1:
                extra = " " + ", ".join(f"_{r['name']}_" for r in c.risks[1:])
            lines.append(
                f"- {_severity_badge(top['severity'])} `{c.address}` "
                f"({c.action}) — {top['reason']}{extra}"
            )
    lines.append("")

    # -- Resource details ---------------------------------------------------
    if config.diff_details and visible:
        nontrivial = [c for c in visible if c.changed_attrs or c.replace_paths]
        if nontrivial:
            lines.append("## Resource details")
            lines.append("")
            for c in nontrivial[:50]:
                lines.append(
                    f"<details><summary>{_severity_badge(c.highest_severity) or c.action} "
                    f"<code>{c.address}</code></summary>"
                )
                lines.append("")
                if c.replace_paths:
                    lines.append(
                        "**Replace forced by:** "
                        + ", ".join(f"`{p}`" for p in c.replace_paths)
                    )
                if c.changed_attrs:
                    lines.append(
                        "**Changed attributes:** "
                        + ", ".join(f"`{k}`" for k in c.changed_attrs)
                    )
                # Per-attribute before → after table (only meaningful for update/replace)
                if c.action in (ACTION_UPDATE, ACTION_REPLACE) and c.attr_diffs:
                    lines.append("")
                    lines.append("| Attribute | Before | After |")
                    lines.append("| --- | --- | --- |")
                    for d in c.attr_diffs[:10]:
                        lines.append(
                            f"| `{d['key']}` | {d['before']} | {d['after']} |"
                        )
                    if len(c.attr_diffs) > 10:
                        lines.append(f"| _… +{len(c.attr_diffs) - 10} more_ | | |")
                # List-element diffs (e.g. address_space, dns_servers, ip_rules)
                for k, ld in c.list_diffs.items():
                    lines.append("")
                    lines.append(f"**List diff — `{k}`:**")
                    if ld.get("added"):
                        lines.append(
                            "- added: "
                            + ", ".join(f"`{x}`" for x in ld["added"])
                        )
                    if ld.get("removed"):
                        lines.append(
                            "- removed: "
                            + ", ".join(f"`{x}`" for x in ld["removed"])
                        )
                # Keyed nested-block diffs (NSG rules, firewall rules, routes, …)
                for k, bd in c.block_diffs.items():
                    added = bd.get("added", [])
                    removed = bd.get("removed", [])
                    changed = bd.get("changed", [])
                    if not (added or removed or changed):
                        continue
                    lines.append("")
                    lines.append(f"**Rule diff — `{k}`:**")
                    if added:
                        names = ", ".join(f"`{e['key']}`" for e in added[:10])
                        extra = f" (+{len(added) - 10} more)" if len(added) > 10 else ""
                        lines.append(f"- added: {names}{extra}")
                    if removed:
                        names = ", ".join(f"`{e['key']}`" for e in removed[:10])
                        extra = f" (+{len(removed) - 10} more)" if len(removed) > 10 else ""
                        lines.append(f"- removed: {names}{extra}")
                    for entry in changed[:5]:
                        lines.append(f"- changed `{entry['key']}`:")
                        for ad in (entry.get("attrs") or [])[:5]:
                            lines.append(
                                f"  - `{ad['key']}`: {ad['before']} → {ad['after']}"
                            )
                    if len(changed) > 5:
                        lines.append(f"- _… +{len(changed) - 5} more changed rules_")
                if c.risks:
                    lines.append(
                        "**Risk rules:** "
                        + ", ".join(f"_{r['name']}_ ({r['severity']})" for r in c.risks)
                    )
                lines.append("")
                lines.append("</details>")
            if len(nontrivial) > 50:
                lines.append(f"_(+{len(nontrivial) - 50} more in artifact)_")
            lines.append("")

    # -- Narrative ----------------------------------------------------------
    if narrative:
        lines.append("## Narrative")
        lines.append("")
        lines.append(narrative.strip())
        lines.append("")

    lines.append("---")
    lines.append("_Report generated by tfreport (advisory only). Open an issue to tune risk rules._")
    if config.source:
        lines.append(f"_Config: `{config.source}`_")
    return "\n".join(lines) + "\n"


# -- CLI ---------------------------------------------------------------------

def _read_input(path: str) -> dict[str, Any]:
    if path == "-":
        data = sys.stdin.read()
    else:
        with open(path, "r", encoding="utf-8") as f:
            data = f.read()
    return json.loads(data)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Summarise a Terraform plan JSON file.")
    p.add_argument("plan", help="Path to plan JSON, or '-' for stdin.")
    p.add_argument("--out", default="plan_summary.md", help="Output Markdown path.")
    p.add_argument("--json-out", default=None, help="Also write structured JSON summary here.")
    p.add_argument("--config", default=None, help="Path to .tfreport config; auto-discovered otherwise.")
    p.add_argument("--baseline", default=None, help="Previous plan_summary.json for delta.")
    p.add_argument("--cost-json", default=None, help="Infracost breakdown JSON.")
    p.add_argument("--heading", default=None, help="Optional H2 heading (multi-stack reports).")
    p.add_argument("--ai", action="store_true", help="Append LLM narrative section.")
    p.add_argument(
        "--ai-backend",
        default=os.environ.get("LLM_BACKEND", "github_models"),
        help="LLM backend: github_models | azure_openai | none.",
    )
    args = p.parse_args(argv)

    try:
        plan = _read_input(args.plan)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: failed to read plan JSON: {e}", file=sys.stderr)
        return 2

    try:
        config = load_config(args.config)
    except (OSError, RuntimeError, FileNotFoundError) as e:
        print(f"warning: config load failed: {e}", file=sys.stderr)
        config = Config()

    summary = parse_plan(plan, config=config)

    delta = None
    if args.baseline:
        try:
            base = delta_mod.load_baseline(args.baseline)
            delta = delta_mod.compute(summary.to_dict(), base, baseline_path=args.baseline)
        except (OSError, json.JSONDecodeError) as e:
            print(f"warning: baseline load failed: {e}", file=sys.stderr)

    cost = None
    if args.cost_json:
        try:
            cost = cost_mod.parse(cost_mod.load(args.cost_json))
        except (OSError, json.JSONDecodeError, KeyError) as e:
            print(f"warning: cost JSON load failed: {e}", file=sys.stderr)

    narrative: str | None = None
    use_ai = args.ai or config.ai
    if use_ai and args.ai_backend != "none":
        try:
            from .narrative import generate_narrative

            narrative = generate_narrative(summary.to_dict(), backend=args.ai_backend)
        except Exception as e:  # narrative is optional — never fail the run
            print(f"warning: narrative generation failed: {e}", file=sys.stderr)

    md = render_markdown(
        summary,
        config=config,
        narrative=narrative,
        delta=delta,
        cost=cost,
        heading=args.heading,
    )
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(md)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(summary.to_dict(), f, indent=2)

    print(f"wrote {args.out} ({summary.stats['total_changing']} changes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
