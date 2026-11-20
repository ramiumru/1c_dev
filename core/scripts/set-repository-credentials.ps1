# Native Windows dialog. Credentials never travel through agent input/output.
[CmdletBinding()]
param(
    [string]$UsernameEnv,
    [string]$PasswordEnv,
    [string]$DatabaseLabel,
    [string]$RepositoryAddress,
    [string]$StatusFile,
    [switch]$LibraryOnly
)
$ErrorActionPreference = 'Stop'

function Save-RepositoryUserCredentials {
    param([string]$LoginName, [string]$Secret, [string]$LoginVariable, [string]$SecretVariable)
    $previousLogin = [Environment]::GetEnvironmentVariable($LoginVariable, 'User')
    $previousSecret = [Environment]::GetEnvironmentVariable($SecretVariable, 'User')
    try {
        [Environment]::SetEnvironmentVariable($LoginVariable, $LoginName, 'User')
        [Environment]::SetEnvironmentVariable($SecretVariable, $Secret, 'User')
    } catch {
        try {
            [Environment]::SetEnvironmentVariable($LoginVariable, $previousLogin, 'User')
            [Environment]::SetEnvironmentVariable($SecretVariable, $previousSecret, 'User')
        } catch { throw 'Credential save and rollback failed.' }
        throw 'Credential save failed; previous values restored.'
    } finally {
        $previousLogin = $null
        $previousSecret = $null
    }
}

function New-RepositoryCredentialForm {
    param([string]$Database, [string]$Address)
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [Windows.Forms.Application]::EnableVisualStyles()
    $form = New-Object Windows.Forms.Form
    $form.Text = 'Доступ к хранилищу 1С'
    $form.ClientSize = New-Object Drawing.Size(580, 370)
    $form.Font = New-Object Drawing.Font('Segoe UI', 10)
    $form.FormBorderStyle = 'FixedDialog'
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.StartPosition = 'CenterScreen'
    $form.TopMost = $true
    $form.Tag = 'cancelled'
    $heading = New-Object Windows.Forms.Label
    $heading.Text = "База: $Database"
    $heading.SetBounds(20, 16, 540, 26)
    $form.Controls.Add($heading)
    $addressBox = New-Object Windows.Forms.TextBox
    $addressBox.Name = 'RepositoryAddress'
    $addressBox.Text = $Address
    $addressBox.ReadOnly = $true
    $addressBox.TabStop = $false
    $addressBox.SetBounds(20, 48, 540, 28)
    $form.Controls.Add($addressBox)
    $account = New-Object Windows.Forms.Label
    $account.Text = 'Пользователь Windows: ' + [Environment]::UserDomainName + '\' + [Environment]::UserName
    $account.SetBounds(20, 84, 540, 26)
    $form.Controls.Add($account)
    $loginLabel = New-Object Windows.Forms.Label
    $loginLabel.Text = 'Логин хранилища'
    $loginLabel.SetBounds(20, 124, 170, 26)
    $form.Controls.Add($loginLabel)
    $login = New-Object Windows.Forms.TextBox
    $login.Name = 'RepositoryLogin'
    $login.SetBounds(200, 120, 360, 28)
    $login.TabIndex = 0
    $form.Controls.Add($login)
    $secretLabel = New-Object Windows.Forms.Label
    $secretLabel.Text = 'Пароль'
    $secretLabel.SetBounds(20, 164, 170, 26)
    $form.Controls.Add($secretLabel)
    $secret = New-Object Windows.Forms.TextBox
    $secret.Name = 'RepositoryPassword'
    $secret.UseSystemPasswordChar = $true
    $secret.SetBounds(200, 160, 360, 28)
    $secret.TabIndex = 1
    $form.Controls.Add($secret)
    $notice = New-Object Windows.Forms.Label
    $notice.Text = "Сохранить на этом компьютере для текущего пользователя Windows.`nПароль не передаётся в чат. База и хранилище не изменяются."
    $notice.SetBounds(20, 210, 540, 48)
    $form.Controls.Add($notice)
    $errorLabel = New-Object Windows.Forms.Label
    $errorLabel.Name = 'ErrorMessage'
    $errorLabel.ForeColor = [Drawing.Color]::DarkRed
    $errorLabel.SetBounds(20, 266, 540, 42)
    $form.Controls.Add($errorLabel)
    $save = New-Object Windows.Forms.Button
    $save.Name = 'SaveCredentials'
    $save.Text = 'Сохранить'
    $save.SetBounds(300, 320, 125, 32)
    $save.TabIndex = 2
    $form.Controls.Add($save)
    $cancel = New-Object Windows.Forms.Button
    $cancel.Text = 'Отмена'
    $cancel.DialogResult = [Windows.Forms.DialogResult]::Cancel
    $cancel.SetBounds(435, 320, 125, 32)
    $cancel.TabIndex = 3
    $form.Controls.Add($cancel)
    $form.AcceptButton = $save
    $form.CancelButton = $cancel
    return $form
}

if ($LibraryOnly) { return }
$form = $null
$state = 'error'
try {
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::UserInteractive) { throw 'Interactive Windows desktop required.' }
    $reserved = @('PATH','HOME','USERPROFILE','SYSTEMROOT','WINDIR','TEMP','TMP','PSMODULEPATH','PYTHONPATH','PYTHONHOME','CODEX_HOME','PATHEXT','COMSPEC')
    foreach ($name in @($UsernameEnv, $PasswordEnv)) {
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$' -or $reserved -contains $name) { throw 'Invalid credential variable.' }
    }
    if ($UsernameEnv -eq $PasswordEnv) { throw 'Use different variable names.' }
    $form = New-RepositoryCredentialForm -Database $DatabaseLabel -Address $RepositoryAddress
    $form.Controls['RepositoryLogin'].Text = [Environment]::GetEnvironmentVariable($UsernameEnv, 'User')
    $form.Controls['SaveCredentials'].Add_Click({
        $loginText = $form.Controls['RepositoryLogin'].Text.Trim()
        $secretText = $form.Controls['RepositoryPassword'].Text
        if ([string]::IsNullOrWhiteSpace($loginText) -or [string]::IsNullOrEmpty($secretText)) {
            $form.Controls['ErrorMessage'].Text = 'Заполните логин и пароль.'
            return
        }
        try {
            Save-RepositoryUserCredentials -LoginName $loginText -Secret $secretText -LoginVariable $UsernameEnv -SecretVariable $PasswordEnv
            $form.Tag = 'saved'
            $form.Close()
        } catch {
            $form.Controls['ErrorMessage'].Text = 'Не удалось сохранить данные. Проверьте права текущего пользователя Windows.'
        } finally { $secretText = $null }
    })
    [void]$form.ShowDialog()
    $state = [string]$form.Tag
} catch {
    $state = 'error'
} finally {
    if ($form) { $form.Controls['RepositoryPassword'].Clear(); $form.Dispose() }
    if ($StatusFile) {
        $payload = '{"status":"' + $state + '"}'
        $temporary = $StatusFile + '.tmp'
        [IO.File]::WriteAllText($temporary, $payload, (New-Object Text.UTF8Encoding($false)))
        [IO.File]::Move($temporary, $StatusFile)
    }
}
