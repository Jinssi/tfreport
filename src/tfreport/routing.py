"""Map planned changes to suggested reviewers (CODEOWNERS-style).

Routing rules live in `.tfreport.json` under the `routing` key:

    {
      "routing": {
        "rules": [
          {"glob": "module.network.*",         "reviewers": ["@team-netsec"]},
          {"type_glob": "azurerm_kubernetes_*", "reviewers": ["@team-platform"]},
          {"glob": "*",                         "reviewers": ["@team-cloud"]}
        ]
      }
    }

Matching:
- A rule matches a change if `glob` matches its address (fnmatch) AND/OR
  `type_glob` matches its resource_type.
- Rules are evaluated in order; ALL matching rules contribute reviewers
  (deduplicated, preserving first-occurrence order).
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Mapping
from typing import Any


def _match(rule: Mapping[str, Any], address: str, rtype: str) -> bool:
    glob = rule.get("glob")
    type_glob = rule.get("type_glob")
    if glob and not fnmatch.fnmatchcase(address, str(glob)):
        return False
    if type_glob and not fnmatch.fnmatchcase(rtype, str(type_glob)):
        return False
    return bool(glob or type_glob)


def suggest(changes: Iterable[Mapping[str, Any]], routing_cfg: Mapping[str, Any]) -> dict[str, list[str]]:
    """Return {reviewer: [addresses…]} aggregated across all changes."""
    rules = (routing_cfg or {}).get("rules") or []
    bucket: dict[str, list[str]] = {}
    for c in changes:
        addr = str(c.get("address") or "")
        rtype = str(c.get("resource_type") or "")
        seen: set[str] = set()
        for rule in rules:
            if not isinstance(rule, Mapping):
                continue
            if not _match(rule, addr, rtype):
                continue
            for rev in rule.get("reviewers") or []:
                rev_s = str(rev)
                if rev_s in seen:
                    continue
                seen.add(rev_s)
                bucket.setdefault(rev_s, []).append(addr)
    return bucket


def render_section(suggestions: Mapping[str, list[str]]) -> list[str]:
    if not suggestions:
        return []
    lines = ["## Suggested reviewers", ""]
    lines.append("| Reviewer | Resources |")
    lines.append("| --- | ---: |")
    # Sort by # of affected resources, descending.
    for reviewer, addrs in sorted(suggestions.items(), key=lambda kv: -len(kv[1])):
        sample = ", ".join(f"`{a}`" for a in addrs[:3])
        more = f" _(+{len(addrs) - 3} more)_" if len(addrs) > 3 else ""
        lines.append(f"| {reviewer} | {len(addrs)} ({sample}{more}) |")
    lines.append("")
    return lines
