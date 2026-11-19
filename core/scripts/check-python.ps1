<# Read-only runtime probe. Run as the normal interactive user, outside an agent.
No Python installation, PATH/config edits, package installation, or credential reads.
#>
param([string]$PythonPath, [ValidateRange(1,30)][int]$TimeoutSeconds = 8)
$ErrorActionPreference = 'Stop'
function Test-Interpreter([string]$Executable, [string]$Label, [switch]$Launcher) {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $prefix = if ($Launcher) { '-3 ' } else { '' }
    $info.Arguments = $prefix + '-I -X utf8 -c "import sys; print(''HARNESS_PYTHON|'' + sys.version.split()[0] + ''|'' + sys.executable)"'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $info.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    # No install-on-launch: supports both the legacy launcher and the new manager.
    $info.EnvironmentVariables['PYTHON_MANAGER_AUTOMATIC_INSTALL'] = 'false'
    $info.EnvironmentVariables.Remove('PYLAUNCHER_ALLOW_INSTALL')
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try {
        if (-not $process.Start()) { throw 'process did not start' }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            $process.WaitForExit()
            return [pscustomobject]@{command=$Label;path=$Executable;state='timeout';version=$null;interpreter=$null}
        }
        $output = $stdout.GetAwaiter().GetResult()
        $null = $stderr.GetAwaiter().GetResult()
        $match = [regex]::Match($output, '(?m)^HARNESS_PYTHON\|([0-9]+\.[0-9]+\.[0-9]+[^|\r\n]*)\|([^\r\n]+)\r?$')
        if ($process.ExitCode -eq 0 -and $match.Success) {
            return [pscustomobject]@{command=$Label;path=$Executable;state='available';version=$match.Groups[1].Value;interpreter=$match.Groups[2].Value}
        }
        # Do not print arbitrary process stderr/config/environment contents.
        return [pscustomobject]@{command=$Label;path=$Executable;state='launch_failed';exit_code=$process.ExitCode;version=$null;interpreter=$null}
    } catch {
        return [pscustomobject]@{command=$Label;path=$Executable;state='launch_failed';version=$null;interpreter=$null}
    } finally { $process.Dispose() }
}
$results = @()
if ($PythonPath) {
    $resolved = (Resolve-Path -LiteralPath $PythonPath).Path
    $results += Test-Interpreter $resolved 'explicit interpreter'
} else {
    foreach ($entry in @(@{name='python';launcher=$false}, @{name='py';launcher=$true})) {
        $commands = @(Get-Command $entry.name -CommandType Application -All -ErrorAction SilentlyContinue)
        if ($commands.Count -eq 0) {
            $results += [pscustomobject]@{command=$entry.name;path=$null;state='not_on_process_path';version=$null;interpreter=$null}
            continue
        }
        # Probe the executable this command actually resolves to, not a later PATH entry.
        $path = $commands[0].Source
        if (-not $entry.launcher -and $path -match '(?i)[\\/]Microsoft[\\/]WindowsApps[\\/]python(?:3)?\.exe$') {
            $results += [pscustomobject]@{command='python';path=$path;state='windowsapps_alias_unverified';version=$null;interpreter=$null}
        } else { $results += Test-Interpreter $path $(if ($entry.launcher) {'py -3'} else {'python'}) -Launcher:$entry.launcher }
    }
}
[pscustomobject]@{
    process='this PowerShell process; not proof of the running AI client environment'
    runtimes=$results
    installed_on_machine='not determined by PATH-only checks'
    permissions='not tested; need exact rejected tool call and effective client configuration'
    changed_settings=$false
} | ConvertTo-Json -Depth 5
