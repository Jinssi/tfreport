param(
    [Parameter(Mandatory = $false)]
    [string]$Repo = "Jinssi/tfreport",

    [Parameter(Mandatory = $false)]
    [int]$KeepRuns = 0,

    [Parameter(Mandatory = $false)]
    [switch]$KeepInProgress,

    [Parameter(Mandatory = $false)]
    [switch]$DeleteArtifacts,

    [Parameter(Mandatory = $false)]
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is required."
}

$null = gh auth status

$runsJson = gh api "repos/$Repo/actions/runs" --paginate
$runs = ($runsJson | ConvertFrom-Json | ForEach-Object workflow_runs)

if (-not $runs) {
    Write-Host "No workflow runs found for $Repo"
} else {
    $sortedRuns = $runs | Sort-Object created_at -Descending
    $keepIds = @()
    if ($KeepRuns -gt 0) {
        $keepIds = $sortedRuns | Select-Object -First $KeepRuns | ForEach-Object { $_.id }
    }

    foreach ($run in $sortedRuns) {
        $shouldKeep = $keepIds -contains $run.id
        if ($KeepInProgress -and $run.status -ne "completed") {
            $shouldKeep = $true
        }
        if ($shouldKeep) {
            Write-Host "Keeping run $($run.id) [$($run.name)] status=$($run.status) conclusion=$($run.conclusion)"
            continue
        }

        if ($DryRun) {
            Write-Host "Would delete run $($run.id) [$($run.name)] created=$($run.created_at)"
            continue
        }

        gh api -X DELETE "repos/$Repo/actions/runs/$($run.id)" | Out-Null
        Write-Host "Deleted run $($run.id) [$($run.name)]"
    }
}

if ($DeleteArtifacts) {
    $artifactsJson = gh api "repos/$Repo/actions/artifacts" --paginate
    $artifacts = ($artifactsJson | ConvertFrom-Json | ForEach-Object artifacts)

    if (-not $artifacts) {
        Write-Host "No artifacts found for $Repo"
    } else {
        foreach ($artifact in $artifacts) {
            if ($DryRun) {
                Write-Host "Would delete artifact $($artifact.id) [$($artifact.name)] size=$($artifact.size_in_bytes)"
                continue
            }

            gh api -X DELETE "repos/$Repo/actions/artifacts/$($artifact.id)" | Out-Null
            Write-Host "Deleted artifact $($artifact.id) [$($artifact.name)]"
        }
    }
}