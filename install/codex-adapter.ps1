# Codex native adapter. Dot-sourced by install.ps1 after update/protection helpers.
# Writes only project-local files; never changes the user's Codex home/configuration.
$codexGeneratedFiles = @{}
$codexUtf8 = New-Object System.Text.UTF8Encoding($false)

function Write-CodexManagedFile([string]$RelPath, [byte[]]$Bytes, [string]$Source) {
    $dst = Join-Path $Target $RelPath
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $hash = ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-', '') }
    finally { $sha.Dispose() }
    # Store the expected public bytes, not the hash of a preserved user file.
    $script:codexGeneratedFiles[$RelPath] = [PSCustomObject]@{
        path=$RelPath; source=$Source; installedHash=$hash; userModified=$false
    }
    $exists = Test-Path -LiteralPath $dst
    $unmanaged = $exists -and -not ($existingManifest -and @($existingManifest.files | Where-Object { $_.path -eq $RelPath }).Count)
    if ($exists -and -not $Force -and ($Mode -eq 'install' -or $unmanaged -or -not (Test-ShouldOverwrite $RelPath))) {
        Write-Host "    skip (Codex existing/user-modified): $RelPath"
        return
    }
    if ($exists -and $Force) {
        $bak = "$dst.bak"
        $i = 1
        while (Test-Path -LiteralPath $bak) { $bak = "$dst.bak$i"; $i++ }
        Copy-Item -LiteralPath $dst -Destination $bak -Force
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
    [System.IO.File]::WriteAllBytes($dst, $Bytes)
}

function Install-CodexSkills {
    Get-ChildItem -LiteralPath "$repo/core/skills" -Directory | ForEach-Object {
        $skill = $_
        Get-ChildItem -LiteralPath $skill.FullName -Recurse -File | ForEach-Object {
            if ($_.FullName -match '[\\/]__pycache__[\\/]' -or $_.Extension -eq '.pyc') { return }
            $inside = $_.FullName.Substring($skill.FullName.Length).TrimStart('\','/') -replace '\\','/'
            $rel = "$skills/$($skill.Name)/$inside"
            $bytes = [System.IO.File]::ReadAllBytes($_.FullName)
            # These placeholders occur in text resources; leave binary assets untouched.
            if ($_.Extension -in @('.md', '.py', '.ps1', '.json', '.yaml', '.yml', '.txt')) {
                $text = [System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)
                if ($_.Name -eq 'SKILL.md' -or $text.Contains('{{SKILL')) {
                    $text = $text.Replace('{{SKILL_DIR}}', "$skills/$($skill.Name)").Replace('{{SKILLS_DIR}}', $skills)
                    $bytes = $codexUtf8.GetBytes($text)
                }
            }
            Write-CodexManagedFile $rel $bytes "core/skills/$($skill.Name)/$inside"
        }
    }
}

function Install-CodexAgents {
    $roles = Get-Content -LiteralPath "$repo/adapters/codex/roles.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    $runtime = [System.IO.File]::ReadAllText((Convert-Path "$repo/adapters/codex/runtime.md"), [System.Text.Encoding]::UTF8)
    foreach ($role in $roles.PSObject.Properties) {
        $name = $role.Name
        $body = [System.IO.File]::ReadAllText((Convert-Path "$repo/core/agents/$name.md"), [System.Text.Encoding]::UTF8)
        $body = $body.Replace('{{CONTEXT_DIR}}', $ctx).Replace('{{LOGS_DIR}}', $logs).Replace('{{SKILLS_DIR}}', $skills).Replace('{{AGENTS_DIR}}', $agents)
        # Retain the Markdown instruction files for direct reading and older clients.
        Write-CodexManagedFile "$agents/$name.md" ($codexUtf8.GetBytes($body)) "core/agents/$name.md"
        # JSON basic-string escaping is compatible with TOML basic strings.
        $instructions = "$runtime`n`n$body"
        $toml = "name = " + (ConvertTo-Json -InputObject $name -Compress) + "`n"
        $toml += "description = " + (ConvertTo-Json -InputObject ([string]$role.Value) -Compress) + "`n"
        $toml += "developer_instructions = " + (ConvertTo-Json -InputObject $instructions -Compress) + "`n"
        if ($name -eq '1c-git-reviewer') {
            $toml += "`n[features]`napps = false`nplugins = false`nmemories = false`n"
        }
        Write-CodexManagedFile ".codex/agents/$name.toml" ($codexUtf8.GetBytes($toml)) "core/agents/$name.md"
    }
}
