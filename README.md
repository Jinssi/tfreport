# tfreport

[![CI](https://github.com/Jinssi/terraformer/actions/workflows/ci.yml/badge.svg)](https://github.com/Jinssi/terraformer/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/tfreport.svg)](https://pypi.org/project/tfreport/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Advisory** Terraform plan/apply report generator. Turns `terraform show -json` and `terraform apply` output into a readable Markdown report (counts, change table, risk flags, optional LLM narrative). Reports are advisory — they never fail your build.

Designed for Azure / AVM / ALZ environments but works on any provider.

## Three ways to use it

| Shape | Best for | Install |
| --- | --- | --- |
| **GitHub reusable workflow** | GitHub-Actions repos that want the full pipeline | `uses: Jinssi/terraformer/.github/workflows/terraform-plan.yml@v1` |
| **GitHub composite action** | GHA users with their own terraform steps | `uses: Jinssi/terraformer@v1` |
| **PyPI package (`tfreport`)** | Any other CI (Azure DevOps, GitLab, Jenkins, local) | `pip install tfreport` |

All three call the same engine.

## Quickstart — local

```bash
pip install tfreport
terraform plan -out=tfplan
terraform show -json tfplan > plan.json
tf-report-plan plan.json --out plan_summary.md --json-out plan_summary.json
```

Add `--ai` for an LLM narrative section (`LLM_BACKEND=github_models|azure_openai|none`).

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

- `--baseline plan_summary.json` — show stat delta and **new** risks vs a prior run
- `--cost-json infracost.json` — overlay Infracost monthly Δ per resource
- `--heading "My Heading"` — custom H2 heading (great per-environment)
- `--config .tfreport.yml` — config file (auto-discovered up to nearest `.git`); supports `ignore` globs, `group_by_module`, `demote_tag_only`, `diff_details`
- `tf-report-drift` — drift report variant with its own JSONL history ledger
- `--history reports/*.jsonl` — append-only run ledger for apply/drift trends
- Provenance footer with Terraform version, tfreport version, timestamp, CI source

See [examples/.tfreport.yml](examples/.tfreport.yml) for a full config sample. Install `pip install tfreport[full]` to enable YAML config files.

## Quickstart — GitHub Actions (reusable workflow)

```yaml
# .github/workflows/terraform.yml in your consumer repo
name: Terraform
on:
  pull_request:
    paths: ["**/*.tf", "**/*.tfvars"]

jobs:
  plan:
    uses: Jinssi/terraformer/.github/workflows/terraform-plan.yml@v1
    with:
      working-directory: infra
      ai: true
```

That's it. You get: plan run, Markdown report, sticky PR comment, artifact, job summary.

For apply on `main`:

```yaml
jobs:
  apply:
    uses: Jinssi/terraformer/.github/workflows/terraform-apply.yml@v1
    with:
      working-directory: infra
      environment: production
```

## Quickstart — GitHub Actions (composite action only)

If you already have your own plan steps:

```yaml
- run: terraform plan -out=tfplan
- run: terraform show -json tfplan > plan.json

- uses: Jinssi/terraformer@v1
  with:
    mode: plan
    plan-json: plan.json
    ai: "true"
```

## Quickstart — Azure DevOps / GitLab / other CI

See [examples/azure-devops](examples/azure-devops/azure-pipelines.yml) and [examples/gitlab](examples/gitlab/.gitlab-ci.yml).

The pattern is always:

```
pip install tfreport
terraform show -json tfplan > plan.json
tf-report-plan plan.json --out plan_summary.md
```

## What the report contains

- **Summary**: total changes + create/update/delete/replace counts
- **Stats**: tabular counts incl. read & no-op
- **Cost impact** (with `--cost-json`): Infracost monthly Δ, top changed resources
- **Baseline delta** (with `--baseline`): stat delta + new HIGH risks vs prior run
- **Changes**: grouped by module, sorted replace → delete → update → create, with risk badge
- **Tag-only updates** collapsed into a `<details>` block (configurable)
- **Risks**: advisory list ordered by severity (high/medium/low)
- **Resource details**: per-resource changed attributes, replace causes, fired rules
- **Narrative** (optional): LLM-generated 2-paragraph human summary
- **Provenance footer**: Terraform version, tfreport version, timestamp, CI source

## Risk classification

Rules in [src/tfreport/risk_rules.py](src/tfreport/risk_rules.py):

- **High**: replace/delete of stateful resources (storage, SQL, Postgres, Cosmos, Key Vault, managed disks, Log Analytics, recovery vaults)
- **Medium**: identity/role changes; network resource replace/delete; policy/management-group changes
- **Low**: any replace; any delete

Advisory only. Customise by editing the `RULES` tuple and adding a fixture under `tests/fixtures/`.

## LLM narrative (optional)

Sends only the deterministic summary JSON (not raw plan content) to:

- **GitHub Models** (default in GHA) — uses workflow `GITHUB_TOKEN` with `models: read` permission. No extra secrets.
- **Azure OpenAI** — set `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`. Auth via `AZURE_OPENAI_API_KEY` or `DefaultAzureCredential` (OIDC). `pip install tfreport[azure]`.
- **none** — disable.

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
examples/              # azure-devops, gitlab, github-actions, .tfreport.yml
tests/                 # pytest + fixtures
```

## Development

```bash
git clone https://github.com/Jinssi/terraformer
cd Terraformer
pip install -e ".[dev]"
pytest -v
ruff check src tests
```

## License

[Apache-2.0](LICENSE).
