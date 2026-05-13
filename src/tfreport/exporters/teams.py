"""Microsoft Teams Adaptive Card exporter for plan summaries.

Output is suitable for posting to a Teams Incoming Webhook (or the Power
Automate "Post adaptive card in a chat" connector). Adaptive Cards v1.4.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

_THEME = {"high": "Attention", "medium": "Warning", "low": "Good"}


def render(summary: Mapping[str, Any], *, title: str = "Terraform Plan Report", link: str | None = None) -> dict[str, Any]:
    stats = summary.get("stats") or {}
    risks_by_sev = summary.get("risks_by_severity") or {}
    risks = summary.get("risks") or []
    compliance = summary.get("compliance") or {}

    facts = [
        {"title": "Create", "value": str(stats.get("create", 0))},
        {"title": "Update", "value": str(stats.get("update", 0))},
        {"title": "Delete", "value": str(stats.get("delete", 0))},
        {"title": "Replace", "value": str(stats.get("replace", 0))},
        {"title": "High risks", "value": str(risks_by_sev.get("high", 0))},
        {"title": "Medium risks", "value": str(risks_by_sev.get("medium", 0))},
    ]
    if compliance.get("enabled") and compliance.get("score") is not None:
        facts.append({"title": "Compliance", "value": f"{int(compliance['score'] * 100)}%"})

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "text": title,
        },
        {"type": "FactSet", "facts": facts},
    ]

    top_risks = [r for r in risks if (r.get("severity") or "").lower() == "high"][:5]
    if top_risks:
        body.append(
            {
                "type": "TextBlock",
                "weight": "Bolder",
                "text": "Top high-severity risks",
                "spacing": "Medium",
            }
        )
        for r in top_risks:
            body.append(
                {
                    "type": "TextBlock",
                    "wrap": True,
                    "text": f"• **{r.get('rule_id', '')}**: `{r.get('address', '')}`: {r.get('message', '')}",
                }
            )

    actions: list[dict[str, Any]] = []
    if link:
        actions.append({"type": "Action.OpenUrl", "title": "Open report", "url": link})

    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
    }
    if actions:
        card["actions"] = actions

    # Wrap in Teams webhook envelope.
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card,
            }
        ],
    }


def render_str(summary: Mapping[str, Any], **kwargs: Any) -> str:
    return json.dumps(render(summary, **kwargs), indent=2)
