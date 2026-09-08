$ErrorActionPreference = "Stop"
$kiloFmDir = "C:\Ramium\1c-vibe\temp\1c_dev\adapters\kilo\frontmatter"
$ocFmDir = "C:\Ramium\1c-vibe\temp\1c_dev\adapters\opencode\frontmatter"

$agents = @("1c-do","1c-analyst","1c-developer","1c-applier","1c-tools")

foreach ($name in $agents) {
    $fm = Get-Content -Path "$kiloFmDir\$name.yml" -Raw -Encoding UTF8

    # 1. Replace .kilo/ paths with .opencode/
    $fm = $fm -replace '\.kilo/', '.opencode/'

    # 2. Remove semantic_search (not in OpenCode)
    $fm = $fm -replace '(?m)^\s*semantic_search:\s*deny\s*\r?\n', ''

    # 3. Replace mcp: block with comment (OpenCode controls MCP at server level)
    # Match the mcp: block from "  mcp:" to the next top-level key or end
    $fm = $fm -replace '(?ms)(^\s*mcp:\s*\r?\n.*?)(?=^\S|\Z)', "# MCP permissions: configured at server level in opencode.json (mcp key)`r`n"

    # 4. Clean up trailing whitespace/empty lines
    $fm = $fm -replace '(\r?\n){3,}', "`r`n`r`n"
    $fm = $fm.TrimEnd() + "`r`n"

    [System.IO.File]::WriteAllText("$ocFmDir\$name.yml", $fm, [System.Text.UTF8Encoding]::new($true))
    Write-Host "OK: $name"
}
Write-Host "Done: OpenCode frontmatter generated"
