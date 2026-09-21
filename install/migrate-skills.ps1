$ErrorActionPreference = "Stop"
# Миграция скиллов из .kilo/skills в core/skills с параметризацией путей.
# Запуск: powershell -File install/migrate-skills.ps1 -SourceRoot <путь-к-рабочему-проекту>
param(
    [Parameter(Mandatory=$true)][string]$SourceRoot,
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)
$srcDir = Join-Path $SourceRoot ".kilo\skills"
$dstDir = Join-Path $RepoRoot "core\skills"

$skills = Get-ChildItem -Path $srcDir -Directory
$count = 0

foreach ($skill in $skills) {
    $name = $skill.Name
    $srcSkill = $skill.FullName
    $dstSkill = Join-Path $dstDir $name

    # Copy entire skill directory (SKILL.md + scripts/)
    Copy-Item -Path $srcSkill -Destination $dstSkill -Recurse -Force

    # Replace .kilo/skills/<name>/ with {{SKILL_DIR}}/ in all files
    $files = Get-ChildItem -Path $dstSkill -Recurse -File
    foreach ($f in $files) {
        $content = [System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8)
        if ($content -and ($content -match '\.kilo/skills')) {
            $pattern = '\.kilo/skills/' + [regex]::Escape($name) + '/'
            $newContent = $content -replace $pattern, '{{SKILL_DIR}}/'
            [System.IO.File]::WriteAllText($f.FullName, $newContent, [System.Text.UTF8Encoding]::new($true))
        }
    }
    $count++
}

Write-Host "OK: migrated $count skills"
