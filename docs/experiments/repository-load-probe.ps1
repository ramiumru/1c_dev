<#
.SYNOPSIS
    Repository-connection probe for /LoadConfigFromFiles and /UpdateDBCfg (8.3.27).
    ONLY for a separate TEST copy of the infobase bound to a TEST repository.

.DESCRIPTION
    Runs one variant per call and stores anonymised evidence under -OutDir\<variant>-<stamp>:
      command.txt   - command line with every credential replaced by ***
      out.txt       - /Out of the platform, credentials replaced by ***
      dumpresult.txt- /DumpResult as written by the platform (absent = not written)
      meta.json     - variant, exit code, timed_out, duration, /DumpResult presence+value,
                      whether repository parameters were passed
      verify.json   - (variant verify) SHA-256 of each listed source file vs the file
                      dumped from the MAIN configuration of the infobase
    Credentials are read from environment variables whose NAMES are passed; values never
    appear in command.txt, out.txt, meta.json or console output.
    The platform runs with closed stdin and is killed after -TimeoutSeconds.

    Variants:
      load-without-repo   /LoadConfigFromFiles -listFile -partial, no repository parameters
      load-with-repo      same + /ConfigurationRepositoryF/N/P
      update-without-repo /UpdateDBCfg
      update-with-repo    /UpdateDBCfg + /ConfigurationRepositoryF/N/P
      verify              /DumpConfigToFiles -listFile of the same objects, compares hashes
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet('load-without-repo','load-with-repo','update-without-repo','update-with-repo','verify')]
    [string]$Variant,
    [Parameter(Mandatory=$true)][string]$V8Path,
    [string]$InfoBaseServer, [string]$InfoBaseRef, [string]$InfoBasePath,
    [string]$IbUserEnv, [string]$IbPasswordEnv,
    [string]$RepositoryPath, [string]$RepoUserEnv, [string]$RepoPasswordEnv,
    [string]$ConfigDir, [string]$ListFile,
    [Parameter(Mandatory=$true)][string]$OutDir,
    [int]$TimeoutSeconds = 900,
    [switch]$TestCopyConfirmed
)
$ErrorActionPreference = 'Stop'
if (-not $TestCopyConfirmed) {
    Write-Host 'Refused: pass -TestCopyConfirmed only for a separate test copy with a test repository (never the pilot base).' -ForegroundColor Red
    exit 2
}
if (-not $InfoBasePath -and -not ($InfoBaseServer -and $InfoBaseRef)) { Write-Host 'Specify -InfoBasePath or -InfoBaseServer + -InfoBaseRef'; exit 2 }
$withRepo = $Variant -like '*-with-repo'
if ($withRepo -and (-not $RepositoryPath -or -not $RepoUserEnv)) { Write-Host 'with-repo variants need -RepositoryPath and -RepoUserEnv'; exit 2 }
if ($Variant -ne 'update-without-repo' -and $Variant -ne 'update-with-repo' -and (-not $ConfigDir -or -not $ListFile)) {
    Write-Host 'load/verify variants need -ConfigDir and -ListFile'; exit 2
}
if (Test-Path $V8Path -PathType Container) { $V8Path = Join-Path $V8Path '1cv8.exe' }
if (-not (Test-Path $V8Path)) { Write-Host "Platform not found: $V8Path"; exit 2 }

function Get-Secret([string]$name) {
    if (-not $name) { return '' }
    $v = [Environment]::GetEnvironmentVariable($name)
    if (-not $v) { Write-Host "Environment variable '$name' is empty"; exit 2 }
    return $v
}
$ibUser = Get-Secret $IbUserEnv; $ibPass = Get-Secret $IbPasswordEnv
$repoUser = if ($withRepo) { Get-Secret $RepoUserEnv } else { '' }
$repoPass = if ($withRepo) { Get-Secret $RepoPasswordEnv } else { '' }
$secrets = @($ibUser, $ibPass, $repoUser, $repoPass) | Where-Object { $_ }
function Hide([string]$text) {
    if ($null -eq $text) { return $text }
    foreach ($s in $secrets) { $text = $text.Replace($s, '***') }
    return $text
}
function Q([string]$v) { return '"' + $v.Replace('"', '""') + '"' }

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$dir = Join-Path $OutDir "$Variant-$stamp"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$work = Join-Path ([IO.Path]::GetTempPath()) "repo_probe_$(Get-Random)"
New-Item -ItemType Directory -Force -Path $work | Out-Null
$outFile = Join-Path $work 'out.txt'; $resultFile = Join-Path $work 'result.txt'

$a = @('DESIGNER')
if ($InfoBasePath) { $a += '/F' + (Q $InfoBasePath) } else { $a += '/S' + (Q "$InfoBaseServer/$InfoBaseRef") }
if ($ibUser) { $a += '/N' + (Q $ibUser) }
if ($ibPass) { $a += '/P' + (Q $ibPass) }
if ($withRepo) {
    $a += '/ConfigurationRepositoryF' + (Q $RepositoryPath)
    $a += '/ConfigurationRepositoryN' + (Q $repoUser)
    if ($repoPass) { $a += '/ConfigurationRepositoryP' + (Q $repoPass) }
}
$dumpDir = $null
switch -Wildcard ($Variant) {
    'load-*'   { $a += '/LoadConfigFromFiles', (Q $ConfigDir), '-listFile', (Q $ListFile), '-partial', '-updateConfigDumpInfo', '-Format', 'Hierarchical' }
    'update-*' { $a += '/UpdateDBCfg' }
    'verify'   { $dumpDir = Join-Path $work 'dump'; New-Item -ItemType Directory -Path $dumpDir | Out-Null
                 $a += '/DumpConfigToFiles', (Q $dumpDir), '-listFile', (Q $ListFile), '-Format', 'Hierarchical' }
}
$a += '/Out', (Q $outFile), '/DumpResult', (Q $resultFile), '/DisableStartupDialogs', '/DisableStartupMessages'
$line = $a -join ' '
[IO.File]::WriteAllText((Join-Path $dir 'command.txt'), (Hide "$V8Path $line"), (New-Object Text.UTF8Encoding($false)))
Write-Host "Running ($Variant): $(Hide $line)"

$psi = New-Object Diagnostics.ProcessStartInfo
$psi.FileName = $V8Path; $psi.Arguments = $line; $psi.UseShellExecute = $false
$psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
$sw = [Diagnostics.Stopwatch]::StartNew()
$p = [Diagnostics.Process]::Start($psi); $p.StandardInput.Close()
$o = $p.StandardOutput.ReadToEndAsync(); $e = $p.StandardError.ReadToEndAsync()
$timedOut = -not $p.WaitForExit($TimeoutSeconds * 1000)
if ($timedOut) { try { $p.Kill() } catch {} ; $p.WaitForExit() }
$sw.Stop()
$exit = if ($timedOut) { $null } else { $p.ExitCode }

$outText = if (Test-Path $outFile) { [IO.File]::ReadAllText($outFile) } else { $null }
if ($null -ne $outText) { [IO.File]::WriteAllText((Join-Path $dir 'out.txt'), (Hide $outText), (New-Object Text.UTF8Encoding($false))) }
$dumpText = if (Test-Path $resultFile) { [IO.File]::ReadAllText($resultFile) } else { $null }
if ($null -ne $dumpText) { [IO.File]::WriteAllText((Join-Path $dir 'dumpresult.txt'), $dumpText, (New-Object Text.UTF8Encoding($false))) }
$console = Hide (($o.Result) + ($e.Result))
if ($console) { [IO.File]::WriteAllText((Join-Path $dir 'console.txt'), $console, (New-Object Text.UTF8Encoding($false))) }

$verify = $null
if ($Variant -eq 'verify' -and -not $timedOut) {
    $verify = @()
    foreach ($rel in (Get-Content -LiteralPath $ListFile -Encoding UTF8 | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
        $src = Join-Path $ConfigDir $rel; $dst = Join-Path $dumpDir $rel
        $sh = if (Test-Path $src) { (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash } else { $null }
        $dh = if (Test-Path $dst) { (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash } else { $null }
        $verify += [pscustomobject]@{ file = $rel; source_sha256 = $sh; main_config_sha256 = $dh; equal = ($sh -and $sh -eq $dh) }
    }
    [IO.File]::WriteAllText((Join-Path $dir 'verify.json'), ($verify | ConvertTo-Json -Depth 3), (New-Object Text.UTF8Encoding($false)))
}
$dumpValue = $null
if ($null -ne $dumpText -and $dumpText.Trim() -match '^-?\d+') { $dumpValue = [int]$Matches[0] }
$meta = [ordered]@{
    variant = $Variant; started_utc = $stamp; duration_seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    exit_code = $exit; timed_out = $timedOut; timeout_seconds = $TimeoutSeconds
    dump_result_present = ($null -ne $dumpText); dump_result_value = $dumpValue
    out_present = ($null -ne $outText); repository_parameters_passed = $withRepo
    platform = (Split-Path (Split-Path (Split-Path $V8Path -Parent) -Parent) -Leaf)
    verify_all_equal = $(if ($verify) { -not ($verify | Where-Object { -not $_.equal }) } else { $null })
}
[IO.File]::WriteAllText((Join-Path $dir 'meta.json'), ($meta | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
Write-Host "Evidence: $dir"
$meta | ConvertTo-Json
