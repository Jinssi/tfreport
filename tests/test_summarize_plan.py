"""Tests for summarize_plan and risk classification."""

from __future__ import annotations

import json
from pathlib import Path


from tfreport import risk_rules as _risk_rules
from tfreport import plan as summarize_plan


FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        return json.load(f)


def test_normalize_actions():
    n = _risk_rules.normalize_actions
    assert n(["create"]) == "create"
    assert n(["update"]) == "update"
    assert n(["delete"]) == "delete"
    assert n(["delete", "create"]) == "replace"
    assert n(["create", "delete"]) == "replace"
    assert n(["no-op"]) == "no-op"
    assert n(["read"]) == "read"
    assert n([]) == "no-op"


def test_noop_plan_has_no_changes():
    summary = summarize_plan.parse_plan(_load("noop.json"))
    assert summary.stats["total_changing"] == 0
    assert summary.changes == []
    assert summary.risks_by_severity == {"high": 0, "medium": 0, "low": 0}


def test_mixed_plan_counts():
    summary = summarize_plan.parse_plan(_load("mixed.json"))
    s = summary.stats
    assert s["create"] == 1
    assert s["update"] == 1
    assert s["delete"] == 2
    assert s["replace"] == 1
    assert s["total_changing"] == 5


def test_mixed_plan_risks():
    summary = summarize_plan.parse_plan(_load("mixed.json"))
    risky = {c.address: c for c in summary.changes if c.risks}
    # Storage account replace -> stateful-replace (high)
    assert "azurerm_storage_account.data" in risky
    assert risky["azurerm_storage_account.data"].highest_severity == "high"
    # Role assignment delete -> iam-change (medium)
    assert "azurerm_role_assignment.reader" in risky
    assert risky["azurerm_role_assignment.reader"].highest_severity == "medium"
    # Subnet delete -> network-replace-or-delete (medium)
    assert "azurerm_subnet.legacy" in risky
    assert risky["azurerm_subnet.legacy"].highest_severity == "medium"


def test_destroys_plan_all_high():
    summary = summarize_plan.parse_plan(_load("destroys.json"))
    severities = {c.address: c.highest_severity for c in summary.changes}
    assert severities["azurerm_key_vault.main"] == "high"
    assert severities["azurerm_postgresql_flexible_server.db"] == "high"
    assert summary.risks_by_severity["high"] >= 2


def test_render_markdown_shape():
    summary = summarize_plan.parse_plan(_load("mixed.json"))
    md = summarize_plan.render_markdown(summary)
    assert md.startswith("# Terraform Plan Summary")
    assert "## Executive summary" in md
    assert "### Reviewer focus" in md
    assert "### Review first" in md
    assert "### Impact by area" in md
    assert "## Stats" in md
    assert "## Changes" in md
    assert "## Risks" in md
    assert md.index("## Executive summary") < md.index("## Stats")
    # Replace appears before delete in the changes table.
    assert md.index("`+/-`") < md.index("`-`")
    assert "azurerm_storage_account.data" in md
    # Advisory marker present.
    assert "advisory" in md.lower()


def test_admin_summary_json_shape():
    summary = summarize_plan.parse_plan(_load("mixed.json"))
    data = summary.to_dict()
    admin = data["admin_summary"]
    assert "headline" in admin
    assert "reviewer_focus" in admin
    assert "affected_domains" in admin
    assert admin["priority_changes"][0]["address"] == "azurerm_storage_account.data"


def test_render_markdown_noop():
    summary = summarize_plan.parse_plan(_load("noop.json"))
    md = summarize_plan.render_markdown(summary)
    assert "_No changing resources._" in md
    assert "_No risks flagged._" in md


def test_main_writes_outputs(tmp_path: Path):
    out_md = tmp_path / "out.md"
    out_json = tmp_path / "out.json"
    rc = summarize_plan.main(
        [
            str(FIXTURES / "mixed.json"),
            "--out",
            str(out_md),
            "--json-out",
            str(out_json),
        ]
    )
    assert rc == 0
    assert out_md.exists() and out_md.stat().st_size > 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["stats"]["total_changing"] == 5


def test_main_returns_2_on_bad_json(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    rc = summarize_plan.main([str(bad), "--out", str(tmp_path / "out.md")])
    assert rc == 2
