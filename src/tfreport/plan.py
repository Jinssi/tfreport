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
  0 = success (advisory mode - never fails on risk)
  2 = parse / IO error
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from . import compliance as compliance_mod
from . import cost as cost_mod
from . import delta as delta_mod
from . import graph as graph_mod
from . import history as history_mod
from . import policy as policy_mod
from . import provenance as prov
from . import rollback as rollback_mod
from . import routing as routing_mod
from . import viz as viz_mod
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
    blast_radius_score: int = 0
    downstream: list[str] = field(default_factory=list)
    after_state: dict[str, Any] = field(default_factory=dict)

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
    admin_summary: dict[str, Any] = field(default_factory=dict)
    policy_changes: list[dict[str, Any]] = field(default_factory=list)
    compliance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        changes_out: list[dict[str, Any]] = []
        for c in self.changes:
            d = asdict(c)
            # after_state can be very large; consumers can opt in by reading
            # the source plan JSON directly. Keep summary lean.
            d.pop("after_state", None)
            changes_out.append(d)
        return {
            "terraform_version": self.terraform_version,
            "stats": self.stats,
            "risks_by_severity": self.risks_by_severity,
            "provenance": self.provenance,
            "admin_summary": self.admin_summary,
            "policy_changes": self.policy_changes,
            "compliance": self.compliance,
            "changes": changes_out,
        }


_DOMAIN_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "identity",
        (
            "azurerm_role_assignment",
            "azurerm_user_assigned_identity",
            "azurerm_federated_identity_credential",
            "azuread_*",
        ),
    ),
    (
        "network",
        (
            "azurerm_virtual_network",
            "azurerm_subnet",
            "azurerm_network_*",
            "azurerm_private_*",
            "azurerm_public_ip*",
            "azurerm_route*",
            "azurerm_firewall*",
            "azurerm_dns_*",
            "azurerm_api_management",
            "azapi_resource.vnet",
        ),
    ),
    (
        "compute",
        (
            "azurerm_*virtual_machine*",
            "azurerm_kubernetes_cluster",
            "azurerm_container_app*",
            "azurerm_app_service*",
            "azurerm_linux_web_app",
            "azurerm_windows_web_app",
            "azurerm_function_app*",
        ),
    ),
    (
        "data",
        (
            "azurerm_storage_account",
            "azurerm_key_vault*",
            "azurerm_cosmosdb_*",
            "azurerm_sql_*",
            "azurerm_postgresql_*",
            "azurerm_mysql_*",
            "azurerm_redis_*",
            "azurerm_data_factory*",
            "azurerm_machine_learning_workspace",
        ),
    ),
    (
        "monitoring",
        (
            "azurerm_monitor_*",
            "azurerm_log_analytics_workspace",
            "azurerm_application_insights",
        ),
    ),
)

_DOMAIN_TITLES = {
    "identity": "Identity & Access",
    "network": "Network & Connectivity",
    "compute": "Compute & Runtime",
    "data": "Data & Secrets",
    "monitoring": "Monitoring & Diagnostics",
    "platform": "Platform & Control Plane",
}

_AUXILIARY_TYPES = {
    "modtm_telemetry",
    "null_resource",
    "random_password",
    "random_uuid",
    "terraform_data",
    "time_sleep",
}


def _resource_domain(resource_type: str, address: str) -> str:
    for domain, patterns in _DOMAIN_RULES:
        if any(fnmatch.fnmatchcase(resource_type, pattern) for pattern in patterns):
            return domain
    if resource_type == "azapi_resource":
        lowered = address.lower()
        if any(token in lowered for token in ("vnet", "subnet", "private_endpoint", "dns")):
            return "network"
        if any(token in lowered for token in ("storage", "cosmos", "vault", "kv", "sql")):
            return "data"
    return "platform"


def _is_auxiliary_change(change: ResourceChange) -> bool:
    return change.type in _AUXILIARY_TYPES


def _build_admin_summary(
    changes: list[ResourceChange],
    stats: dict[str, int],
    risks_by_severity: dict[str, int],
) -> dict[str, Any]:
    visible = [c for c in changes if not c.ignored]
    meaningful = [c for c in visible if not c.tag_only and not _is_auxiliary_change(c)]
    domains_touched = sorted({_resource_domain(c.type, c.address) for c in meaningful})
    modules_touched = sorted({c.module for c in meaningful})
    noisy_count = len(visible) - len(meaningful)
    stateful = [
        c
        for c in visible
        if any(r["name"] in {"stateful-replace", "stateful-delete"} for r in c.risks)
    ]
    iam = [c for c in visible if any(r["name"] == "iam-change" for r in c.risks)]
    policy = [c for c in visible if any(r["name"] == "policy-change" for r in c.risks)]
    network_destructive = [
        c
        for c in visible
        if any(r["name"] == "network-replace-or-delete" for r in c.risks)
    ]
    large_updates = [
        c
        for c in meaningful
        if c.action in (ACTION_UPDATE, ACTION_REPLACE) and len(c.changed_attrs) >= 10
    ]

    if meaningful:
        headline = (
            f"{len(meaningful)} meaningful change(s) across {len(domains_touched)} operational area(s) "
            f"and {len(modules_touched)} module(s)."
        )
    elif visible:
        headline = "No major infrastructure changes detected; remaining changes are mostly low-signal or auxiliary."
    else:
        headline = "No infrastructure changes detected."

    posture_bits: list[str] = []
    if risks_by_severity.get("high"):
        posture_bits.append(f"{risks_by_severity['high']} high-risk item(s) require explicit approval")
    if stateful:
        posture_bits.append(f"{len(stateful)} stateful replace/delete operation(s)")
    if iam:
        posture_bits.append(f"{len(iam)} access-control change(s)")
    if noisy_count:
        posture_bits.append(f"{noisy_count} low-signal update(s) were deprioritised")
    posture = "; ".join(posture_bits) if posture_bits else "No elevated-risk patterns were detected."

    reviewer_focus: list[str] = []
    if stateful:
        reviewer_focus.append(
            f"Validate backup, migration, and rollback plans for {len(stateful)} stateful replace/delete change(s)."
        )
    if iam:
        reviewer_focus.append(
            f"Review blast radius and least-privilege impact for {len(iam)} identity/authorisation change(s)."
        )
    if network_destructive:
        reviewer_focus.append(
            f"Check outage risk and dependency sequencing for {len(network_destructive)} destructive network change(s)."
        )
    if policy:
        reviewer_focus.append(
            f"Confirm governance intent for {len(policy)} policy or management-scope change(s)."
        )
    if large_updates:
        reviewer_focus.append(
            f"Smoke-test {len(large_updates)} large in-place update(s) with 10+ changed attributes."
        )
    if not reviewer_focus:
        reviewer_focus.append("Proceed with routine validation; no destructive or privilege-heavy changes were detected.")

    domain_groups: dict[str, list[ResourceChange]] = defaultdict(list)
    for change in meaningful:
        domain_groups[_resource_domain(change.type, change.address)].append(change)
    affected_domains: list[dict[str, Any]] = []
    for domain, group in domain_groups.items():
        severity_counter = Counter(c.highest_severity for c in group if c.highest_severity)
        action_counter = Counter(c.action for c in group)
        examples = sorted({c.type for c in group})[:3]
        affected_domains.append(
            {
                "domain": domain,
                "label": _DOMAIN_TITLES.get(domain, domain.title()),
                "resources": len(group),
                "actions": {
                    "create": action_counter.get(ACTION_CREATE, 0),
                    "update": action_counter.get(ACTION_UPDATE, 0),
                    "delete": action_counter.get(ACTION_DELETE, 0),
                    "replace": action_counter.get(ACTION_REPLACE, 0),
                },
                "risks": {
                    "high": severity_counter.get("high", 0),
                    "medium": severity_counter.get("medium", 0),
                    "low": severity_counter.get("low", 0),
                },
                "examples": examples,
            }
        )
    affected_domains.sort(
        key=lambda item: (
            -item["risks"]["high"],
            -item["risks"]["medium"],
            -item["resources"],
            item["label"],
        )
    )

    module_groups: dict[str, list[ResourceChange]] = defaultdict(list)
    for change in meaningful:
        module_groups[change.module].append(change)
    module_hotspots: list[dict[str, Any]] = []
    for module, group in module_groups.items():
        severity_counter = Counter(c.highest_severity for c in group if c.highest_severity)
        replacements = sum(1 for c in group if c.action == ACTION_REPLACE)
        deletes = sum(1 for c in group if c.action == ACTION_DELETE)
        examples = sorted({c.type for c in group})[:3]
        module_hotspots.append(
            {
                "module": module,
                "resources": len(group),
                "high": severity_counter.get("high", 0),
                "medium": severity_counter.get("medium", 0),
                "replacements": replacements,
                "deletes": deletes,
                "examples": examples,
            }
        )
    module_hotspots.sort(
        key=lambda item: (
            -item["high"],
            -item["medium"],
            -(item["replacements"] + item["deletes"]),
            -item["resources"],
            item["module"],
        )
    )

    def _priority_key(change: ResourceChange) -> tuple[int, int, int, str]:
        severity_order = {"high": 0, "medium": 1, "low": 2, None: 3}
        destructive = 0 if change.action in (ACTION_REPLACE, ACTION_DELETE) else 1
        size_rank = -len(change.changed_attrs)
        return (
            severity_order.get(change.highest_severity, 3),
            destructive,
            size_rank,
            change.address,
        )

    priority_changes = []
    for change in sorted(meaningful, key=_priority_key)[:7]:
        top_risk = change.risks[0] if change.risks else None
        priority_changes.append(
            {
                "address": change.address,
                "action": change.action,
                "severity": change.highest_severity,
                "reason": top_risk["reason"] if top_risk else (_why_text(change) or "Targeted review recommended."),
                "module": change.module,
                "type": change.type,
            }
        )

    return {
        "headline": headline,
        "posture": posture,
        "reviewer_focus": reviewer_focus,
        "affected_domains": affected_domains,
        "module_hotspots": module_hotspots[:5],
        "priority_changes": priority_changes,
        "noise": {
            "tag_only": sum(1 for c in visible if c.tag_only),
            "auxiliary": sum(1 for c in visible if _is_auxiliary_change(c)),
            "ignored": sum(1 for c in changes if c.ignored),
            "meaningful": len(meaningful),
        },
    }


def parse_plan(plan: dict[str, Any], config: Config | None = None) -> PlanSummary:
    config = config or Config()
    rcs = plan.get("resource_changes") or []
    changes: list[ResourceChange] = []
    counter: Counter[str] = Counter()
    policy_records: list[dict[str, Any]] = []

    dep_graph = graph_mod.build_graph(plan)

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
        policy_record = policy_mod.analyze_change(rtype, addr, action, before, after)
        if policy_record and policy_record.get("effect_tightening"):
            risks.insert(
                0,
                {
                    "name": "policy-effect-tightening",
                    "severity": "high",
                    "reason": (
                        f"Policy effect tightened: "
                        f"{policy_record['effect_before']} -> {policy_record['effect_after']}."
                    ),
                },
            )
        if policy_record:
            policy_records.append(policy_record)
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
        downstream = sorted(dep_graph.blast_radius(addr))
        after_state = after if isinstance(after, dict) else {}
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
                blast_radius_score=len(downstream),
                downstream=downstream,
                after_state=after_state,
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
        admin_summary=_build_admin_summary(changes, stats, risks_by_severity),
        policy_changes=policy_records,
        compliance=compliance_mod.evaluate(changes, config.compliance),
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


def _action_mix(actions: dict[str, int]) -> str:
    parts = []
    for key in ("create", "update", "delete", "replace"):
        count = actions.get(key, 0)
        if count:
            parts.append(f"{count} {key}")
    return ", ".join(parts) if parts else "-"


def _risk_mix(risks: dict[str, int]) -> str:
    parts = []
    for key, label in (("high", "H"), ("medium", "M"), ("low", "L")):
        count = risks.get(key, 0)
        if count:
            parts.append(f"{count}{label}")
    return " / ".join(parts) if parts else "-"


def _render_admin_summary(summary: PlanSummary) -> list[str]:
    admin = summary.admin_summary
    if not admin:
        return []
    lines = ["## Executive summary", "", admin["headline"], "", f"_Posture: {admin['posture']}_", ""]

    lines.append("### Reviewer focus")
    lines.append("")
    for item in admin.get("reviewer_focus", []):
        lines.append(f"- {item}")
    lines.append("")

    priority = admin.get("priority_changes") or []
    if priority:
        lines.append("### Review first")
        lines.append("")
        for item in priority:
            sev = _severity_badge(item.get("severity"))
            prefix = f"{sev} " if sev else ""
            lines.append(
                f"- {prefix}`{item['address']}` ({item['action']}, `{item['type']}`) - {item['reason']}"
            )
        lines.append("")

    domains = admin.get("affected_domains") or []
    if domains:
        lines.append("### Impact by area")
        lines.append("")
        lines.append("| Area | Resources | Actions | Risks | Example types |")
        lines.append("| --- | ---: | --- | --- | --- |")
        for item in domains:
            examples = ", ".join(f"`{example}`" for example in item["examples"]) if item["examples"] else "-"
            lines.append(
                f"| {item['label']} | {item['resources']} | {_action_mix(item['actions'])} | {_risk_mix(item['risks'])} | {examples} |"
            )
        lines.append("")

    hotspots = admin.get("module_hotspots") or []
    if hotspots:
        lines.append("### Module hotspots")
        lines.append("")
        lines.append("| Module | Resources | Risks | Destructive | Example types |")
        lines.append("| --- | ---: | --- | --- | --- |")
        for item in hotspots:
            destructive = []
            if item["replacements"]:
                destructive.append(f"{item['replacements']} replace")
            if item["deletes"]:
                destructive.append(f"{item['deletes']} delete")
            destructive_text = ", ".join(destructive) if destructive else "-"
            examples = ", ".join(f"`{example}`" for example in item["examples"]) if item["examples"] else "-"
            lines.append(
                f"| `{item['module']}` | {item['resources']} | {_risk_mix({'high': item['high'], 'medium': item['medium'], 'low': 0})} | {destructive_text} | {examples} |"
            )
        lines.append("")
        tree = viz_mod.mermaid_module_tree(hotspots, title="Module map (top hotspots)")
        if tree:
            lines.append("#### Module map")
            lines.append("")
            lines.extend(tree)
            lines.append("")

    noise = admin.get("noise") or {}
    noise_bits = []
    if noise.get("tag_only"):
        noise_bits.append(f"{noise['tag_only']} tag-only")
    if noise.get("auxiliary"):
        noise_bits.append(f"{noise['auxiliary']} auxiliary")
    if noise.get("ignored"):
        noise_bits.append(f"{noise['ignored']} ignored by config")
    if noise_bits:
        lines.append("### Noise budget")
        lines.append("")
        lines.append("- Deprioritised below: " + ", ".join(noise_bits) + ".")
        lines.append("")

    return lines


def _render_blast_radius(summary: PlanSummary) -> list[str]:
    """Surface destructive operations with downstream impact.

    Highlights destroyed/replaced resources whose downstream dependency
    set is non-empty so reviewers see secondary impact at a glance.
    """
    destructive = [
        c
        for c in summary.changes
        if not c.ignored
        and c.action in (ACTION_REPLACE, ACTION_DELETE)
        and c.blast_radius_score > 0
    ]
    if not destructive:
        return []
    destructive.sort(key=lambda c: (-c.blast_radius_score, c.address))
    lines: list[str] = [
        "## Blast radius",
        "",
        f"_{len(destructive)} destructive operation(s) with downstream dependents._",
        "",
        "| Resource | Action | Downstream | Examples |",
        "| --- | --- | ---: | --- |",
    ]
    for c in destructive[:10]:
        examples = ", ".join(f"`{a}`" for a in c.downstream[:3])
        if len(c.downstream) > 3:
            examples += f" _(+{len(c.downstream) - 3} more)_"
        lines.append(
            f"| `{c.address}` | {c.action} | {c.blast_radius_score} | {examples} |"
        )
    if len(destructive) > 10:
        lines.append(f"| _… +{len(destructive) - 10} more_ | | | |")
    lines.append("")
    # Per-resource detail callouts.
    lines.append("> ⚠️ Replacing or deleting these resources affects the listed dependents. Verify rollout sequencing.")
    lines.append("")
    return lines


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
    trend_entries: list[dict[str, Any]] | None = None,
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

    dashboard_lines = viz_mod.render_dashboard(summary.to_dict())
    if dashboard_lines:
        lines.extend(dashboard_lines)

    lines.extend(_render_admin_summary(summary))

    # -- Blast radius -------------------------------------------------------
    lines.extend(_render_blast_radius(summary))

    # -- Policy changes -----------------------------------------------------
    policy_lines = policy_mod.render_section(summary.policy_changes)
    if policy_lines:
        lines.extend(policy_lines)

    # -- Compliance checks --------------------------------------------------
    compliance_lines = compliance_mod.render_section(summary.compliance)
    if compliance_lines:
        lines.extend(compliance_lines)

    # -- Trend (history) ----------------------------------------------------
    if trend_entries:
        trend_lines = history_mod.render_section(trend_entries)
        if trend_lines:
            lines.extend(trend_lines)

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
                f"({c.action}) - {top['reason']}{extra}"
            )
    lines.append("")

    # -- Suggested reviewers (routing) --------------------------------------
    if config and config.routing:
        suggestions = routing_mod.suggest(
            [c.__dict__ for c in visible],
            config.routing,
        )
        routing_lines = routing_mod.render_section(suggestions)
        if routing_lines:
            lines.extend(routing_lines)

    # -- Rollback playbook --------------------------------------------------
    rollback_plans = rollback_mod.plan_for_changes([c.__dict__ for c in visible])
    if rollback_plans:
        lines.extend(rollback_mod.render_section(rollback_plans))

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
                    lines.append(f"**List diff - `{k}`:**")
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
                    lines.append(f"**Rule diff - `{k}`:**")
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


def _load_or_append_history(
    history_path: str | None,
    summary: PlanSummary,
    cost: cost_mod.CostImpact | None,
) -> list[dict[str, Any]] | None:
    if not history_path:
        return None
    try:
        cost_diff = cost.diff_monthly if cost else None
        entry = history_mod.snapshot(summary.to_dict(), cost_diff=cost_diff)
        history_mod.append(history_path, entry)
        return history_mod.load(history_path)
    except OSError as e:
        print(f"warning: history update failed: {e}", file=sys.stderr)
        return None


def _write_exporters(args: argparse.Namespace, summary: PlanSummary) -> None:
    """Write optional multi-format exporter outputs. Failures warn, never crash."""
    from . import __version__ as _ver
    from .exporters import html as html_mod
    from .exporters import sarif as sarif_mod
    from .exporters import slack as slack_mod
    from .exporters import teams as teams_mod

    summary_dict = summary.to_dict()
    title = args.heading or "Terraform Plan Report"
    link = getattr(args, "report_link", None)

    if getattr(args, "html_out", None):
        try:
            with open(args.html_out, "w", encoding="utf-8") as f:
                f.write(html_mod.render(summary_dict, title=title))
        except OSError as e:
            print(f"warning: html export failed: {e}", file=sys.stderr)
    if getattr(args, "sarif_out", None):
        try:
            with open(args.sarif_out, "w", encoding="utf-8") as f:
                f.write(sarif_mod.render_str(summary_dict, version=_ver))
        except OSError as e:
            print(f"warning: sarif export failed: {e}", file=sys.stderr)
    if getattr(args, "teams_out", None):
        try:
            with open(args.teams_out, "w", encoding="utf-8") as f:
                f.write(teams_mod.render_str(summary_dict, title=title, link=link))
        except OSError as e:
            print(f"warning: teams export failed: {e}", file=sys.stderr)
    if getattr(args, "slack_out", None):
        try:
            with open(args.slack_out, "w", encoding="utf-8") as f:
                f.write(slack_mod.render_str(summary_dict, title=title, link=link))
        except OSError as e:
            print(f"warning: slack export failed: {e}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Summarise a Terraform plan JSON file.")
    p.add_argument("plan", help="Path to plan JSON, or '-' for stdin.")
    p.add_argument("--out", default="plan_summary.md", help="Output Markdown path.")
    p.add_argument("--json-out", default=None, help="Also write structured JSON summary here.")
    p.add_argument("--config", default=None, help="Path to .tfreport config; auto-discovered otherwise.")
    p.add_argument("--baseline", default=None, help="Previous plan_summary.json for delta.")
    p.add_argument("--cost-json", default=None, help="Infracost breakdown JSON.")
    p.add_argument("--heading", default=None, help="Optional H2 heading (multi-stack reports).")
    p.add_argument("--history", default=None, help="Append snapshot to this JSONL history file and render trend.")
    p.add_argument("--html-out", default=None, help="Also write a self-contained HTML report here.")
    p.add_argument("--sarif-out", default=None, help="Also write a SARIF 2.1.0 file (risks + compliance) here.")
    p.add_argument("--teams-out", default=None, help="Also write a Microsoft Teams Adaptive Card JSON here.")
    p.add_argument("--slack-out", default=None, help="Also write a Slack Block Kit JSON here.")
    p.add_argument("--report-link", default=None, help="Optional URL embedded into Teams/Slack payloads.")
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
        except Exception as e:  # narrative is optional - never fail the run
            print(f"warning: narrative generation failed: {e}", file=sys.stderr)

    md = render_markdown(
        summary,
        config=config,
        narrative=narrative,
        delta=delta,
        cost=cost,
        heading=args.heading,
        trend_entries=_load_or_append_history(args.history, summary, cost),
    )
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(md)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(summary.to_dict(), f, indent=2)

    _write_exporters(args, summary)

    print(f"wrote {args.out} ({summary.stats['total_changing']} changes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
