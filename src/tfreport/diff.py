"""Helpers for understanding *why* a resource is changing.

We extract:
- changed_attrs: top-level keys whose value differs between change.before and change.after
- replace_paths: when an action is "replace", Terraform records which paths forced it
- tag-only flag: changed_attrs is a subset of {"tags", "tags_all"} — usually noise
- module path: parsed from the address so we can group the changes table
"""

from __future__ import annotations

from typing import Any, Iterable


_TAG_KEYS = {"tags", "tags_all"}


def changed_top_level_keys(before: Any, after: Any) -> list[str]:
    """Top-level keys whose value differs between before and after."""
    if not isinstance(before, dict):
        before = {}
    if not isinstance(after, dict):
        after = {}
    keys = set(before.keys()) | set(after.keys())
    return sorted(k for k in keys if before.get(k) != after.get(k))


def is_tag_only(changed_keys: Iterable[str]) -> bool:
    keys = list(changed_keys)
    if not keys:
        return False
    return all(k in _TAG_KEYS for k in keys)


def replace_paths(change: dict[str, Any]) -> list[str]:
    """Render Terraform's replace_paths into dotted strings."""
    raw = change.get("replace_paths") if isinstance(change, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for path in raw:
        if isinstance(path, list):
            out.append(".".join(str(p) for p in path))
        else:
            out.append(str(path))
    return out


def module_of(address: str) -> str:
    """Return the module path for an address, or '(root)' if it's at root."""
    if not address:
        return "(root)"
    parts = address.split(".")
    out: list[str] = []
    i = 0
    # Match repeated "module.<name>[index]" segments at the start.
    while i + 1 < len(parts) and parts[i] == "module":
        out.append(parts[i])
        out.append(parts[i + 1])
        i += 2
    return ".".join(out) if out else "(root)"
