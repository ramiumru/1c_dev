$ErrorActionPreference = "Stop"
$skillsDir = "C:\Ramium\1c-vibe\temp\1c_dev\core\skills"

$files = Get-ChildItem -Path $skillsDir -Recurse -Filter "SKILL.md"
foreach ($f in $files) {
    $content = Get-Content -Path $f.FullName -Raw -Encoding UTF8
    if ($content -and ($content -match '\.kilo/skills/')) {
        $newContent = $content -replace '\.kilo/skills/', '{{SKILLS_DIR}}/'
        [System.IO.File]::WriteAllText($f.FullName, $newContent, [System.Text.UTF8Encoding]::new($true))
        Write-Host "Fixed: $($f.FullName)"
    }
}
Write-Host "Done"
