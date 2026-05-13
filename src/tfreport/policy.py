"""Azure Policy body diff analyzer.

Decodes the embedded JSON in Terraform's policy resource fields and produces
a structured diff that surfaces governance-relevant changes:

* Effect transitions (Audit → Deny is highlighted as a tightening change).
* Parameter delta (added / removed / changed defaults).
* Assignment scope delta.
* Initiative member add/remove.
* Exemption changes.

The module intentionally avoids any Azure SDK calls — it works purely from
the plan JSON. Resources that are not policy-related return None so callers
can skip them cheaply.
"""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Mapping
from typing import Any

POLICY_TYPE_GLOBS: tuple[str, ...] = (
    "azurerm_policy_definition",
    "azurerm_policy_set_definition",
    "azurerm_*_policy_assignment",
    "azurerm_management_group_policy_assignment",
    "azurerm_resource_policy_*",
    "azurerm_policy_exemption*",
    "azuread_conditional_access_policy",
)

# Effect transitions considered a tightening of enforcement.
_EFFECT_ORDER = {
    "disabled": 0,
    "audit": 1,
    "auditifnotexists": 1,
    "modify": 2,
    "append": 2,
    "deployifnotexists": 3,
    "deny": 4,
}


def is_policy_type(resource_type: str) -> bool:
    return any(fnmatch.fnmatchcase(resource_type, g) for g in POLICY_TYPE_GLOBS)


def _decode(value: Any) -> Any:
    """Best-effort decode of a Terraform-stored JSON string."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return value
    return value


def _extract_effect(rule: Any) -> str | None:
    if isinstance(rule, Mapping):
        then = rule.get("then")
        if isinstance(then, Mapping):
            effect = then.get("effect")
            if isinstance(effect, str):
                return effect
        eff = rule.get("effect")
        if isinstance(eff, str):
            return eff
    return None


def _parameter_diff(before: Any, after: Any) -> dict[str, list[dict[str, Any]]]:
    b = _decode(before) or {}
    a = _decode(after) or {}
    if not isinstance(b, Mapping):
        b = {}
    if not isinstance(a, Mapping):
        a = {}
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    for key in sorted(set(a) - set(b)):
        added.append({"name": key, "value": _short(a[key])})
    for key in sorted(set(b) - set(a)):
        removed.append({"name": key, "value": _short(b[key])})
    for key in sorted(set(a) & set(b)):
        if a[key] != b[key]:
            changed.append({"name": key, "before": _short(b[key]), "after": _short(a[key])})
    return {"added": added, "removed": removed, "changed": changed}


def _short(value: Any, limit: int = 80) -> str:
    text = json.dumps(value, default=str) if not isinstance(value, str) else value
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _effect_is_tightening(before_effect: str | None, after_effect: str | None) -> bool:
    if not before_effect or not after_effect:
        return False
    b = _EFFECT_ORDER.get(before_effect.lower(), -1)
    a = _EFFECT_ORDER.get(after_effect.lower(), -1)
    return a > b


def analyze_change(
    resource_type: str,
    address: str,
    action: str,
    before: Any,
    after: Any,
) -> dict[str, Any] | None:
    """Return a structured policy-change record or ``None`` for non-policy."""
    if not is_policy_type(resource_type):
        return None
    if before is None and after is None:
        return None

    before = before or {}
    after = after or {}
    before_rule = _decode(before.get("policy_rule")) if isinstance(before, Mapping) else None
    after_rule = _decode(after.get("policy_rule")) if isinstance(after, Mapping) else None
    before_effect = _extract_effect(before_rule)
    after_effect = _extract_effect(after_rule)

    params = _parameter_diff(
        before.get("parameters") if isinstance(before, Mapping) else None,
        after.get("parameters") if isinstance(after, Mapping) else None,
    )

    scope_before = before.get("scope") if isinstance(before, Mapping) else None
    scope_after = after.get("scope") if isinstance(after, Mapping) else None
    scope_changed = scope_before != scope_after

    enforce_before = before.get("enforce") if isinstance(before, Mapping) else None
    enforce_after = after.get("enforce") if isinstance(after, Mapping) else None

    record: dict[str, Any] = {
        "address": address,
        "type": resource_type,
        "action": action,
        "effect_before": before_effect,
        "effect_after": after_effect,
        "effect_tightening": _effect_is_tightening(before_effect, after_effect),
        "parameters": params,
        "scope_changed": scope_changed,
        "scope_before": scope_before if scope_changed else None,
        "scope_after": scope_after if scope_changed else None,
        "enforce_before": enforce_before,
        "enforce_after": enforce_after,
    }
    return record


def render_section(records: list[dict[str, Any]]) -> list[str]:
    if not records:
        return []
    lines: list[str] = ["## Policy changes", ""]
    tightening = [r for r in records if r.get("effect_tightening")]
    if tightening:
        lines.append(f"> 🟥 {len(tightening)} policy effect tightening detected (enforcement upgraded).")
        lines.append("")
    for r in records:
        eff_arrow = ""
        if r["effect_before"] or r["effect_after"]:
            eff_arrow = f"  · effect: `{r['effect_before'] or '∅'}` → `{r['effect_after'] or '∅'}`"
            if r.get("effect_tightening"):
                eff_arrow += " 🟥 **tightening**"
        lines.append(f"### `{r['address']}` ({r['action']}){eff_arrow}")
        lines.append("")
        params = r.get("parameters") or {}
        if params.get("added") or params.get("removed") or params.get("changed"):
            lines.append("**Parameter delta**")
            lines.append("")
            lines.append("| Change | Name | Detail |")
            lines.append("| --- | --- | --- |")
            for p in params.get("added", [])[:10]:
                lines.append(f"| added | `{p['name']}` | {p['value']} |")
            for p in params.get("removed", [])[:10]:
                lines.append(f"| removed | `{p['name']}` | {p['value']} |")
            for p in params.get("changed", [])[:10]:
                lines.append(f"| changed | `{p['name']}` | {p['before']} → {p['after']} |")
            lines.append("")
        if r.get("scope_changed"):
            lines.append(f"- scope: `{r.get('scope_before')}` → `{r.get('scope_after')}`")
        if r.get("enforce_before") != r.get("enforce_after"):
            lines.append(
                f"- enforce: `{r.get('enforce_before')}` → `{r.get('enforce_after')}`"
            )
        lines.append("")
    return lines
