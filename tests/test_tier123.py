"""Tests for Tier 1-3 features: config, modules, tag-only, replace_paths, delta, cost."""

from __future__ import annotations

import json
from pathlib import Path

from tfreport import plan as summarize_plan
from tfreport.config import Config
from tfreport import cost as cost_mod
from tfreport import delta as delta_mod
from tfreport import diff as diff_mod


FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_module_of():
    assert diff_mod.module_of("azurerm_resource_group.this") == "(root)"
    assert (
        diff_mod.module_of("module.networking.azurerm_virtual_network.hub")
        == "module.networking"
    )
    assert (
        diff_mod.module_of("module.a.module.b.azurerm_subnet.s") == "module.a.module.b"
    )


def test_changed_top_level_keys_and_tag_only():
    keys = diff_mod.changed_top_level_keys(
        {"name": "x", "tags": {"a": 1}}, {"name": "x", "tags": {"a": 2}}
    )
    assert keys == ["tags"]
    assert diff_mod.is_tag_only(keys)
    assert not diff_mod.is_tag_only(["tags", "address_space"])


def test_replace_paths_rendering():
    assert diff_mod.replace_paths({"replace_paths": [["a", "b"], ["c"]]}) == ["a.b", "c"]
    assert diff_mod.replace_paths({}) == []


def test_attr_diffs_basic_and_truncation():
    diffs = diff_mod.attr_diffs(
        {"name": "old", "size": 5, "tags": {"env": "dev"}},
        {"name": "new", "size": 5, "tags": {"env": "prod"}, "added": True},
    )
    keys = [d["key"] for d in diffs]
    assert "name" in keys and "tags" in keys and "added" in keys
    assert "size" not in keys  # unchanged
    name_diff = next(d for d in diffs if d["key"] == "name")
    assert "old" in name_diff["before"] and "new" in name_diff["after"]
    added_diff = next(d for d in diffs if d["key"] == "added")
    assert "unset" in added_diff["before"]
    # truncation
    long_diffs = diff_mod.attr_diffs({"x": "a" * 200}, {"x": "b" * 200})
    assert "…" in long_diffs[0]["before"] and "…" in long_diffs[0]["after"]


def test_attr_diffs_sensitive_masking():
    # By key name
    d = diff_mod.attr_diffs({"admin_password": "oldpw"}, {"admin_password": "newpw"})
    assert d[0]["before"] == "_(sensitive)_" and d[0]["after"] == "_(sensitive)_"
    # By Terraform sensitive map
    d = diff_mod.attr_diffs(
        {"value": "before"},
        {"value": "after"},
        sensitive_before={"value": True},
        sensitive_after={"value": True},
    )
    assert d[0]["before"] == "_(sensitive)_"


def test_config_ignore_globs():
    cfg = Config(ignore=["module.legacy.*", "azurerm_role_assignment.*"])
    assert cfg.is_ignored("module.legacy.azurerm_subnet.s")
    assert cfg.is_ignored("azurerm_role_assignment.reader")
    assert not cfg.is_ignored("azurerm_storage_account.data")


def test_parse_plan_with_modules_and_tags():
    cfg = Config()
    summary = summarize_plan.parse_plan(_load("with_modules.json"), config=cfg)
    by_addr = {c.address: c for c in summary.changes}
    vnet = by_addr["module.networking.azurerm_virtual_network.hub"]
    assert vnet.module == "module.networking"
    assert vnet.tag_only is True
    assert "tags" in vnet.changed_attrs

    rg = by_addr["azurerm_resource_group.this"]
    assert rg.module == "(root)"
    assert rg.tag_only is True

    subnet = by_addr["module.networking.azurerm_subnet.app"]
    assert subnet.action == "replace"
    assert subnet.replace_paths == ["address_prefixes"]
    assert subnet.tag_only is False


def test_render_groups_by_module_and_collapses_tag_only():
    cfg = Config(group_by_module=True, demote_tag_only=True)
    summary = summarize_plan.parse_plan(_load("with_modules.json"), config=cfg)
    md = summarize_plan.render_markdown(summary, config=cfg)
    assert "### module.networking" in md or "### (root)" in md
    assert "Tag-only updates" in md
    # Tag-only details collapsed
    assert "<details><summary>Tag-only updates" in md


def test_render_ignored_collapsed():
    cfg = Config(ignore=["module.networking.azurerm_subnet.*"])
    summary = summarize_plan.parse_plan(_load("with_modules.json"), config=cfg)
    md = summarize_plan.render_markdown(summary, config=cfg)
    assert "Ignored by config" in md


def test_delta_compute_detects_new_high_risk():
    base = {
        "stats": {"create": 0, "update": 0, "delete": 0, "replace": 0, "total_changing": 0},
        "changes": [],
    }
    cur = {
        "stats": {"create": 0, "update": 0, "delete": 0, "replace": 1, "total_changing": 1},
        "changes": [
            {
                "address": "azurerm_storage_account.data",
                "risks": [{"severity": "high", "name": "x", "reason": "y"}],
            }
        ],
    }
    d = delta_mod.compute(cur, base, baseline_path="b.json")
    assert "azurerm_storage_account.data" in d.new_high_risk
    assert "azurerm_storage_account.data" in d.new_addresses
    assert d.stat_diff.get("replace") == 1
    assert not d.is_empty()


def test_cost_parse_and_render():
    data = {
        "currency": "EUR",
        "pastTotalMonthlyCost": "10.00",
        "totalMonthlyCost": "15.50",
        "diffTotalMonthlyCost": "5.50",
        "projects": [
            {
                "diff": {
                    "resources": [
                        {"name": "azurerm_storage_account.data", "monthlyCost": "5.50"},
                        {"name": "noise", "monthlyCost": "0"},
                    ]
                }
            }
        ],
    }
    impact = cost_mod.parse(data)
    assert impact.currency == "EUR"
    assert impact.has_impact
    assert len(impact.top_resources) == 1
    section = cost_mod.render_section(impact)
    assert "EUR" in section
    assert "azurerm_storage_account.data" in section


def test_main_with_baseline_and_cost(tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "stats": {"create": 0, "update": 0, "delete": 0, "replace": 0, "total_changing": 0},
                "changes": [],
            }
        ),
        encoding="utf-8",
    )
    infracost = tmp_path / "infracost.json"
    infracost.write_text(
        json.dumps(
            {"currency": "USD", "totalMonthlyCost": "1.0", "diffTotalMonthlyCost": "1.0"}
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out.md"
    rc = summarize_plan.main(
        [
            str(FIXTURES / "mixed.json"),
            "--out",
            str(out),
            "--baseline",
            str(baseline),
            "--cost-json",
            str(infracost),
        ]
    )
    assert rc == 0
    md = out.read_text(encoding="utf-8")
    assert "Compared to baseline" in md
    assert "Estimated cost change" in md
