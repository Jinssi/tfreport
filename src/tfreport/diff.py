"""Helpers for understanding *why* a resource is changing.

We extract:
- changed_attrs: top-level keys whose value differs between change.before and change.after
- attr_diffs:    same keys but with truncated/sensitive-masked before & after snippets
- replace_paths: when an action is "replace", Terraform records which paths forced it
- tag-only flag: changed_attrs is a subset of {"tags", "tags_all"} - usually noise
- module path:   parsed from the address so we can group the changes table
"""

from __future__ import annotations

import json
from typing import Any, Iterable


_TAG_KEYS = {"tags", "tags_all"}
_SENSITIVE_MARKER = "(sensitive value)"
_MAX_SNIPPET = 80
_SECRET_KEY_HINTS = (
    "password",
    "secret",
    "token",
    "key",
    "credential",
    "connection_string",
    "private_key",
    "sas",
)


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


def _is_sensitive_key(key: str) -> bool:
    k = key.lower()
    return any(hint in k for hint in _SECRET_KEY_HINTS)


def _snippet(value: Any, key: str) -> str:
    """Render a single value for a before/after snippet.

    - Sensitive keys are masked.
    - Terraform's own "(sensitive value)" sentinel is preserved.
    - Lists/dicts are JSON-encoded.
    - Long strings are truncated to _MAX_SNIPPET chars.
    """
    if value is None:
        return "_(unset)_"
    if isinstance(value, str) and _SENSITIVE_MARKER in value:
        return "_(sensitive)_"
    if _is_sensitive_key(key):
        return "_(sensitive)_"
    if isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    else:
        text = str(value)
    if len(text) > _MAX_SNIPPET:
        text = text[: _MAX_SNIPPET - 1] + "…"
    return f"`{text}`"


def attr_diffs(
    before: Any,
    after: Any,
    sensitive_before: Any = None,
    sensitive_after: Any = None,
) -> list[dict[str, str]]:
    """Return per-attribute before/after snippets for the keys that changed.

    Each entry is `{"key", "before", "after"}` already rendered as Markdown-safe
    strings (backticked, truncated, sensitive-masked).
    """
    if not isinstance(before, dict):
        before = {}
    if not isinstance(after, dict):
        after = {}
    sb = sensitive_before if isinstance(sensitive_before, dict) else {}
    sa = sensitive_after if isinstance(sensitive_after, dict) else {}
    out: list[dict[str, str]] = []
    for key in sorted(set(before) | set(after)):
        b = before.get(key)
        a = after.get(key)
        if b == a:
            continue
        is_sens = bool(sb.get(key) or sa.get(key)) or _is_sensitive_key(key)
        if is_sens:
            out.append({"key": key, "before": "_(sensitive)_", "after": "_(sensitive)_"})
        else:
            out.append({"key": key, "before": _snippet(b, key), "after": _snippet(a, key)})
    return out


# ---------------------------------------------------------------------------
# List-element diff
# ---------------------------------------------------------------------------


def list_element_diff(before: Any, after: Any) -> dict[str, list[str]] | None:
    """Return added / removed elements for two scalar lists.

    Returns None when either side is not a list of hashable scalars, or when
    nothing changed. The return shape is `{"added": [...], "removed": [...]}`
    with elements rendered as short strings (truncated).
    """
    if not isinstance(before, list) or not isinstance(after, list):
        return None
    try:
        b_set = list(before)
        a_set = list(after)
        # Sanity: only handle scalar elements (str / int / float / bool).
        if any(isinstance(x, (dict, list)) for x in b_set + a_set):
            return None
        before_seq = [str(x) for x in b_set]
        after_seq = [str(x) for x in a_set]
    except TypeError:
        return None
    added = [x for x in after_seq if x not in before_seq]
    removed = [x for x in before_seq if x not in after_seq]
    if not added and not removed:
        return None

    def _trunc(items: list[str]) -> list[str]:
        out: list[str] = []
        for it in items[:10]:
            t = it if len(it) <= _MAX_SNIPPET else it[: _MAX_SNIPPET - 1] + "…"
            out.append(t)
        if len(items) > 10:
            out.append(f"… +{len(items) - 10} more")
        return out

    return {"added": _trunc(added), "removed": _trunc(removed)}


# ---------------------------------------------------------------------------
# Keyed nested-block diff (NSG rules, firewall rules, routes, …)
# ---------------------------------------------------------------------------

# Attribute names whose value is a list of objects keyed by `name` (or the
# rule-ish key listed). Used to render rule-level diffs instead of dumping
# the whole blob as JSON.
KEYED_BLOCK_ATTRS: dict[str, str] = {
    # NSG-ish
    "security_rule": "name",
    "rule": "name",
    # Firewall policies
    "network_rule": "name",
    "application_rule": "name",
    "nat_rule": "name",
    "network_rule_collection": "name",
    "application_rule_collection": "name",
    "nat_rule_collection": "name",
    # Route tables
    "route": "name",
    # Key Vault / Storage / general
    "access_policy": "object_id",
    "ip_rule": "ip_range",
    "virtual_network_rule": "subnet_id",
    # Identity blocks
    "role_assignment": "principal_id",
}


def _key_of(block: dict[str, Any], key_field: str) -> str | None:
    if not isinstance(block, dict):
        return None
    v = block.get(key_field)
    if v is None:
        return None
    return str(v)


def keyed_block_diff(
    attr: str,
    before: Any,
    after: Any,
) -> dict[str, list[dict[str, Any]]] | None:
    """Diff a list-of-objects attribute (e.g. NSG `security_rule`) by key.

    Returns `None` if the attribute is not a known keyed block or values are
    malformed. Otherwise returns:

        {
          "added":   [{"key": ..., "block": {...}}],
          "removed": [{"key": ..., "block": {...}}],
          "changed": [{"key": ..., "attrs": [attr_diffs entries...]}],
        }
    """
    key_field = KEYED_BLOCK_ATTRS.get(attr)
    if key_field is None:
        return None
    if not isinstance(before, list) or not isinstance(after, list):
        return None
    b_by: dict[str, dict[str, Any]] = {}
    a_by: dict[str, dict[str, Any]] = {}
    for blk in before:
        k = _key_of(blk, key_field)
        if k is not None:
            b_by[k] = blk
    for blk in after:
        k = _key_of(blk, key_field)
        if k is not None:
            a_by[k] = blk

    if not b_by and not a_by:
        return None

    added_keys = sorted(set(a_by) - set(b_by))
    removed_keys = sorted(set(b_by) - set(a_by))
    common_keys = sorted(set(b_by) & set(a_by))

    added = [{"key": k, "block": a_by[k]} for k in added_keys]
    removed = [{"key": k, "block": b_by[k]} for k in removed_keys]
    changed: list[dict[str, Any]] = []
    for k in common_keys:
        if b_by[k] == a_by[k]:
            continue
        changed.append({"key": k, "attrs": attr_diffs(b_by[k], a_by[k])})

    if not added and not removed and not changed:
        return None
    return {"added": added, "removed": removed, "changed": changed}
