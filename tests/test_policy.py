"""Tests for the policy diff analyzer."""

from __future__ import annotations

import json
from pathlib import Path

from tfreport import plan as summarize_plan
from tfreport import policy

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        return json.load(f)


def test_is_policy_type():
    assert policy.is_policy_type("azurerm_policy_definition")
    assert policy.is_policy_type("azurerm_resource_group_policy_assignment")
    assert policy.is_policy_type("azuread_conditional_access_policy")
    assert not policy.is_policy_type("azurerm_virtual_network")


def test_effect_tightening_audit_to_deny():
    before = {
        "policy_rule": '{"if":{},"then":{"effect":"Audit"}}',
        "parameters": "{}",
    }
    after = {
        "policy_rule": '{"if":{},"then":{"effect":"Deny"}}',
        "parameters": "{}",
    }
    rec = policy.analyze_change("azurerm_policy_definition", "x.y", "update", before, after)
    assert rec is not None
    assert rec["effect_before"].lower() == "audit"
    assert rec["effect_after"].lower() == "deny"
    assert rec["effect_tightening"] is True


def test_parameter_delta():
    rec = policy.analyze_change(
        "azurerm_policy_definition",
        "x.y",
        "update",
        {"parameters": '{"a":1}'},
        {"parameters": '{"a":2,"b":3}'},
    )
    assert rec is not None
    params = rec["parameters"]
    names_changed = [p["name"] for p in params["changed"]]
    names_added = [p["name"] for p in params["added"]]
    assert "a" in names_changed
    assert "b" in names_added


def test_non_policy_returns_none():
    rec = policy.analyze_change(
        "azurerm_virtual_network", "x.y", "update", {"foo": 1}, {"foo": 2}
    )
    assert rec is None


def test_policy_fixture_renders_section_with_tightening_badge():
    summary = summarize_plan.parse_plan(_load("policy.json"))
    md = summarize_plan.render_markdown(summary)
    assert "## Policy changes" in md
    assert "tightening" in md.lower()
    assert "azurerm_policy_definition.deny_public_ip" in md


def test_policy_tightening_bumps_severity():
    summary = summarize_plan.parse_plan(_load("policy.json"))
    by_addr = {c.address: c for c in summary.changes}
    deny = by_addr["azurerm_policy_definition.deny_public_ip"]
    assert deny.highest_severity == "high"


def test_scope_change_rendered():
    summary = summarize_plan.parse_plan(_load("policy.json"))
    md = summarize_plan.render_markdown(summary)
    assert "scope" in md.lower()
    assert "rg-prod" in md


def test_to_dict_exposes_policy_changes():
    summary = summarize_plan.parse_plan(_load("policy.json"))
    data = summary.to_dict()
    assert "policy_changes" in data
    assert len(data["policy_changes"]) == 2
