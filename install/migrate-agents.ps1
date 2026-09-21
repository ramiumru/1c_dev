$ErrorActionPreference = "Stop"
# Миграция агентов из .kilo/agent/ в core/agents/ + frontmatter в adapters/kilo/.
# Запуск: powershell -File install/migrate-agents.ps1 -SourceRoot <путь-к-рабочему-проекту>
# -SourceRoot обязателен (корень рабочего проекта с .kilo/agent/), -RepoRoot — по умолчанию .. репо.
param(
    [Parameter(Mandatory=$true)][string]$SourceRoot,
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)
$srcDir = Join-Path $SourceRoot ".kilo\agent"
$dstAgents = Join-Path $RepoRoot "core\agents"
$dstFm = Join-Path $RepoRoot "adapters\kilo\frontmatter"

$agents = @("1c-do","1c-analyst","1c-developer","1c-reviewer","1c-applier","1c-tools")

foreach ($name in $agents) {
    $srcFile = Join-Path $srcDir "$name.md"
    if (-not (Test-Path $srcFile)) { Write-Host "SKIP (no source): $name"; continue }
    $raw = [System.IO.File]::ReadAllText($srcFile, [System.Text.Encoding]::UTF8)

    # Split on first --- ... --- pair
    $lines = $raw -split "`r?`n"
    $fmEnd = -1
    for ($i = 1; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^---\s*$') { $fmEnd = $i; break }
    }
    if ($fmEnd -eq -1) { throw "No frontmatter end marker in $name.md" }

    $fmLines = $lines[1..($fmEnd - 1)]
    $bodyLines = $lines[($fmEnd + 1)..($lines.Count - 1)]
    $frontmatter = ($fmLines -join "`r`n")
    $body = ($bodyLines -join "`r`n")

    # Parameterize runtime paths in body
    $body = $body -replace '\.kilo/context', '{{CONTEXT_DIR}}'
    $body = $body -replace '\.kilo/logs', '{{LOGS_DIR}}'
    $body = $body -replace '\.kilo/skills', '{{SKILLS_DIR}}'
    $body = $body -replace '\.kilo/agent', '{{AGENTS_DIR}}'

    # Write frontmatter (as .yml, no leading ---)
    [System.IO.File]::WriteAllText("$dstFm\$name.yml", $frontmatter + "`r`n", [System.Text.UTF8Encoding]::new($true))

    # Write body (with header comment about model/mode/description)
    $model = ""
    if ($frontmatter -match '(?m)^model:\s*(.+)$') { $model = $matches[1].Trim() }
    $mode = ""
    if ($frontmatter -match '(?m)^mode:\s*(.+)$') { $mode = $matches[1].Trim() }

    $header = "<!-- Agent: $name | Mode: $mode | Model: $model -->`r`n"
    [System.IO.File]::WriteAllText("$dstAgents\$name.md", $header + $body, [System.Text.UTF8Encoding]::new($true))

    Write-Host "OK: $name (FM=$fmEnd lines, body=$($bodyLines.Count) lines)"
}
