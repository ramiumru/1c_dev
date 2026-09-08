$ErrorActionPreference = "Stop"
# Исправление кросс-скилловых ссылок: .kilo/skills/ -> {{SKILLS_DIR}}/
# Запуск: powershell -File install/fix-cross-skill-refs.ps1
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)
$skillsDir = Join-Path $RepoRoot "core\skills"

$files = Get-ChildItem -Path $skillsDir -Recurse -Filter "SKILL.md"
foreach ($f in $files) {
    $content = [System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8)
    if ($content -and ($content -match '\.kilo/skills/')) {
        $newContent = $content -replace '\.kilo/skills/', '{{SKILLS_DIR}}/'
        [System.IO.File]::WriteAllText($f.FullName, $newContent, [System.Text.UTF8Encoding]::new($true))
        Write-Host "Fixed: $($f.FullName)"
    }
}
Write-Host "Done"
