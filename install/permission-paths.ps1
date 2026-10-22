# Kilo file permissions are matched relative to Instance.worktree ("/" without Git).
# Render exact project prefixes; never grant **/specs/** across unrelated projects.
function Get-KiloPermissionRoot([string]$ProjectPath) {
    $full = [System.IO.Path]::GetFullPath($ProjectPath)
    $candidate = $full
    while ($candidate) {
        if (Test-Path -LiteralPath (Join-Path $candidate '.git')) {
            if (Get-Command git -ErrorAction SilentlyContinue) {
                $common = & git -C $candidate rev-parse --git-common-dir 2>$null
                if ($LASTEXITCODE -eq 0 -and $common) {
                    if ([System.IO.Path]::IsPathRooted($common)) {
                        $commonPath = [System.IO.Path]::GetFullPath($common)
                    } else {
                        $commonPath = [System.IO.Path]::GetFullPath((Join-Path $candidate $common))
                    }
                    return (Split-Path -Parent $commonPath)
                }
            }
            return $candidate
        }
        $parent = Split-Path -Parent $candidate
        if ($parent -eq $candidate) { break }
        $candidate = $parent
    }
    return [System.IO.Path]::GetPathRoot($full)
}

function Get-KiloPermissionPrefix([string]$ProjectPath, [string]$WorktreePath) {
    # Uri.MakeRelativeUri works on Windows PowerShell 5.1 (.NET GetRelativePath does not).
    $project = [System.IO.Path]::GetFullPath($ProjectPath).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $worktree = [System.IO.Path]::GetFullPath($WorktreePath).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $relative = ([System.Uri]::new($worktree, [System.UriKind]::Absolute)).MakeRelativeUri([System.Uri]::new($project, [System.UriKind]::Absolute))
    if ($relative.IsAbsoluteUri) { throw 'Kilo permission root and project must share a filesystem root.' }
    $prefix = [System.Uri]::UnescapeDataString($relative.ToString()).Replace('\', '/')
    # Prefix must be literal in Kilo's wildcard matcher, not a wildcard directory.
    if ($prefix -match '[*?]') { throw "Unsafe Kilo permission prefix: $prefix" }
    return $prefix
}

function Convert-KiloFilePermissions([string]$Frontmatter, [string]$Prefix) {
    $section = ''
    $seen = @{}
    $output = [System.Collections.Generic.List[string]]::new()
    foreach ($line in ($Frontmatter -split '\r?\n')) {
        if ($line -match '^  ([a-z_]+):\s*$') {
            $section = $matches[1]
            $seen = @{}
        } elseif ($line -match '^\S|^  \S') { $section = '' }
        # Only resolved file paths: glob/grep use tool-input patterns, not file paths.
        if ($section -in @('read', 'edit') -and $line -match '^    "([^"]+)":\s*(allow|deny|ask)\s*$') {
            $pattern = $matches[1]
            $action = $matches[2]
            if ($pattern -ne '*') {
                # Old do-agent recursive aliases must not grant access outside Target.
                while ($pattern.StartsWith('**/')) { $pattern = $pattern.Substring(3) }
                $pattern = $Prefix + $pattern
                if ($seen.ContainsKey($pattern)) {
                    if ($seen[$pattern] -ne $action) { throw "Conflicting Kilo permission: $pattern" }
                    continue
                }
                $seen[$pattern] = $action
                $key = ConvertTo-Json -InputObject $pattern -Compress
                $line = "    ${key}: $action"
            }
        }
        $output.Add($line)
    }
    return ($output -join "`r`n")
}
