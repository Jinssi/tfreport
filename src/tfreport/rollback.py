"""Generate rollback recipes for destructive operations.

For each destroyed / replaced stateful resource, emit a short recipe of
pre-checks (backup verification, snapshot, export) and rollback steps
(re-apply previous module version, restore from backup, etc.).

This is intentionally heuristic — it surfaces *what to think about*
before approval, not a guaranteed automated rollback.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Map resource-type → (pre_checks, rollback_steps).
# Keys may use trailing wildcards via simple prefix matching in `_lookup`.
_RECIPES: dict[str, tuple[list[str], list[str]]] = {
    "azurerm_storage_account": (
        [
            "Confirm soft-delete and versioning are enabled for blobs.",
            "Trigger an on-demand `azcopy` snapshot of critical containers.",
            "Verify any private endpoints / network rules are documented.",
        ],
        [
            "Re-apply the previous module version with the same `name` to recover the resource.",
            "If the account is replaced (new name), restore data via `azcopy sync` from snapshot.",
            "Re-attach RBAC role assignments captured from the previous state file.",
        ],
    ),
    "azurerm_sql_database": (
        [
            "Verify automated backup retention covers the maintenance window.",
            "Take a point-in-time geo-backup or `azcopy` export of any reference data.",
        ],
        [
            "Restore from point-in-time backup to a new database name; CNAME swap.",
            "Re-grant DB-level role assignments and contained-user logins.",
        ],
    ),
    "azurerm_mssql_database": (
        [
            "Verify automated backup retention covers the maintenance window.",
            "Take a point-in-time geo-backup or BACPAC export of any reference data.",
        ],
        [
            "Restore from point-in-time backup to a new database name; CNAME swap.",
            "Re-grant DB-level role assignments and contained-user logins.",
        ],
    ),
    "azurerm_postgresql_flexible_server": (
        [
            "Verify automated backups & geo-redundant backup is enabled.",
            "Optionally take an on-demand `pg_dump` of small schemas.",
        ],
        [
            "Restore from automated backup to a new server, swap DNS.",
            "Re-create Entra ID admin and re-apply firewall rules.",
        ],
    ),
    "azurerm_key_vault": (
        [
            "Confirm soft-delete + purge protection are enabled.",
            "Export non-secret metadata (access policies / RBAC) for re-import.",
        ],
        [
            "Recover the soft-deleted vault: `az keyvault recover --name <name>`.",
            "Re-apply RBAC role assignments and access policies.",
        ],
    ),
    "azurerm_cosmosdb_account": (
        [
            "Verify continuous backup is enabled on the account.",
            "Document throughput / consistency settings.",
        ],
        [
            "Restore from continuous backup to a new account, then swap connection strings.",
        ],
    ),
    "azurerm_virtual_machine": (
        [
            "Take an OS-disk snapshot of the VM via `az snapshot create`.",
        ],
        [
            "Re-create the VM from the snapshot; re-attach data disks.",
        ],
    ),
    "azurerm_linux_virtual_machine": (
        [
            "Take an OS-disk snapshot via `az snapshot create`.",
            "Confirm the boot diagnostics storage is intact.",
        ],
        [
            "Re-create the VM from the snapshot; re-attach data disks and NIC.",
        ],
    ),
    "azurerm_windows_virtual_machine": (
        [
            "Take an OS-disk snapshot via `az snapshot create`.",
        ],
        [
            "Re-create the VM from the snapshot; re-attach data disks and NIC.",
        ],
    ),
    "azurerm_kubernetes_cluster": (
        [
            "Capture node pool config, network plugin, and add-ons in cluster docs.",
            "Verify workloads have GitOps / Helm manifests stored externally.",
        ],
        [
            "Re-create the cluster via the previous module version.",
            "Re-bootstrap workloads from GitOps or `helm upgrade --install` runs.",
        ],
    ),
}

_DEFAULT_STATEFUL_PREFIXES = (
    "azurerm_storage",
    "azurerm_sql",
    "azurerm_mssql",
    "azurerm_postgresql",
    "azurerm_mysql",
    "azurerm_mariadb",
    "azurerm_cosmosdb",
    "azurerm_key_vault",
    "azurerm_virtual_machine",
    "azurerm_linux_virtual_machine",
    "azurerm_windows_virtual_machine",
    "azurerm_kubernetes_cluster",
    "azurerm_redis_cache",
    "azurerm_eventhub_namespace",
    "azurerm_servicebus_namespace",
)


def _is_destructive(action: str) -> bool:
    return action in {"delete", "replace"}


def _is_stateful(rtype: str) -> bool:
    if rtype in _RECIPES:
        return True
    return any(rtype.startswith(p) for p in _DEFAULT_STATEFUL_PREFIXES)


def _lookup(rtype: str) -> tuple[list[str], list[str]]:
    if rtype in _RECIPES:
        return _RECIPES[rtype]
    return (
        ["Take a manual backup or snapshot of any stateful data attached to this resource."],
        ["Re-apply the previous module version to recreate the resource and restore data."],
    )


def plan_for_changes(changes: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return rollback plans for destructive stateful changes."""
    plans: list[dict[str, Any]] = []
    for c in changes:
        action = str(c.get("action") or "")
        if not _is_destructive(action):
            continue
        rtype = str(c.get("resource_type") or "")
        if not _is_stateful(rtype):
            continue
        pre, steps = _lookup(rtype)
        plans.append(
            {
                "address": str(c.get("address") or ""),
                "resource_type": rtype,
                "action": action,
                "pre_checks": pre,
                "rollback_steps": steps,
            }
        )
    return plans


def render_section(plans: list[dict[str, Any]]) -> list[str]:
    if not plans:
        return []
    lines = [
        "## Rollback playbook",
        "",
        f"_Generated for {len(plans)} destructive operation(s) on stateful resources. Review before approving._",
        "",
    ]
    for p in plans:
        lines.append(f"### `{p['address']}` ({p['action']})")
        lines.append("")
        lines.append("**Pre-checks:**")
        for s in p["pre_checks"]:
            lines.append(f"- {s}")
        lines.append("")
        lines.append("**Rollback steps:**")
        for s in p["rollback_steps"]:
            lines.append(f"- {s}")
        lines.append("")
    return lines
