# Caller workflow examples

Drop one of these into a consumer Terraform repo at `.github/workflows/`.

## Plan on PR

```yaml
# .github/workflows/terraform.yml
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

Set repo Variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` for OIDC, or pass `use-azure-login: false`.

## Apply on main

```yaml
# .github/workflows/terraform-apply.yml
name: Terraform Apply

on:
  push:
    branches: [main]
    paths: ["**/*.tf", "**/*.tfvars"]
  workflow_dispatch:

jobs:
  apply:
    uses: Jinssi/terraformer/.github/workflows/terraform-apply.yml@v1
    with:
      working-directory: infra
      environment: production
```

## Just the action (BYO terraform steps)

```yaml
- run: terraform plan -out=tfplan
- run: terraform show -json tfplan > plan.json

- uses: Jinssi/terraformer@v1
  with:
    mode: plan
    plan-json: plan.json
    ai: "true"
```
