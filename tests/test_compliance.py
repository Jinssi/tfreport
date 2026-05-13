"""Tests for compliance checks."""

from __future__ import annotations

import json
from pathlib import Path

from tfreport import plan as summarize_plan
from tfreport.config import Config

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        return json.load(f)


def test_no_config_disables_compliance():
    summary = summarize_plan.parse_plan(_load("compliance_fail.json"))
    assert summary.compliance["enabled"] is False
    md = summarize_plan.render_markdown(summary)
    assert "## Compliance checks" not in md


def test_required_tags_finding():
    cfg = Config()
    cfg.compliance = {"required_tags": ["owner", "cost-center"]}
    summary = summarize_plan.parse_plan(_load("compliance_fail.json"), config=cfg)
    findings = summary.compliance["findings"]
    bad = [f for f in findings if "bad" in f["address"]]
    assert bad and bad[0]["rule"] == "required_tags"


def test_allowed_regions_finding():
    cfg = Config()
    cfg.compliance = {"allowed_regions": ["westeurope", "northeurope"]}
    summary = summarize_plan.parse_plan(_load("compliance_fail.json"), config=cfg)
    findings = summary.compliance["findings"]
    assert any(f["rule"] == "allowed_regions" and "bad" in f["address"] for f in findings)


def test_no_public_network_finding():
    cfg = Config()
    cfg.compliance = {"no_public_network": True}
    summary = summarize_plan.parse_plan(_load("compliance_fail.json"), config=cfg)
    findings = summary.compliance["findings"]
    rules = {f["rule"] for f in findings if "bad" in f["address"]}
    assert "no_public_network" in rules


def test_naming_finding():
    cfg = Config()
    cfg.compliance = {"naming": {"azurerm_storage_account": "^st[a-z]+prod"}}
    summary = summarize_plan.parse_plan(_load("compliance_fail.json"), config=cfg)
    rules = {f["rule"] for f in summary.compliance["findings"]}
    assert "naming" in rules


def test_compliance_section_renders_with_score():
    cfg = Config()
    cfg.compliance = {"required_tags": ["owner"], "no_public_network": True}
    summary = summarize_plan.parse_plan(_load("compliance_fail.json"), config=cfg)
    md = summarize_plan.render_markdown(summary)
    assert "## Compliance checks" in md
    assert "Score" in md


def test_compliance_all_pass_shows_check_mark():
    cfg = Config()
    cfg.compliance = {"allowed_regions": ["westeurope"]}
    # Reduce fixture to only the "good" entry.
    plan = _load("compliance_fail.json")
    plan["resource_changes"] = [
        rc for rc in plan["resource_changes"] if rc["name"] == "good"
    ]
    summary = summarize_plan.parse_plan(plan, config=cfg)
    md = summarize_plan.render_markdown(summary)
    assert "All configured compliance checks passed" in md
