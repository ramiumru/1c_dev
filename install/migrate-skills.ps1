$ErrorActionPreference = "Stop"
$srcDir = "C:\Ramium\1c-vibe\.kilo\skills"
$dstDir = "C:\Ramium\1c-vibe\temp\1c_dev\core\skills"

$skills = Get-ChildItem -Path $srcDir -Directory
$count = 0

foreach ($skill in $skills) {
    $name = $skill.Name
    $srcSkill = $skill.FullName
    $dstSkill = Join-Path $dstDir $name

    # Copy entire skill directory (SKILL.md + scripts/)
    Copy-Item -Path $srcSkill -Destination $dstSkill -Recurse -Force

    # Replace .kilo/skills/<name>/ with {{SKILL_DIR}}/ in all .md and .ps1 files
    $files = Get-ChildItem -Path $dstSkill -Recurse -File
    foreach ($f in $files) {
        $content = Get-Content -Path $f.FullName -Raw -Encoding UTF8
        if ($content -and ($content -match '\.kilo/skills')) {
            $pattern = '\.kilo/skills/' + [regex]::Escape($name) + '/'
            $newContent = $content -replace $pattern, '{{SKILL_DIR}}/'
            [System.IO.File]::WriteAllText($f.FullName, $newContent, [System.Text.UTF8Encoding]::new($true))
        }
    }
    $count++
}

Write-Host "OK: migrated $count skills"
