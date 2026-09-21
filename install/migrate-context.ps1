$ErrorActionPreference = "Stop"
# Миграция контекста из .kilo/context в core/context.
# Запуск: powershell -File install/migrate-context.ps1 -SourceRoot <путь-к-рабочему-проекту>
param(
    [Parameter(Mandatory=$true)][string]$SourceRoot,
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)
$dst = $RepoRoot

# 1. INSTRUCTIONS.md -> core/context/INSTRUCTIONS.md (as-is)
Copy-Item -Path (Join-Path $SourceRoot "INSTRUCTIONS.md") -Destination (Join-Path $dst "core\context\INSTRUCTIONS.md") -Force
Write-Host "OK: INSTRUCTIONS.md"

# 2. AGENTS.md -> core/context/BslChecklists.md (as-is)
Copy-Item -Path (Join-Path $SourceRoot "AGENTS.md") -Destination (Join-Path $dst "core\context\BslChecklists.md") -Force
Write-Host "OK: BslChecklists.md (from AGENTS.md)"

# 3. standards: публичный core хранит только нейтральный шаблон standards.example.md;
#    корпоративные стандарты НЕ мигрируются в core (для них — external overlay,
#    см. docs/corporate-overlay-recommendations.md).

# 4. common/requirements-README.md -> core/context/common/
Copy-Item -Path (Join-Path $SourceRoot ".kilo\context\common\requirements-README.md") -Destination (Join-Path $dst "core\context\common\requirements-README.md") -Force
Write-Host "OK: requirements-README.md"

# 5. Per-project: only context.md, objects-index.md, analyst-scope.md (NO summaries/, requirements/)
$projectsRoot = Join-Path $SourceRoot ".kilo\context\projects"
if (Test-Path $projectsRoot) {
    $projects = @(Get-ChildItem -Path $projectsRoot -Directory | ForEach-Object { $_.Name })
} else {
    $projects = @()
}
foreach ($proj in $projects) {
    $srcProj = Join-Path $SourceRoot ".kilo\context\projects\$proj"
    $dstProj = Join-Path $dst "core\context\projects\$proj"
    New-Item -ItemType Directory -Force -Path $dstProj | Out-Null

    $files = @("context.md","objects-index.md","analyst-scope.md")
    foreach ($fn in $files) {
        $srcFile = Join-Path $srcProj $fn
        if (Test-Path $srcFile) {
            Copy-Item -Path $srcFile -Destination "$dstProj\$fn" -Force
        }
    }
    Write-Host "OK: project $proj"
}

# 6. specs/README.md -> core/sdd/README.md (as-is)
Copy-Item -Path (Join-Path $SourceRoot "specs\README.md") -Destination (Join-Path $dst "core\sdd\README.md") -Force
Write-Host "OK: sdd/README.md"

Write-Host "All context files migrated."
