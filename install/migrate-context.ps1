$ErrorActionPreference = "Stop"
$root = "C:\Ramium\1c-vibe"
$dst = "C:\Ramium\1c-vibe\temp\1c_dev"

# 1. INSTRUCTIONS.md -> core/context/INSTRUCTIONS.md (as-is)
Copy-Item -Path "$root\INSTRUCTIONS.md" -Destination "$dst\core\context\INSTRUCTIONS.md" -Force
Write-Host "OK: INSTRUCTIONS.md"

# 2. AGENTS.md -> core/context/BslChecklists.md (as-is)
Copy-Item -Path "$root\AGENTS.md" -Destination "$dst\core\context\BslChecklists.md" -Force
Write-Host "OK: BslChecklists.md (from AGENTS.md)"

# 3. standards/level-standards.md -> core/context/standards/
Copy-Item -Path "$root\.kilo\context\standards\level-standards.md" -Destination "$dst\core\context\standards\level-standards.md" -Force
Write-Host "OK: level-standards.md"

# 4. common/requirements-README.md -> core/context/common/
Copy-Item -Path "$root\.kilo\context\common\requirements-README.md" -Destination "$dst\core\context\common\requirements-README.md" -Force
Write-Host "OK: requirements-README.md"

# 5. Per-project: only context.md, objects-index.md, analyst-scope.md (NO summaries/, requirements/)
$projects = @("finance","trade","collector")
foreach ($proj in $projects) {
    $srcProj = "$root\.kilo\context\projects\$proj"
    $dstProj = "$dst\core\context\projects\$proj"
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
Copy-Item -Path "$root\specs\README.md" -Destination "$dst\core\sdd\README.md" -Force
Write-Host "OK: sdd/README.md"

Write-Host "All context files migrated."
