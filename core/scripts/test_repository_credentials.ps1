# Native form construction and isolated credential round trip. No live 1C.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'set-repository-credentials.ps1') -LibraryOnly
$form = New-RepositoryCredentialForm -Database 'test-dev' -Address 'https://example.invalid/repository/dev'
try {
    if (-not $form.Controls['RepositoryPassword'].UseSystemPasswordChar) { throw 'Password is not masked.' }
    if ($form.Controls['RepositoryPassword'].Text -ne '') { throw 'Password must start empty.' }
    if (-not $form.Controls['RepositoryAddress'].ReadOnly) { throw 'Address must be read-only.' }
    if ($form.Tag -ne 'cancelled') { throw 'Closing the window must default to cancellation.' }
    if ($form.Controls['SaveCredentials'].Text -ne 'Сохранить') { throw 'Save button missing.' }
    $form.CreateControl()
    $form.Show()
    [Windows.Forms.Application]::DoEvents()
    $image = New-Object Drawing.Bitmap($form.Width, $form.Height)
    try {
        $form.DrawToBitmap($image, (New-Object Drawing.Rectangle(0, 0, $form.Width, $form.Height)))
        if ($env:RUNNER_TEMP) { $image.Save((Join-Path $env:RUNNER_TEMP 'repository-credentials-preview.png'), [Drawing.Imaging.ImageFormat]::Png) }
    } finally { $image.Dispose() }
} finally { $form.Dispose() }
$prefix = 'HARNESS_TEST_' + [Guid]::NewGuid().ToString('N')
$loginVar = $prefix + '_USER'
$secretVar = $prefix + '_PASSWORD'
try {
    Save-RepositoryUserCredentials -LoginName 'fixture-user' -Secret 'fixture%PASSWORD%' -LoginVariable $loginVar -SecretVariable $secretVar
    if ([Environment]::GetEnvironmentVariable($loginVar, 'User') -ne 'fixture-user') { throw 'User storage failed.' }
    if ([Environment]::GetEnvironmentVariable($secretVar, 'User') -cne 'fixture%PASSWORD%') { throw 'Password was transformed.' }
} finally {
    [Environment]::SetEnvironmentVariable($loginVar, $null, 'User')
    [Environment]::SetEnvironmentVariable($secretVar, $null, 'User')
}
Write-Output 'Native credential form and isolated User environment round trip: PASS'
