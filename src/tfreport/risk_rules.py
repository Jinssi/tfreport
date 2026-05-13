"""Risk classification rules for Terraform resource changes.

Each rule has:
- match: callable(resource_type: str) -> bool
- actions: set of action combos that trigger the rule
- severity: "high" | "medium" | "low"
- reason: short human-readable explanation

Rules are advisory only - they annotate the report but never fail the build.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Iterable


# Canonical action labels we use after normalising raw Terraform actions arrays.
ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"
ACTION_REPLACE = "replace"  # delete + create
ACTION_READ = "read"
ACTION_NOOP = "no-op"


@dataclass(frozen=True)
class RiskRule:
    name: str
    type_globs: tuple[str, ...]
    actions: frozenset[str]
    severity: str
    reason: str

    def matches(self, resource_type: str, action: str) -> bool:
        if action not in self.actions:
            return False
        return any(fnmatch.fnmatch(resource_type, g) for g in self.type_globs)


# Stateful resources where replace = data loss.
_STATEFUL_GLOBS = (
    "azurerm_storage_account",
    "azurerm_storage_*",
    "azurerm_sql_*",
    "azurerm_mssql_*",
    "azurerm_postgresql_*",
    "azurerm_mysql_*",
    "azurerm_mariadb_*",
    "azurerm_cosmosdb_*",
    "azurerm_redis_*",
    "azurerm_key_vault",
    "azurerm_key_vault_key",
    "azurerm_key_vault_secret",
    "azurerm_key_vault_certificate",
    "azurerm_managed_disk",
    "azurerm_recovery_services_*",
    "azurerm_backup_*",
    "azurerm_log_analytics_workspace",
)

# Identity / authorisation surface.
_IAM_GLOBS = (
    "azurerm_role_assignment",
    "azurerm_role_definition",
    "azurerm_user_assigned_identity",
    "azuread_*",
)

# Network surface.
_NETWORK_GLOBS = (
    "azurerm_virtual_network",
    "azurerm_subnet",
    "azurerm_network_security_*",
    "azurerm_firewall*",
    "azurerm_private_dns_*",
    "azurerm_private_endpoint",
    "azurerm_public_ip",
    "azurerm_route_table",
    "azurerm_route",
    "azurerm_virtual_network_peering",
    "azurerm_vpn_*",
    "azurerm_express_route_*",
)

# Policy / governance surface.
_POLICY_GLOBS = (
    "azurerm_policy_*",
    "azurerm_management_group*",
    "azurerm_subscription*",
    "azurerm_resource_policy_*",
)


RULES: tuple[RiskRule, ...] = (
    RiskRule(
        name="stateful-replace",
        type_globs=_STATEFUL_GLOBS,
        actions=frozenset({ACTION_REPLACE}),
        severity="high",
        reason="Replacement of a stateful resource will destroy data.",
    ),
    RiskRule(
        name="stateful-delete",
        type_globs=_STATEFUL_GLOBS,
        actions=frozenset({ACTION_DELETE}),
        severity="high",
        reason="Deleting a stateful resource is destructive and unrecoverable.",
    ),
    RiskRule(
        name="iam-change",
        type_globs=_IAM_GLOBS,
        actions=frozenset({ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE, ACTION_REPLACE}),
        severity="medium",
        reason="Identity/authorisation change - review blast radius.",
    ),
    RiskRule(
        name="network-replace-or-delete",
        type_globs=_NETWORK_GLOBS,
        actions=frozenset({ACTION_REPLACE, ACTION_DELETE}),
        severity="medium",
        reason="Network resource replace/delete may cause connectivity outage.",
    ),
    RiskRule(
        name="policy-change",
        type_globs=_POLICY_GLOBS,
        actions=frozenset({ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE, ACTION_REPLACE}),
        severity="medium",
        reason="Policy / management-group change affects governance scope.",
    ),
    RiskRule(
        name="any-replace",
        type_globs=("*",),
        actions=frozenset({ACTION_REPLACE}),
        severity="low",
        reason="Resource replacement (delete + create).",
    ),
    RiskRule(
        name="any-delete",
        type_globs=("*",),
        actions=frozenset({ACTION_DELETE}),
        severity="low",
        reason="Resource deletion.",
    ),
)


def classify(resource_type: str, action: str) -> list[RiskRule]:
    """Return all rules matching a (resource_type, action) pair, most-specific first."""
    return [r for r in RULES if r.matches(resource_type, action)]


def normalize_actions(actions: Iterable[str]) -> str:
    """Collapse a Terraform `change.actions` array into a single canonical action.

    Terraform represents replacements as ["delete", "create"] or ["create", "delete"].
    """
    a = list(actions)
    s = set(a)
    if s == {"create", "delete"}:
        return ACTION_REPLACE
    if a == ["no-op"]:
        return ACTION_NOOP
    if a == ["read"]:
        return ACTION_READ
    if a == ["create"]:
        return ACTION_CREATE
    if a == ["update"]:
        return ACTION_UPDATE
    if a == ["delete"]:
        return ACTION_DELETE
    # Fallback: join unknown sequences.
    return "+".join(a) if a else ACTION_NOOP
