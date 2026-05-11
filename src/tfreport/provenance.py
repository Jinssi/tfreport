"""Gather provenance/runtime metadata for the report footer + history JSONL.

Captured fields (best-effort, all optional):
- timestamp_utc
- tfreport_version
- terraform_version, provider_versions  (from plan JSON)
- format_version                         (from plan JSON; warns on unknown)
- ci.platform, ci.run_id, ci.run_url, ci.commit, ci.actor, ci.ref, ci.repo, ci.workflow
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any


_KNOWN_PLAN_FORMAT_VERSIONS = {"1.0", "1.1", "1.2"}


def _provider_versions(plan: dict[str, Any]) -> dict[str, str]:
    cfg = plan.get("configuration") or {}
    pcfg = cfg.get("provider_config") or {}
    out: dict[str, str] = {}
    if isinstance(pcfg, dict):
        for key, val in pcfg.items():
            if not isinstance(val, dict):
                continue
            ver = val.get("version_constraint") or val.get("version")
            if ver:
                out[str(key)] = str(ver)
    # Also scan top-level configuration.root_module if present
    return out


def _ci_metadata() -> dict[str, str]:
    e = os.environ
    if e.get("GITHUB_ACTIONS") == "true":
        repo = e.get("GITHUB_REPOSITORY", "")
        run_id = e.get("GITHUB_RUN_ID", "")
        server = e.get("GITHUB_SERVER_URL", "https://github.com")
        run_url = f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else ""
        return {
            "platform": "github-actions",
            "repo": repo,
            "ref": e.get("GITHUB_REF", ""),
            "commit": e.get("GITHUB_SHA", "")[:12],
            "run_id": run_id,
            "run_url": run_url,
            "actor": e.get("GITHUB_ACTOR", ""),
            "workflow": e.get("GITHUB_WORKFLOW", ""),
        }
    if e.get("TF_BUILD") == "True" or e.get("BUILD_BUILDID"):
        return {
            "platform": "azure-devops",
            "repo": e.get("BUILD_REPOSITORY_NAME", ""),
            "ref": e.get("BUILD_SOURCEBRANCH", ""),
            "commit": (e.get("BUILD_SOURCEVERSION", "") or "")[:12],
            "run_id": e.get("BUILD_BUILDID", ""),
            "run_url": (
                f"{e.get('SYSTEM_TEAMFOUNDATIONCOLLECTIONURI', '')}"
                f"{e.get('SYSTEM_TEAMPROJECT', '')}/_build/results?buildId={e.get('BUILD_BUILDID', '')}"
                if e.get("BUILD_BUILDID") else ""
            ),
            "actor": e.get("BUILD_REQUESTEDFOR", ""),
            "workflow": e.get("BUILD_DEFINITIONNAME", ""),
        }
    if e.get("GITLAB_CI") == "true":
        return {
            "platform": "gitlab-ci",
            "repo": e.get("CI_PROJECT_PATH", ""),
            "ref": e.get("CI_COMMIT_REF_NAME", ""),
            "commit": (e.get("CI_COMMIT_SHA", "") or "")[:12],
            "run_id": e.get("CI_PIPELINE_ID", ""),
            "run_url": e.get("CI_PIPELINE_URL", ""),
            "actor": e.get("GITLAB_USER_LOGIN", ""),
            "workflow": e.get("CI_JOB_NAME", ""),
        }
    return {"platform": "local"}


def gather(plan: dict[str, Any] | None = None) -> dict[str, Any]:
    from . import __version__

    plan = plan or {}
    fmt = plan.get("format_version", "")
    if fmt and fmt not in _KNOWN_PLAN_FORMAT_VERSIONS:
        print(
            f"warning: plan format_version={fmt!r} is not in known set "
            f"{sorted(_KNOWN_PLAN_FORMAT_VERSIONS)}; report may be inaccurate.",
            file=sys.stderr,
        )

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tfreport_version": __version__,
        "terraform_version": plan.get("terraform_version", ""),
        "format_version": fmt,
        "provider_versions": _provider_versions(plan),
        "ci": _ci_metadata(),
    }


def render_footer(meta: dict[str, Any]) -> str:
    """One-line provenance footer for the report."""
    ci = meta.get("ci", {})
    bits: list[str] = []
    if meta.get("terraform_version"):
        bits.append(f"Terraform {meta['terraform_version']}")
    if meta.get("tfreport_version"):
        bits.append(f"tfreport {meta['tfreport_version']}")
    if ci.get("commit"):
        commit = ci["commit"]
        if ci.get("run_url"):
            bits.append(f"commit `{commit}` · [run]({ci['run_url']})")
        else:
            bits.append(f"commit `{commit}`")
    elif ci.get("run_url"):
        bits.append(f"[run]({ci['run_url']})")
    if ci.get("actor"):
        bits.append(f"by @{ci['actor']}")
    if meta.get("timestamp_utc"):
        bits.append(meta["timestamp_utc"])
    return " · ".join(bits)
