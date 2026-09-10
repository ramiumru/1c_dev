<#
.SYNOPSIS
    Установка агентской схемы 1C Dev под целевой AI-кодинг-инструмент.

.DESCRIPTION
    Собирает раскладку схемы из core/ + adapters/ в целевой каталог.
    Поддерживаемые инструменты: kilo, claude, codex, openworks.

.PARAMETER Tool
    Целевой инструмент: kilo | claude | codex | openworks

.PARAMETER Target
    Целевой каталог (по умолчанию ".").

.EXAMPLE
    .\install.ps1 -Tool kilo
    .\install.ps1 -Tool claude -Target C:\MyProject
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet("kilo","claude","codex","openworks")]
    [string]$Tool,
    [string]$Target = ".",
    [ValidateSet("install","update")]
    [string]$Mode = "install",
    [switch]$Force
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot   # корень репо (temp/1c_dev)
$utf8Bom = [System.Text.UTF8Encoding]::new($true)

# --- Проверка core/ ---
if (-not (Test-Path "$repo\core\agents")) {
    throw "core/agents не найден. Запускайте из корня репозитория 1c-dev."
}

# --- Конфигурация путей по инструменту ---
$config = @{
    kilo = @{
        skillDir = ".kilo/skills"
        agentDir = ".kilo/agent"
        contextDir = ".kilo/context"
        logsDir = ".kilo/logs"
        rootConfig = "kilo.json"
        instructionsInRoot = $true
        copySkills = $true
        copyAgents = $true
    }
    claude = @{
        skillDir = ".claude/skills"
        agentDir = ".claude/agents"
        contextDir = ".claude/context"
        logsDir = ".claude/logs"
        rootConfig = "CLAUDE.md"
        instructionsInRoot = $false
        copySkills = $true
        copyAgents = $true
    }
    openworks = @{
        skillDir = ".openworks/skills"
        agentDir = ".openworks/agents"
        contextDir = ".openworks/context"
        logsDir = ".openworks/logs"
        rootConfig = "openworks.json"
        instructionsInRoot = $false
        copySkills = $true
        copyAgents = $true
    }
    codex = @{
        skillDir = "skills"
        agentDir = "agents"
        contextDir = "context"
        logsDir = "logs"
        rootConfig = "AGENTS.md"
        instructionsInRoot = $false
        copySkills = $true
        copyAgents = $true
    }
}[$Tool]

$ctx = $config.contextDir
$logs = $config.logsDir
$skills = $config.skillDir
$agents = $config.agentDir

Write-Host "=== Установка 1c-dev для '$Tool' в '$Target' (mode: $Mode) ==="

# --- Чтение манифеста для update-режима ---
$existingManifest = $null
$userModifiedFiles = @{}
if ($Mode -eq "update") {
    $manifestPath = Join-Path $Target ".ai-rules.json"
    if (Test-Path $manifestPath) {
        try {
            $existingManifest = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($f in $existingManifest.files) {
                $currentHash = (Get-FileHash -LiteralPath (Join-Path $Target $f.path) -Algorithm SHA256 -ErrorAction SilentlyContinue).Hash
                if ($currentHash -and $currentHash -ne $f.installedHash) {
                    $userModifiedFiles[$f.path] = $true
                }
            }
            Write-Host "  update: обнаружено $($userModifiedFiles.Count) user-modified файлов (будут сохранены)"
        } catch {
            Write-Host "  update: .ai-rules.json не читается — полная установка"
        }
    } else {
        Write-Host "  update: .ai-rules.json не найден — полная установка"
    }
}

# Вспомогательная функция: проверить, нужно ли перезаписать файл в update-режиме
function Should-Overwrite($relPath) {
    if ($script:Mode -ne "update") { return $true }
    if ($script:Force) { return $true }
    if ($script:userModifiedFiles.ContainsKey($relPath)) {
        Write-Host "    skip (user-modified): $relPath"
        return $false
    }
    return $true
}

# --- 1. Скиллы ---
if ($config.copySkills) {
    Write-Host "[1/7] Скиллы -> $skills"
    $skillSrc = "$repo\core\skills"
    Get-ChildItem $skillSrc -Directory | ForEach-Object {
        $name = $_.Name
        $dst = Join-Path $Target "$skills/$name"
        Copy-Item $_.FullName $dst -Recurse -Force
        # Подстановка плейсхолдеров
        Get-ChildItem $dst -Recurse -File | ForEach-Object {
            $content = [System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)
            if ($content -match '\{\{SKILL') {
                $content = $content -replace '\{\{SKILL_DIR\}\}', "$skills/$name"
                $content = $content -replace '\{\{SKILLS_DIR\}\}', "$skills"
                [System.IO.File]::WriteAllText($_.FullName, $content, $utf8Bom)
            }
        }
    }
} else {
    Write-Host "[1/7] Скиллы: пропуск (одноагентный режим)"
}

# --- 2. Агенты ---
if ($config.copyAgents) {
    Write-Host "[2/7] Агенты -> $agents"
    $agentSrc = "$repo\core\agents"
    $fmSrc = "$repo\adapters\$Tool\frontmatter"
    Get-ChildItem $agentSrc -Filter "*.md" | ForEach-Object {
        $name = $_.BaseName
        $fmPath = "$fmSrc\$name.yml"
        $body = [System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)
        # Подстановка путей в теле
        $body = $body -replace '\{\{CONTEXT_DIR\}\}', $ctx
        $body = $body -replace '\{\{LOGS_DIR\}\}', $logs
        $body = $body -replace '\{\{SKILLS_DIR\}\}', $skills
        $body = $body -replace '\{\{AGENTS_DIR\}\}', $agents
        if (Test-Path $fmPath) {
            $fm = [System.IO.File]::ReadAllText($fmPath, [System.Text.Encoding]::UTF8)
            # Defense-in-depth: удалить существующие --- delimiters, чтобы избежать двойного frontmatter (8.9)
            $fm = $fm -replace '(?s)^\s*---\s*\r?\n', ''
            $fm = $fm -replace '(?s)\r?\n\s*---\s*$', ''
            $fm = $fm.TrimEnd("`r", "`n")
            # Сборка: ---\n<FM>\n---\n\n<body>
            $combined = "---`r`n$fm`r`n---`r`n`r`n$body"
        } else {
            # Нет frontmatter (напр. Codex одноагентный режим) — тело как reference-док
            $combined = $body
        }
        $dstPath = Join-Path $Target "$agents/$name.md"
        $relPath = "$agents/$name.md" -replace '\\','/'
        if (-not (Should-Overwrite $relPath)) { continue }
        $dstDir = Split-Path $dstPath -Parent
        New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
        [System.IO.File]::WriteAllText($dstPath, $combined, $utf8Bom)
    }
} else {
    Write-Host "[2/7] Агенты: пропуск (одноагентный режим)"
}

# --- 3. Корневой конфиг ---
    Write-Host "[3/7] Корневой конфиг -> $($config.rootConfig)"
$tplPath = "$repo\adapters\$Tool\$($config.rootConfig).tpl"
if (-not (Test-Path $tplPath)) {
    # Fallback: kilo.json.tpl, openworks.json.tpl
    $tpls = Get-ChildItem "$repo\adapters\$Tool" -Filter "*.tpl" -ErrorAction SilentlyContinue
    if ($tpls) { $tplPath = $tpls[0].FullName }
}
if (Test-Path $tplPath) {
    $tplContent = [System.IO.File]::ReadAllText($tplPath, [System.Text.Encoding]::UTF8)
    $dstPath = Join-Path $Target $config.rootConfig
    [System.IO.File]::WriteAllText($dstPath, $tplContent, $utf8Bom)
}

# --- 4. Контекст ---
    Write-Host "[4/7] Контекст -> $ctx"
$ctxSrc = "$repo\core\context"
$ctxDst = Join-Path $Target $ctx
Copy-Item $ctxSrc $ctxDst -Recurse -Force

# Подстановка плейсхолдеров путей в контекст-файлах (INSTRUCTIONS.md, BslChecklists.md,
# requirements-README.md и др.) — они используют {{CONTEXT_DIR}}/{{LOGS_DIR}}/
# {{SKILLS_DIR}}/{{AGENTS_DIR}}, как и тела агентов.
Get-ChildItem $ctxDst -Recurse -File -Filter "*.md" | ForEach-Object {
    $content = [System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)
    if ($content -match '\{\{') {
        $content = $content -replace '\{\{CONTEXT_DIR\}\}', $ctx
        $content = $content -replace '\{\{LOGS_DIR\}\}', $logs
        $content = $content -replace '\{\{SKILLS_DIR\}\}', $skills
        $content = $content -replace '\{\{AGENTS_DIR\}\}', $agents
        [System.IO.File]::WriteAllText($_.FullName, $content, $utf8Bom)
    }
}

# Для kilo: INSTRUCTIONS.md + BslChecklists.md в корень (kilo.json instructions: ["INSTRUCTIONS.md"])
if ($config.instructionsInRoot) {
    Copy-Item "$ctxDst\INSTRUCTIONS.md" (Join-Path $Target "INSTRUCTIONS.md") -Force
    Copy-Item "$ctxDst\BslChecklists.md" (Join-Path $Target "AGENTS.md") -Force
}

# --- 5. On-demand правила ---
Write-Host "[5/7] On-demand правила -> $ctx/rules"
$rulesSrc = "$repo\core\rules"
$rulesDst = Join-Path $Target "$ctx/rules"
if (Test-Path $rulesSrc) {
    Copy-Item $rulesSrc $rulesDst -Recurse -Force
    # Подстановка плейсхолдеров путей в rules/*.md
    Get-ChildItem $rulesDst -Recurse -File -Filter "*.md" | ForEach-Object {
        $content = [System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)
        if ($content -match '\{\{') {
            $content = $content -replace '\{\{CONTEXT_DIR\}\}', $ctx
            $content = $content -replace '\{\{LOGS_DIR\}\}', $logs
            $content = $content -replace '\{\{SKILLS_DIR\}\}', $skills
            $content = $content -replace '\{\{AGENTS_DIR\}\}', $agents
            [System.IO.File]::WriteAllText($_.FullName, $content, $utf8Bom)
        }
    }
} else {
    Write-Host "[5/7] On-demand правила: core/rules/ не найден — пропуск"
}

# Копировать AGENT-INSTALL.md и LICENSE в корень (для agent-first UX)
if (Test-Path "$repo\AGENT-INSTALL.md") {
    Copy-Item "$repo\AGENT-INSTALL.md" (Join-Path $Target "AGENT-INSTALL.md") -Force
}
if (Test-Path "$repo\LICENSE") {
    Copy-Item "$repo\LICENSE" (Join-Path $Target "LICENSE") -Force
}

# --- 6. .dev.env (параметры проекта) ---
Write-Host "[6/7] .dev.env -> параметры проекта"
$devEnvPath = Join-Path $Target ".dev.env"
if (Test-Path $devEnvPath) {
    Write-Host "[6/7] .dev.env уже существует — сохранён без изменений"
} else {
    $envExample = "$repo\core\context\.dev.env.example"
    if (Test-Path $envExample) {
        $envContent = [System.IO.File]::ReadAllText($envExample, [System.Text.Encoding]::UTF8)
        # Автоопределение PLATFORM_VERSION из Configuration.xml
        $configXml = Join-Path $Target "projects\*\src\Configuration.xml"
        $configExtXml = Join-Path $Target "projects\*\src\ConfigurationExtension.xml"
        $detectedVersion = ""
        if (Test-Path $configXml) {
            $xmlContent = [System.IO.File]::ReadAllText((Get-ChildItem $configXml | Select-Object -First 1).FullName, [System.Text.Encoding]::UTF8)
            if ($xmlContent -match 'CompatibilityMode\s*>\s*([\d.]+)') { $detectedVersion = $matches[1] }
        } elseif (Test-Path $configExtXml) {
            $xmlContent = [System.IO.File]::ReadAllText((Get-ChildItem $configExtXml | Select-Object -First 1).FullName, [System.Text.Encoding]::UTF8)
            if ($xmlContent -match 'CompatibilityMode\s*>\s*([\d.]+)') { $detectedVersion = $matches[1] }
        }
        if ($detectedVersion) {
            $envContent = $envContent -replace 'PLATFORM_VERSION=8.3.27', "PLATFORM_VERSION=$detectedVersion"
        }
        # Автоопределение PLATFORM_PATH — скан C:\Program Files\1cv8\
        $detectedPath = ""
        $v8Dirs = @("C:\Program Files\1cv8", "C:\Program Files (x86)\1cv8")
        foreach ($baseDir in $v8Dirs) {
            if (Test-Path $baseDir) {
                $candidates = Get-ChildItem $baseDir -Directory | Sort-Object Name -Descending
                foreach ($cand in $candidates) {
                    $exePath = Join-Path $cand.FullName "bin\1cv8.exe"
                    if (Test-Path $exePath) { $detectedPath = $exePath; break }
                }
            }
            if ($detectedPath) { break }
        }
        if ($detectedPath) {
            $envContent = $envContent -replace 'PLATFORM_PATH=', "PLATFORM_PATH=$detectedPath"
        }
        # Автоопределение PREFIX из ConfigurationExtension.xml (NamePrefix)
        $detectedPrefix = ""
        if (Test-Path $configExtXml) {
            $extFile = (Get-ChildItem $configExtXml | Select-Object -First 1).FullName
            $xmlContent = [System.IO.File]::ReadAllText($extFile, [System.Text.Encoding]::UTF8)
            if ($xmlContent -match 'NamePrefix\s*>\s*([A-Za-zА-Яа-яЁё_]+)') { $detectedPrefix = $matches[1] + "_" }
        }
        if ($detectedPrefix) {
            $envContent = $envContent -replace 'PREFIX=', "PREFIX=$detectedPrefix"
        }
        $envContent = $envContent -replace "`r`n", "`r`n"  # ensure CRLF
        [System.IO.File]::WriteAllText($devEnvPath, $envContent, [System.Text.Encoding]::UTF8)
        Write-Host "[6/7] .dev.env создан (автоопределение: $(if ($detectedVersion) {'version=' + $detectedVersion + ' '})$(if ($detectedPath) {'path=' + $detectedPath + ' '})$(if ($detectedPrefix) {'prefix=' + $detectedPrefix}))"
    } else {
        Write-Host "[6/7] .dev.env: шаблон не найден — пропуск"
    }
}

# --- 7. Скрипты + SDD ---
Write-Host "[7/7] Скрипты + SDD + манифест"
$scriptsDst = Join-Path $Target "scripts"
Copy-Item "$repo\core\scripts" $scriptsDst -Recurse -Force

$specsDst = Join-Path $Target "specs"
New-Item -ItemType Directory -Force -Path $specsDst | Out-Null
Copy-Item "$repo\core\sdd\README.md" (Join-Path $specsDst "README.md") -Force

# Подстановка плейсхолдеров путей в specs/README.md (использует {{CONTEXT_DIR}} и др.)
$specReadme = Join-Path $specsDst "README.md"
if (Test-Path $specReadme) {
    $content = [System.IO.File]::ReadAllText($specReadme, [System.Text.Encoding]::UTF8)
    if ($content -match '\{\{') {
        $content = $content -replace '\{\{CONTEXT_DIR\}\}', $ctx
        $content = $content -replace '\{\{LOGS_DIR\}\}', $logs
        $content = $content -replace '\{\{SKILLS_DIR\}\}', $skills
        $content = $content -replace '\{\{AGENTS_DIR\}\}', $agents
        [System.IO.File]::WriteAllText($specReadme, $content, $utf8Bom)
    }
}

# Пример реестра баз (безопасный, без секретов)
$examplesDst = Join-Path $Target "examples"
New-Item -ItemType Directory -Force -Path $examplesDst | Out-Null
Copy-Item "$repo\examples\v8-project.example.json" (Join-Path $examplesDst "v8-project.example.json") -Force

# --- Манифест .ai-rules.json ---
Write-Host "[7/7] Генерация манифеста .ai-rules.json"
$manifestPath = Join-Path $Target ".ai-rules.json"
$utcNow = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$targetFull = (Get-Item $Target).FullName.TrimEnd('\','/')

# Сбор списка размещённых файлов с sha256 hash
$files = @()

# Агенты
$agentDirFull = Join-Path $Target $agents
if (Test-Path $agentDirFull) {
    Get-ChildItem $agentDirFull -Filter "*.md" | ForEach-Object {
        $rel = $_.FullName.Substring($targetFull.Length).TrimStart('\','/') -replace '\\','/'
        $srcName = $_.BaseName
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        $files += [PSCustomObject]@{ path = $rel; source = "core/agents/$srcName.md"; installedHash = $hash; userModified = $false }
    }
}

# On-demand правила
$rulesDirFull = Join-Path $Target "$ctx/rules"
if (Test-Path $rulesDirFull) {
    Get-ChildItem $rulesDirFull -Filter "*.md" | ForEach-Object {
        $rel = $_.FullName.Substring($targetFull.Length).TrimStart('\','/') -replace '\\','/'
        $srcName = $_.BaseName
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        $files += [PSCustomObject]@{ path = $rel -replace '\\','/'; source = "core/rules/$srcName.md"; installedHash = $hash; userModified = $false }
    }
}

# Контекст (*.md в context/)
$ctxDirFull = Join-Path $Target $ctx
if (Test-Path $ctxDirFull) {
    Get-ChildItem $ctxDirFull -Filter "*.md" | ForEach-Object {
        $rel = $_.FullName.Substring($targetFull.Length).TrimStart('\','/') -replace '\\','/'
        $srcName = $_.BaseName
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        $files += [PSCustomObject]@{ path = $rel; source = "core/context/$srcName.md"; installedHash = $hash; userModified = $false }
    }
}

# Скрипты (*.py)
$scriptsDirFull = Join-Path $Target "scripts"
if (Test-Path $scriptsDirFull) {
    Get-ChildItem $scriptsDirFull -Filter "*.py" | ForEach-Object {
        $rel = $_.FullName.Substring($targetFull.Length).TrimStart('\','/') -replace '\\','/'
        $srcName = $_.BaseName
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        $files += [PSCustomObject]@{ path = $rel; source = "core/scripts/$srcName.py"; installedHash = $hash; userModified = $false }
    }
}

# specs/README.md
$specsReadmePath = Join-Path $Target "specs/README.md"
if (Test-Path $specsReadmePath) {
    $hash = (Get-FileHash -LiteralPath $specsReadmePath -Algorithm SHA256).Hash
    $files += [PSCustomObject]@{ path = "specs/README.md"; source = "core/sdd/README.md"; installedHash = $hash; userModified = $false }
}

# examples/v8-project.example.json
$examplePath = Join-Path $Target "examples/v8-project.example.json"
if (Test-Path $examplePath) {
    $hash = (Get-FileHash -LiteralPath $examplePath -Algorithm SHA256).Hash
    $files += [PSCustomObject]@{ path = "examples/v8-project.example.json"; source = "examples/v8-project.example.json"; installedHash = $hash; userModified = $false }
}

# Корневой конфиг
$rootConfigPath = Join-Path $Target $config.rootConfig
if (Test-Path $rootConfigPath) {
    $hash = (Get-FileHash -LiteralPath $rootConfigPath -Algorithm SHA256).Hash
    $files += [PSCustomObject]@{ path = $config.rootConfig; source = "adapters/$Tool/$($config.rootConfig).tpl"; installedHash = $hash; userModified = $false }
}

# .dev.env
if (Test-Path $devEnvPath) {
    $hash = (Get-FileHash -LiteralPath $devEnvPath -Algorithm SHA256).Hash
    $files += [PSCustomObject]@{ path = ".dev.env"; source = "core/context/.dev.env.example"; installedHash = $hash; userModified = $false }
}

# AGENT-INSTALL.md
$agentInstallPath = Join-Path $Target "AGENT-INSTALL.md"
if (Test-Path $agentInstallPath) {
    $hash = (Get-FileHash -LiteralPath $agentInstallPath -Algorithm SHA256).Hash
    $files += [PSCustomObject]@{ path = "AGENT-INSTALL.md"; source = "AGENT-INSTALL.md"; installedHash = $hash; userModified = $false }
}

# LICENSE
$licensePath = Join-Path $Target "LICENSE"
if (Test-Path $licensePath) {
    $hash = (Get-FileHash -LiteralPath $licensePath -Algorithm SHA256).Hash
    $files += [PSCustomObject]@{ path = "LICENSE"; source = "LICENSE"; installedHash = $hash; userModified = $false }
}

# Сериализация манифеста
$manifest = [PSCustomObject]@{
    protocolVersion = "1.0"
    tool = $Tool
    installedAt = $utcNow
    updatedAt = $utcNow
    files = $files
}
$manifestJson = $manifest | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText($manifestPath, $manifestJson, $utf8Bom)

# --- Итог ---
Write-Host ""
Write-Host "=== Готово! Схема установлена для '$Tool' в '$Target'. ==="
Write-Host ""
Write-Host "Следующие шаги:"
Write-Host "  1. Создайте .v8-project.json из examples/v8-project.example.json"
Write-Host "  2. Разместите исходники конфигурации в projects/<источник>/src/"
Write-Host "  3. Первичный индекс: python scripts/build_summaries.py --project <имя> --scan --context-dir $ctx/projects"
Write-Host "  4. Проверка: python scripts/doctor.py && python scripts/validate.py"
