$ErrorActionPreference = "Stop"
$kiloFmDir = "C:\Ramium\1c-vibe\temp\1c_dev\adapters\kilo\frontmatter"
$claudeFmDir = "C:\Ramium\1c-vibe\temp\1c_dev\adapters\claude\frontmatter"

$agents = @("1c-do","1c-analyst","1c-developer","1c-applier","1c-tools")

foreach ($name in $agents) {
    $fm = Get-Content -Path "$kiloFmDir\$name.yml" -Raw -Encoding UTF8

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
