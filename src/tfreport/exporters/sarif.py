"""SARIF 2.1.0 exporter for plan summary risks + compliance findings.

The output is suitable for upload to GitHub Code Scanning via
`github/codeql-action/upload-sarif@v3` or Azure DevOps SARIF tasks.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

_TOOL_NAME = "tfreport"
_TOOL_URI = "https://github.com/Jinssi/Terraformer"

_SEV_TO_SARIF = {"high": "error", "medium": "warning", "low": "note"}


def _rule_obj(rule_id: str, short: str, full: str | None = None) -> dict[str, Any]:
    return {
        "id": rule_id,
        "name": rule_id,
        "shortDescription": {"text": short[:140]},
        "fullDescription": {"text": (full or short)[:800]},
        "defaultConfiguration": {"level": "warning"},
    }


def _result(rule_id: str, level: str, message: str, address: str) -> dict[str, Any]:
    return {
        "ruleId": rule_id,
        "level": level,
        "message": {"text": message},
        "locations": [
            {
                "logicalLocations": [
                    {"name": address, "kind": "resource", "fullyQualifiedName": address}
                ],
                "physicalLocation": {
                    "artifactLocation": {"uri": "terraform-plan.json"}
                },
            }
        ],
    }


def render(summary: Mapping[str, Any], *, version: str = "0.0.0") -> dict[str, Any]:
    risks = summary.get("risks") or []
    compliance = summary.get("compliance") or {}
    findings = compliance.get("findings") or []

    rules_seen: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []

    for r in risks:
        rid = str(r.get("rule_id") or "risk")
        if rid not in rules_seen:
            rules_seen[rid] = _rule_obj(rid, f"Plan risk: {rid}")
        sev = str(r.get("severity") or "low").lower()
        results.append(
            _result(
                rid,
                _SEV_TO_SARIF.get(sev, "note"),
                str(r.get("message") or rid),
                str(r.get("address") or "(unknown)"),
            )
        )

    for f in findings:
        rid = f"compliance/{f.get('rule', 'compliance')}"
        if rid not in rules_seen:
            rules_seen[rid] = _rule_obj(rid, f"Compliance: {f.get('rule', '')}")
        sev = str(f.get("severity") or "medium").lower()
        results.append(
            _result(
                rid,
                _SEV_TO_SARIF.get(sev, "warning"),
                str(f.get("message") or rid),
                str(f.get("address") or "(unknown)"),
            )
        )

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": _TOOL_NAME,
                        "informationUri": _TOOL_URI,
                        "version": version,
                        "rules": list(rules_seen.values()),
                    }
                },
                "results": results,
            }
        ],
    }


def render_str(summary: Mapping[str, Any], *, version: str = "0.0.0") -> str:
    return json.dumps(render(summary, version=version), indent=2)
