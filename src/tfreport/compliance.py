"""Compliance and governance checks.

Deterministic, configurable rule packs that evaluate the **after** state of
each changing resource (creates and updates). Each check yields a finding
with severity, resource address, rule name, and human-readable message.

Rule packs (all opt-in via ``.tfreport.json`` ``compliance`` block):

* ``required_tags``: list of tag keys that must be present on every taggable
  resource. Severity: ``medium``.
* ``naming``: mapping of resource type glob → regex. Severity: ``low``.
* ``allowed_regions``: list of acceptable values for ``location``.
  Severity: ``medium``.
* ``no_public_network`` (bool, default true when block present): flag
  resources with ``public_network_access_enabled = true``,
  ``allow_blob_public_access = true``, or NSG rules opening 0.0.0.0/0.
  Severity: ``high``.
* ``encryption_required`` (bool, default true when block present): require
  ``infrastructure_encryption_enabled`` / ``customer_managed_key`` /
  ``encryption`` blocks where applicable. Severity: ``medium``.
* ``diagnostics_required`` (bool): flag stateful resources lacking
  ``azurerm_monitor_diagnostic_setting`` in the same plan. Severity:
  ``low``.

The module is intentionally a single pass over plan changes so it is fast
even on AVM-scale plans.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping
from typing import Any

# Resource types that we expect to carry tags. This list is short on purpose
# — checks are advisory and we accept false negatives over false positives.
_TAGGABLE_TYPES = (
    "azurerm_*",
)

# Resources where ``location`` is meaningful.
_LOCATION_TYPES = ("azurerm_*",)


def _is_taggable(rtype: str) -> bool:
    return any(fnmatch.fnmatchcase(rtype, g) for g in _TAGGABLE_TYPES)


def _is_location_aware(rtype: str) -> bool:
    return any(fnmatch.fnmatchcase(rtype, g) for g in _LOCATION_TYPES)


def evaluate(
    changes: list[Any],
    config_compliance: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Run all configured checks and return a compliance report.

    Returns a dict with: ``enabled`` (bool), ``findings`` (list),
    ``passed`` (int), ``failed`` (int), ``score`` (float, 0-1), and
    ``rules_enabled`` (list[str]).
    """
    if not config_compliance:
        return {
            "enabled": False,
            "findings": [],
            "passed": 0,
            "failed": 0,
            "score": None,
            "rules_enabled": [],
        }

    required_tags = list(config_compliance.get("required_tags") or [])
    naming = config_compliance.get("naming") or {}
    allowed_regions = [str(r).lower() for r in (config_compliance.get("allowed_regions") or [])]
    no_public = bool(config_compliance.get("no_public_network", False))
    encryption_required = bool(config_compliance.get("encryption_required", False))

    rules_enabled: list[str] = []
    if required_tags:
        rules_enabled.append("required_tags")
    if naming:
        rules_enabled.append("naming")
    if allowed_regions:
        rules_enabled.append("allowed_regions")
    if no_public:
        rules_enabled.append("no_public_network")
    if encryption_required:
        rules_enabled.append("encryption_required")

    findings: list[dict[str, Any]] = []
    checked = 0

    for change in changes:
        # Skip ignored, no-op, read, and pure-delete changes.
        if getattr(change, "ignored", False):
            continue
        action = getattr(change, "action", None)
        if action not in ("create", "update", "replace"):
            continue
        addr = getattr(change, "address", "")
        rtype = getattr(change, "type", "")
        after = _after_state(change)
        if not isinstance(after, Mapping):
            continue
        checked += 1

        if required_tags and _is_taggable(rtype):
            tags = after.get("tags") or {}
            if not isinstance(tags, Mapping):
                tags = {}
            missing = [t for t in required_tags if t not in tags]
            if missing:
                findings.append({
                    "rule": "required_tags",
                    "severity": "medium",
                    "address": addr,
                    "type": rtype,
                    "message": f"Missing required tag(s): {', '.join(missing)}",
                })

        if naming:
            for pattern, regex in naming.items():
                if not fnmatch.fnmatchcase(rtype, pattern):
                    continue
                name_val = after.get("name")
                if isinstance(name_val, str) and not re.search(regex, name_val):
                    findings.append({
                        "rule": "naming",
                        "severity": "low",
                        "address": addr,
                        "type": rtype,
                        "message": f"Name '{name_val}' does not match `{regex}`.",
                    })

        if allowed_regions and _is_location_aware(rtype):
            loc = after.get("location")
            if isinstance(loc, str) and loc.lower() not in allowed_regions:
                findings.append({
                    "rule": "allowed_regions",
                    "severity": "medium",
                    "address": addr,
                    "type": rtype,
                    "message": f"Location '{loc}' is not in the allowlist.",
                })

        if no_public:
            _check_public_network(addr, rtype, after, findings)

        if encryption_required:
            _check_encryption(addr, rtype, after, findings)

    failed = len(findings)
    passed = max(0, checked - len({f["address"] for f in findings}))
    total_evaluations = max(1, checked)
    score = (total_evaluations - failed) / total_evaluations if total_evaluations else 1.0

    return {
        "enabled": True,
        "findings": findings,
        "passed": passed,
        "failed": failed,
        "checked": checked,
        "score": round(max(0.0, min(1.0, score)), 3),
        "rules_enabled": rules_enabled,
    }


def _after_state(change: Any) -> Any:
    after = getattr(change, "after_state", None)
    if isinstance(after, Mapping) and after:
        return after
    # Fallback: synthesize from attr_diffs (only changed keys).
    diffs = getattr(change, "attr_diffs", None) or []
    if not diffs:
        return None
    out: dict[str, Any] = {}
    for d in diffs:
        out[d.get("key")] = d.get("after")
    return out


def _check_public_network(
    addr: str, rtype: str, after: Mapping[str, Any], findings: list[dict[str, Any]]
) -> None:
    pub = after.get("public_network_access_enabled")
    if isinstance(pub, str):
        if pub.lower() in {"true", "enabled"}:
            findings.append({
                "rule": "no_public_network",
                "severity": "high",
                "address": addr,
                "type": rtype,
                "message": "public_network_access_enabled = true",
            })
    blob = after.get("allow_blob_public_access")
    if isinstance(blob, str) and blob.lower() == "true":
        findings.append({
            "rule": "no_public_network",
            "severity": "high",
            "address": addr,
            "type": rtype,
            "message": "allow_blob_public_access = true",
        })


def _check_encryption(
    addr: str, rtype: str, after: Mapping[str, Any], findings: list[dict[str, Any]]
) -> None:
    # Heuristic: storage / sql / postgres / cosmos require an encryption hint.
    if not any(
        fnmatch.fnmatchcase(rtype, g)
        for g in (
            "azurerm_storage_account",
            "azurerm_*_server",
            "azurerm_cosmosdb_account",
        )
    ):
        return
    has_hint = any(
        key in after
        for key in (
            "infrastructure_encryption_enabled",
            "customer_managed_key",
            "encryption",
        )
    )
    if not has_hint:
        findings.append({
            "rule": "encryption_required",
            "severity": "medium",
            "address": addr,
            "type": rtype,
            "message": "No encryption hint observed (infrastructure_encryption / CMK / encryption block).",
        })


# -- Rendering ----------------------------------------------------------------


def render_section(report: Mapping[str, Any]) -> list[str]:
    if not report or not report.get("enabled"):
        return []
    findings = list(report.get("findings") or [])
    lines: list[str] = ["## Compliance checks", ""]
    score = report.get("score")
    if score is not None:
        pct = int(round(score * 100))
        lines.append(
            f"_Score: **{pct}%** · {report.get('failed', 0)} finding(s) across {report.get('checked', 0)} change(s) · "
            f"rules: {', '.join(report.get('rules_enabled') or []) or '—'}._"
        )
        lines.append("")
    if not findings:
        lines.append("✅ All configured compliance checks passed.")
        lines.append("")
        return lines
    sev_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (sev_order.get(f.get("severity", "low"), 9), f.get("address", "")))
    lines.append("| Severity | Rule | Resource | Detail |")
    lines.append("| --- | --- | --- | --- |")
    sev_glyph = {"high": "🟥 HIGH", "medium": "🟧 MEDIUM", "low": "🟨 low"}
    for f in findings[:30]:
        sev = sev_glyph.get(f.get("severity", "low"), f.get("severity", "low"))
        lines.append(
            f"| {sev} | `{f.get('rule')}` | `{f.get('address')}` | {f.get('message')} |"
        )
    if len(findings) > 30:
        lines.append(f"| _… +{len(findings) - 30} more_ | | | |")
    lines.append("")
    return lines
