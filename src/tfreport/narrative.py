"""Optional LLM narrative generator.

Backends:
  - github_models  (default; uses GitHub Models inference API)
  - azure_openai
  - none           (returns empty string)

We never feed raw Terraform plan JSON to the model — only the deterministic
summary dict produced by summarize_plan.parse_plan(). This keeps prompts small,
reproducible, and free of secrets that may leak through plan output.

Auth:
  - github_models: GITHUB_TOKEN (the workflow token; needs `models: read`).
  - azure_openai:  AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_DEPLOYMENT, plus either
                   AZURE_OPENAI_API_KEY or DefaultAzureCredential (OIDC).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


SYSTEM_PROMPT = (
    "You are a senior platform engineer reviewing a Terraform plan for an "
    "Azure Verified Modules (AVM) / Azure Landing Zone deployment. "
    "Given a structured JSON summary of the plan, write a concise narrative "
    "(max ~12 lines, plain Markdown, no headings) covering: "
    "(1) what is changing in business terms, "
    "(2) the most important risks the reviewer must check, "
    "(3) suggested manual checks before merge. "
    "Be specific to the resources listed. Do not invent resources. "
    "Do not repeat the stats table. Output only the narrative text."
)


def _user_prompt(summary: dict[str, Any]) -> str:
    return (
        "Plan summary JSON:\n```json\n"
        + json.dumps(summary, indent=2)[:12000]
        + "\n```"
    )


def _call_github_models(summary: dict[str, Any]) -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set; cannot call GitHub Models.")
    model = os.environ.get("GITHUB_MODELS_MODEL", "openai/gpt-4o-mini")
    endpoint = os.environ.get(
        "GITHUB_MODELS_ENDPOINT",
        "https://models.github.ai/inference/chat/completions",
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(summary)},
        ],
        "temperature": 0.2,
        "max_tokens": 600,
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"GitHub Models HTTP {e.code}: {detail}") from e
    return _extract_choice(payload)


def _call_azure_openai(summary: dict[str, Any]) -> str:
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
    if not endpoint or not deployment:
        raise RuntimeError(
            "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT must be set."
        )
    url = (
        f"{endpoint.rstrip('/')}/openai/deployments/{deployment}"
        f"/chat/completions?api-version={api_version}"
    )
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    if api_key:
        headers["api-key"] = api_key
    else:
        try:
            from azure.identity import DefaultAzureCredential

            cred = DefaultAzureCredential()
            token = cred.get_token("https://cognitiveservices.azure.com/.default").token
            headers["Authorization"] = f"Bearer {token}"
        except Exception as e:
            raise RuntimeError(
                "No AZURE_OPENAI_API_KEY and DefaultAzureCredential failed."
            ) from e
    body = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(summary)},
        ],
        "temperature": 0.2,
        "max_tokens": 600,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Azure OpenAI HTTP {e.code}: {detail}") from e
    return _extract_choice(payload)


def _extract_choice(payload: dict[str, Any]) -> str:
    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected LLM response shape: {payload!r}") from e
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("LLM returned empty content.")
    # Strip accidental leading headings (the prompt forbids them, but defend anyway).
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join(lines).strip()


def generate_narrative(summary: dict[str, Any], *, backend: str = "github_models") -> str:
    backend = (backend or "none").lower()
    if backend == "none":
        return ""
    if backend == "github_models":
        return _call_github_models(summary)
    if backend == "azure_openai":
        return _call_azure_openai(summary)
    raise ValueError(f"Unknown LLM backend: {backend}")
