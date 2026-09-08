$ErrorActionPreference = "Stop"
$srcDir = "C:\Ramium\1c-vibe\.kilo\agent"
$dstAgents = "C:\Ramium\1c-vibe\temp\1c_dev\core\agents"
$dstFm = "C:\Ramium\1c-vibe\temp\1c_dev\adapters\kilo\frontmatter"

$agents = @("1c-do","1c-analyst","1c-developer","1c-applier","1c-tools")

foreach ($name in $agents) {
    $raw = Get-Content -Path "$srcDir\$name.md" -Raw -Encoding UTF8

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
    $desc = ""
    if ($frontmatter -match '(?ms)^description:\s*(.+?)(?=\r?\n[a-z_-]+:|\Z)') { $desc = $matches[1].Trim() }

    $header = "<!-- Agent: $name | Mode: $mode | Model: $model -->`r`n"
    [System.IO.File]::WriteAllText("$dstAgents\$name.md", $header + $body, [System.Text.UTF8Encoding]::new($true))

    Write-Host "OK: $name (FM=$fmEnd lines, body=$($bodyLines.Count) lines)"
}
