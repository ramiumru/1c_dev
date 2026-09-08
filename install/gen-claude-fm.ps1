$ErrorActionPreference = "Stop"
# Генератор frontmatter для адаптера Claude из frontmatter Kilo.
# Запуск из корня репо ai-environment: powershell -File install/gen-claude-fm.ps1
$repo = Split-Path -Parent $PSScriptRoot
$kiloFmDir = Join-Path $repo "adapters\kilo\frontmatter"
$claudeFmDir = Join-Path $repo "adapters\claude\frontmatter"

$agents = @("1c-do","1c-analyst","1c-developer","1c-reviewer","1c-applier","1c-tools")

foreach ($name in $agents) {
    $src = Join-Path $kiloFmDir "$name.yml"
    if (-not (Test-Path $src)) { Write-Host "SKIP (no source): $name"; continue }
    $fm = [System.IO.File]::ReadAllText($src, [System.Text.Encoding]::UTF8)

    # Extract description
    $desc = ""
    if ($fm -match '(?ms)^description:\s*(.+?)(?=\r?\n[a-z_-]+:)') { $desc = $matches[1].Trim() }
    elseif ($fm -match '(?ms)^description:\s*"(.+?)"') { $desc = $matches[1] }
    elseif ($fm -match '(?ms)^description:\s*(.+)') { $desc = $matches[1].Trim() }

    # Determine tools based on agent role
    $tools = switch ($name) {
        "1c-do"        { "Read, Edit, Glob, Grep, Task" }
        "1c-analyst"   { "Read, Glob, Grep, Bash, Skill" }
        "1c-developer" { "Read, Edit, Glob, Grep, Bash, Skill" }
        "1c-reviewer"  { "Read, Edit, Glob, Grep, Bash, Skill" }
        "1c-applier"   { "Read, Glob, Grep, Bash, Skill" }
        "1c-tools"     { "Read, Glob, Grep, Bash" }
    }

    $model = "level/z-ai/glm-5.2"

    $claudeFm = @"
---
name: $name
description: $desc
tools: $tools
model: $model
---
"@

    [System.IO.File]::WriteAllText("$claudeFmDir\$name.yml", $claudeFm + "`r`n", [System.Text.UTF8Encoding]::new($true))
    Write-Host "OK: $name"
}
Write-Host "Done: Claude frontmatter generated"
