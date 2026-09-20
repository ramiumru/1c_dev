$ErrorActionPreference = "Stop"
# Генератор frontmatter для адаптера Open Works из frontmatter Kilo.
# Запуск из корня репо ai-environment: powershell -File install/gen-openworks-fm.ps1
$repo = Split-Path -Parent $PSScriptRoot
$kiloFmDir = Join-Path $repo "adapters\kilo\frontmatter"
$owFmDir = Join-Path $repo "adapters\openworks\frontmatter"

$agents = @("1c-do","1c-analyst","1c-developer","1c-reviewer","1c-applier","1c-tools")

foreach ($name in $agents) {
    $src = Join-Path $kiloFmDir "$name.yml"
    if (-not (Test-Path $src)) { Write-Host "SKIP (no source): $name"; continue }
    $fm = [System.IO.File]::ReadAllText($src, [System.Text.Encoding]::UTF8)

    # 1. Replace .kilo/ paths with .opencode/agents/ (plural) or .opencode/<rest>
    #    (Open Works installs to .opencode/ per install.ps1)
    $fm = $fm -replace '\.kilo/agent/', '.opencode/agents/'
    $fm = $fm -replace '\.kilo/', '.opencode/'

    # 2. Remove semantic_search (not in Open Works)
    $fm = $fm -replace '(?m)^\s*semantic_search:\s*deny\s*\r?\n', ''

    # 3. Replace mcp: block with comment (Open Works controls MCP at server level)
    $fm = $fm -replace '(?ms)(^\s*mcp:\s*\r?\n.*?)(?=^\S|\Z)', "# MCP permissions: configured at server level in openworks.json (mcp key)`r`n"

    # 4. Clean up trailing whitespace/empty lines
    $fm = $fm -replace '(\r?\n){3,}', "`r`n`r`n"
    $fm = $fm.TrimEnd() + "`r`n"

    $dst = Join-Path $owFmDir "$name.yml"
    $dstDir = Split-Path $dst -Parent
    New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
    [System.IO.File]::WriteAllText($dst, $fm, [System.Text.UTF8Encoding]::new($true))
    Write-Host "OK: $name"
}
Write-Host "Done: Open Works frontmatter generated"
