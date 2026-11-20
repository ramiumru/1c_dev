# Run manually in the user's Windows PowerShell, never through an agent.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z_][A-Za-z0-9_]*$')][string]$UsernameEnv,
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z_][A-Za-z0-9_]*$')][string]$PasswordEnv
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This credential helper requires Windows.' }
if ($UsernameEnv -eq $PasswordEnv) { throw 'Use different variable names.' }
$reserved = @('PATH','HOME','USERPROFILE','SYSTEMROOT','WINDIR','TEMP','TMP','PSMODULEPATH','PYTHONPATH','PYTHONHOME','CODEX_HOME','PATHEXT','COMSPEC')
if ($reserved -contains $UsernameEnv -or $reserved -contains $PasswordEnv) { throw 'System environment variables cannot store credentials.' }
Write-Host 'Credentials will be stored in Windows User environment variables.'
Write-Host 'Environment variables are not a credential vault. Values will not be printed.'
Write-Host 'No database or repository operation will be performed.'
$confirmation = Read-Host 'Enter YES to continue'
if ($confirmation -cne 'YES') { Write-Host 'Cancelled. Nothing changed.'; return }
$login = Read-Host 'Repository user'
$secret = Read-Host 'Repository password' -AsSecureString
if ([string]::IsNullOrWhiteSpace($login) -or $secret.Length -eq 0) { throw 'Empty credentials are unsupported.' }
$pointer = [IntPtr]::Zero
try {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    [Environment]::SetEnvironmentVariable($UsernameEnv, $login, 'User')
    [Environment]::SetEnvironmentVariable($PasswordEnv, $plain, 'User')
    Write-Host 'Saved for the current Windows user. Fully exit and restart VS Code/client.'
    Write-Host 'Opening a new chat alone does not refresh the process environment.'
} finally {
    $plain = $null
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    if ($secret) { $secret.Dispose() }
}
