# tfreport

[![CI](https://github.com/Jinssi/tfreport/actions/workflows/ci.yml/badge.svg)](https://github.com/Jinssi/tfreport/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/tfreport.svg)](https://pypi.org/project/tfreport/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Comprehensive** Terraform plan/apply/drift report generator. Turns `terraform show -json` and `terraform apply` output into a visualization-rich Markdown report — dashboards, dependency-graph blast-radius, policy-body diffs, compliance checks, cost charts, run-to-run trends, rollback playbooks — with optional HTML / SARIF / Teams / Slack exporters. Advisory by design: reports never fail your build.

Designed for Azure / AVM / ALZ environments but works on any provider.

## Three ways to use it

| Shape | Best for | Install |
| --- | --- | --- |
| **GitHub reusable workflow** | GitHub-Actions repos that want the full pipeline | `uses: Jinssi/tfreport/.github/workflows/terraform-plan.yml@v1` |
| **GitHub composite action** | GHA users with their own terraform steps | `uses: Jinssi/tfreport@v1` |
| **PyPI package (`tfreport`)** | Any other CI (Azure DevOps, GitLab, Jenkins, local) | `pip install tfreport` |

All three call the same engine.

## Quickstart - local

```bash
pip install tfreport
terraform plan -out=tfplan
terraform show -json tfplan > plan.json
tf-report-plan plan.json --out plan_summary.md --json-out plan_summary.json
```

Add `--ai` for an LLM reviewer narrative section (`LLM_BACKEND=github_models|azure_openai|none`).

For apply runs:

```bash
terraform apply -auto-approve tfplan 2>&1 | tee apply.log
tf-report-apply --log apply.log --plan-summary-json plan_summary.json --out apply_summary.md \
  --history reports/apply-history.jsonl
```

For drift detection (e.g. nightly `terraform plan` on `main`):

```bash
tf-report-drift plan.json --out drift_summary.md --history reports/drift-history.jsonl
```

### v0.2 features

- `--baseline plan_summary.json` - show stat delta and **new** risks vs a prior run
- `--cost-json infracost.json` - overlay Infracost monthly Δ per resource
- `--heading "My Heading"` - custom H2 heading (great per-environment)
- `--config .tfreport.yml` - config file (auto-discovered up to nearest `.git`); supports `ignore` globs, `group_by_module`, `demote_tag_only`, `diff_details`
- `tf-report-drift` - drift report variant with its own JSONL history ledger
- `--history reports/*.jsonl` - append-only run ledger for apply/drift trends
- Provenance footer with Terraform version, tfreport version, timestamp, CI source

See [examples/.tfreport.yml](examples/.tfreport.yml) for a full config sample. Install `pip install tfreport[full]` to enable YAML config files.

## Quickstart - GitHub Actions (reusable workflow)

```yaml
# .github/workflows/terraform.yml in your consumer repo
name: Terraform
on:
  pull_request:
    paths: ["**/*.tf", "**/*.tfvars"]

jobs:
  plan:
    uses: Jinssi/tfreport/.github/workflows/terraform-plan.yml@v1
    with:
      working-directory: infra
      ai: true
```

That's it. You get: plan run, Markdown report, sticky PR comment, artifact, job summary.

For apply on `main`:

```yaml
jobs:
  apply:
    uses: Jinssi/tfreport/.github/workflows/terraform-apply.yml@v1
    with:
      working-directory: infra
      environment: production
```

## Quickstart - GitHub Actions (composite action only)

If you already have your own plan steps:

```yaml
- run: terraform plan -out=tfplan
- run: terraform show -json tfplan > plan.json

- uses: Jinssi/tfreport@v1
  with:
    mode: plan
    plan-json: plan.json
    ai: "true"
```

## Quickstart - Azure DevOps / GitLab / other CI

See [examples/azure-devops](examples/azure-devops/azure-pipelines.yml) and [examples/gitlab](examples/gitlab/.gitlab-ci.yml).

The pattern is always:

```
pip install tfreport
terraform show -json tfplan > plan.json
tf-report-plan plan.json --out plan_summary.md
```

## What the report contains

Plan reports (`tf-report-plan`) are now layered for deployers approving real changes:

- **Dashboard** — action-mix Mermaid pie, risk-severity Mermaid bar, Unicode-bar impact heatmap, headline badge row
- **Executive summary** + **Module map** (Mermaid flowchart colored by severity)
- **Blast radius** — top destructive ops and the downstream resources they would impact, derived from a real dependency graph of `configuration.root_module` references
- **Policy changes** — for `azurerm_policy_*` and `azuread_conditional_access_policy`: effect transitions (`Audit → Deny` etc.), parameter deltas, scope diffs. Tightening upgrades severity to **high**
- **Compliance checks** — pluggable rule packs (required tags, naming regex, region allowlist, public-network flags, encryption-at-rest) with a pass/fail score
- **Trend (last N runs)** — Unicode sparkbars for change-count, risks, cost delta, compliance score (with `--history PATH`)
- **Cost impact** (with `--cost-json`) — Infracost monthly Δ table + Mermaid bar chart of the top deltas
- **Baseline delta** (with `--baseline`) — stat delta + new HIGH risks vs prior run
- **Changes** — grouped by module, sorted replace → delete → update → create, with risk badge
- **Risks** — advisory list ordered by severity (high/medium/low)
- **Suggested reviewers** — derived from `routing.rules` config (address/type globs → reviewers)
- **Rollback playbook** — per destructive stateful change: pre-checks (snapshots, soft-delete, backup verification) and rollback steps
- **Resource details** — per-resource changed attributes, replace causes, before → after diff tables (sensitive values masked)
- **Tag-only updates** and **Ignored by config** collapsed into `<details>` blocks
- **Narrative** (optional) — LLM reviewer guidance built from the deterministic summary
- **Provenance footer** — Terraform version, tfreport version, timestamp, CI source

Apply reports (`tf-report-apply`) add an apply dashboard, planned-vs-applied table, a slowest-resources table, and a Mermaid `gantt` of the top-20 longest operations when the apply log contains duration markers.

### Extra output formats

| Flag | Output |
| --- | --- |
| `--out plan.md` | Markdown (always) |
| `--json-out plan.json` | Structured `PlanSummary` JSON |
| `--html-out plan.html` | Self-contained HTML report (inline CSS, SVG charts, no network calls) |
| `--sarif-out plan.sarif` | SARIF 2.1.0 — uploadable to GitHub Code Scanning |
| `--teams-out teams.json` | Microsoft Teams Adaptive Card v1.4 payload (POST to Incoming Webhook) |
| `--slack-out slack.json` | Slack Block Kit payload (POST to Incoming Webhook) |
| `--report-link URL` | Embeds an "Open report" button into Teams/Slack payloads |
| `--history reports/plan-history.jsonl` | Append a snapshot and render `## Trend` |

See [examples/sample-report.md](examples/sample-report.md) for a full example rendered against an AVM AI/ML Landing Zone plan.

### Config (`.tfreport.json` or `.tfreport.yml`)

```json
{
  "ignore": ["module.legacy.*"],
  "group_by_module": true,
  "demote_tag_only": true,
  "diff_details": true,
  "compliance": {
    "required_tags": ["owner", "costcenter", "env"],
    "allowed_regions": ["westeurope", "northeurope"],
    "naming": {"azurerm_storage_account": "^st[a-z0-9]{3,22}$"},
    "no_public_network": true,
    "encryption_required": true
  },
  "routing": {
    "rules": [
      {"glob": "module.network.*",           "reviewers": ["@team-netsec"]},
      {"type_glob": "azurerm_kubernetes_*",  "reviewers": ["@team-platform"]},
      {"glob": "*",                          "reviewers": ["@team-cloud"]}
    ]
  }
}
```

## Risk classification

Rules in [src/tfreport/risk_rules.py](src/tfreport/risk_rules.py):

- **High**: replace/delete of stateful resources (storage, SQL, Postgres, Cosmos, Key Vault, managed disks, Log Analytics, recovery vaults)
- **Medium**: identity/role changes; network resource replace/delete; policy/management-group changes
- **Low**: any replace; any delete

Advisory only. Customise by editing the `RULES` tuple and adding a fixture under `tests/fixtures/`.

## LLM narrative (optional)

Sends only the deterministic summary JSON (not raw plan content) to:

- **GitHub Models** (default in GHA) - uses workflow `GITHUB_TOKEN` with `models: read` permission. No extra secrets.
- **Azure OpenAI** - set `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`. Auth via `AZURE_OPENAI_API_KEY` or `DefaultAzureCredential` (OIDC). `pip install tfreport[azure]`.
- **none** - disable.

## Layout

```
src/tfreport/
  risk_rules.py      # advisory risk classifier
  plan.py            # plan JSON → Markdown + JSON
  apply.py           # apply log → Markdown
  drift.py           # tf-report-drift CLI
  diff.py            # attribute / module / replace_paths helpers
  delta.py           # baseline comparison
  cost.py            # Infracost JSON overlay
  config.py          # .tfreport.yml loader (PyYAML optional)
  provenance.py      # version + CI metadata footer
  narrative.py       # optional LLM
action.yml           # GitHub composite action
.github/workflows/
  terraform-plan.yml   # reusable workflow (workflow_call)
  terraform-apply.yml  # reusable workflow (workflow_call)
  ci.yml               # tests for this repo
  release.yml          # tag → PyPI (Trusted Publishing) + GitHub Release
scripts/
  gh-actions-cleanup.ps1 # CLI cleanup for Actions run/artifact history
examples/              # azure-devops, gitlab, github-actions, .tfreport.yml
tests/                 # pytest + fixtures
```

## Release maintenance

The tag release workflow now rebuilds distributions per job and does **not** rely on `upload-artifact` / `download-artifact`. This avoids GitHub Actions artifact-storage quota from blocking GitHub Releases or PyPI publishing.

If you still need to purge Actions history, use the helper script:

```powershell
pwsh ./scripts/gh-actions-cleanup.ps1 -Repo Jinssi/tfreport -DeleteArtifacts
```

Useful variants:

- Delete all completed runs, keep nothing: `pwsh ./scripts/gh-actions-cleanup.ps1 -Repo Jinssi/tfreport`
- Keep the newest 5 runs: `pwsh ./scripts/gh-actions-cleanup.ps1 -Repo Jinssi/tfreport -KeepRuns 5`
- Preview only: `pwsh ./scripts/gh-actions-cleanup.ps1 -Repo Jinssi/tfreport -DeleteArtifacts -DryRun`

## Development

```bash
git clone https://github.com/Jinssi/tfreport
cd Terraformer
pip install -e ".[dev]"
pytest -v
ruff check src tests
```

## License

[Apache-2.0](LICENSE).
