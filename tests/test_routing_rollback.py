"""Tests for routing.py and rollback.py."""

from __future__ import annotations

from tfreport import rollback, routing


def test_routing_suggest_glob_and_type_glob():
    changes = [
        {"address": "module.network.azurerm_virtual_network.hub", "resource_type": "azurerm_virtual_network"},
        {"address": "module.app.azurerm_kubernetes_cluster.aks", "resource_type": "azurerm_kubernetes_cluster"},
        {"address": "azurerm_resource_group.rg", "resource_type": "azurerm_resource_group"},
    ]
    cfg = {
        "rules": [
            {"glob": "module.network.*", "reviewers": ["@team-netsec"]},
            {"type_glob": "azurerm_kubernetes_*", "reviewers": ["@team-platform"]},
            {"glob": "*", "reviewers": ["@team-cloud"]},
        ]
    }
    out = routing.suggest(changes, cfg)
    assert "@team-netsec" in out
    assert "@team-platform" in out
    assert "@team-cloud" in out
    assert len(out["@team-cloud"]) == 3
    assert out["@team-netsec"] == ["module.network.azurerm_virtual_network.hub"]


def test_routing_render_section_sorted_desc():
    md = "\n".join(routing.render_section({"@a": ["x", "y"], "@b": ["z"]}))
    assert "## Suggested reviewers" in md
    # @a should appear before @b (2 > 1).
    assert md.index("@a") < md.index("@b")


def test_routing_render_empty():
    assert routing.render_section({}) == []


def test_rollback_skips_creates_and_non_stateful():
    changes = [
        {"address": "azurerm_storage_account.x", "resource_type": "azurerm_storage_account", "action": "create"},
        {"address": "azurerm_resource_group.x", "resource_type": "azurerm_resource_group", "action": "delete"},
    ]
    assert rollback.plan_for_changes(changes) == []


def test_rollback_handles_destroy_and_replace_stateful():
    changes = [
        {"address": "azurerm_storage_account.s1", "resource_type": "azurerm_storage_account", "action": "delete"},
        {"address": "azurerm_postgresql_flexible_server.s", "resource_type": "azurerm_postgresql_flexible_server", "action": "replace"},
    ]
    plans = rollback.plan_for_changes(changes)
    assert len(plans) == 2
    assert plans[0]["pre_checks"]
    assert plans[1]["rollback_steps"]


def test_rollback_render_section():
    plans = [
        {
            "address": "azurerm_storage_account.s1",
            "resource_type": "azurerm_storage_account",
            "action": "delete",
            "pre_checks": ["check soft delete"],
            "rollback_steps": ["restore from snapshot"],
        }
    ]
    md = "\n".join(rollback.render_section(plans))
    assert "## Rollback playbook" in md
    assert "azurerm_storage_account.s1" in md
    assert "check soft delete" in md
    assert "restore from snapshot" in md


def test_rollback_unknown_type_uses_default():
    changes = [
        {"address": "azurerm_redis_cache.c", "resource_type": "azurerm_redis_cache", "action": "delete"},
    ]
    plans = rollback.plan_for_changes(changes)
    assert len(plans) == 1
    assert plans[0]["pre_checks"]
    assert plans[0]["rollback_steps"]
