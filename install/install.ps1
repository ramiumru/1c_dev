<#
.SYNOPSIS
    Установка агентской схемы 1C Dev под целевой AI-кодинг-инструмент.

.DESCRIPTION
    Собирает раскладку схемы из core/ + adapters/ в целевой каталог.
    Поддерживаемые инструменты: kilo, claude, codex, openworks.

    Безопасность установки (P0-2):
    - Корневые пользовательские файлы (AGENTS.md, CLAUDE.md, INSTRUCTIONS.md, kilo.json,
      openworks.json, .ai-rules.json, .dev.env, LICENSE, specs/README.md) НЕ перезаписываются
      без явного разрешения (-Force или подтверждение в install-режиме).
    - LICENSE целевого проекта НИКОГДА не заменяется лицензией harness.
    - При update: изменённые пользователем файлы (hash расходится) сохраняются.
    - При -Force: перезапись разрешена, но LICENSE целевого проекта всё равно не трогается.
    - Перед разрешённой заменой создаётся резервная копия с уникальным именем.

.PARAMETER Tool
    Целевой инструмент: kilo | claude | codex | openworks

.PARAMETER Target
    Целевой каталог (по умолчанию ".").

.PARAMETER Mode
    install | update

.PARAMETER Force
    Принудительная перезапись (кроме LICENSE целевого проекта).

.EXAMPLE
    .\install.ps1 -Tool kilo
    .\install.ps1 -Tool claude -Target C:\MyProject
    .\install.ps1 -Tool kilo -Mode update
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
$repo = Split-Path -Parent $PSScriptRoot   # корень репо
$utf8Bom = [System.Text.UTF8Encoding]::new($true)

if (-not (Test-Path "$repo\core\agents")) {
    throw "core/agents не найден. Запускайте из корня репозитория."
}

# --- Конфигурация путей по инструменту ---
$config = @{
    kilo = @{ skillDir=".kilo/skills"; agentDir=".kilo/agent"; contextDir=".kilo/context"; logsDir=".kilo/logs"; rootConfig="kilo.json"; instructionsInRoot=$true; copySkills=$true; copyAgents=$true }
    claude = @{ skillDir=".claude/skills"; agentDir=".claude/agents"; contextDir=".claude/context"; logsDir=".claude/logs"; rootConfig="CLAUDE.md"; instructionsInRoot=$false; copySkills=$true; copyAgents=$true }
    openworks = @{ skillDir=".openworks/skills"; agentDir=".openworks/agents"; contextDir=".openworks/context"; logsDir=".openworks/logs"; rootConfig="openworks.json"; instructionsInRoot=$false; copySkills=$true; copyAgents=$true }
    codex = @{ skillDir="skills"; agentDir="agents"; contextDir="context"; logsDir="logs"; rootConfig="AGENTS.md"; instructionsInRoot=$false; copySkills=$true; copyAgents=$true }
}[$Tool]

$ctx = $config.contextDir
$logs = $config.logsDir
$skills = $config.skillDir
$agents = $config.agentDir

# --- Защищённые файлы: никогда не перезаписывать без явного разрешения ---
$PROTECTED_ROOT_FILES = @(
    "AGENTS.md", "CLAUDE.md", "INSTRUCTIONS.md", "kilo.json", "openworks.json",
    ".ai-rules.json", ".dev.env", "LICENSE", "specs/README.md"
)

# Файлы, которые LICENSE никогда не заменяется (P0-2.2)
$LICENSE_NEVER_OVERWRITE = @("LICENSE")

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
                $fp = Join-Path $Target $f.path
                $currentHash = (Get-FileHash -LiteralPath $fp -Algorithm SHA256 -ErrorAction SilentlyContinue).Hash
                if ($currentHash -and $currentHash -ne $f.installedHash) {
                    $userModifiedFiles[$f.path] = $true
                }
            }
            Write-Host "  update: обнаружено $($userModifiedFiles.Count) user-modified файлов (будут сохранены)"
        } catch {
            Write-Host "  update: .ai-rules.json не читается — полная установка (манифест повреждён)"
        }
    } else {
        Write-Host "  update: .ai-rules.json не найден — полная установка"
    }
}

# --- Вспомогательные функции ---

function Test-ShouldOverwrite($relPath) {
    # LICENSE целевого проекта — НИКОГДА не перезаписывается (P0-2.2)
    if ($LICENSE_NEVER_OVERWRITE -contains $relPath) {
        return $false
    }
    # Защищённые корневые файлы в install-режиме — не перезаписывать без -Force
    if ($script:Mode -eq "install" -and -not $script:Force) {
        if ($PROTECTED_ROOT_FILES -contains $relPath) {
            $fullPath = Join-Path $script:Target $relPath
            if (Test-Path $fullPath) {
                Write-Host "    skip (protected, exists): $relPath"
                return $false
            }
        }
    }
    # update-режим: user-modified файлы сохраняются без -Force
    if ($script:Mode -eq "update" -and -not $script:Force) {
        if ($script:userModifiedFiles.ContainsKey($relPath)) {
            Write-Host "    skip (user-modified): $relPath"
            return $false
        }
    }
    return $true
}

function Safe-CopyFile($srcPath, $dstPath, $relPath) {
    # Проверка: нужно ли перезаписывать
    if (-not (Test-ShouldOverwrite $relPath)) {
        # Если файл существует и мы его не перезаписываем — создаём резервную копию при -Force
        if ($script:Force -and (Test-Path $dstPath) -and ($LICENSE_NEVER_OVERWRITE -notcontains $relPath)) {
            $bakPath = "$dstPath.bak"
            $bakIdx = 1
            while (Test-Path $bakPath) { $bakPath = "$dstPath.bak$bakIdx"; $bakIdx++ }
            Copy-Item $dstPath $bakPath -Force
            Write-Host "    backup: $relPath -> $(Split-Path $bakPath -Leaf)"
            Copy-Item $srcPath $dstPath -Force
        }
        return
    }
    $dstDir = Split-Path $dstPath -Parent
    if ($dstDir -and -not (Test-Path $dstDir)) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
    Copy-Item $srcPath $dstPath -Force
}

# --- 1. Скиллы ---
if ($config.copySkills) {
    Write-Host "[1/7] Скиллы -> $skills"
    $skillSrc = "$repo\core\skills"
    Get-ChildItem $skillSrc -Directory | ForEach-Object {
        $name = $_.Name
        $dst = Join-Path $Target "$skills/$name"
        Copy-Item $_.FullName $dst -Recurse -Force
        Get-ChildItem $dst -Recurse -File | ForEach-Object {
            $content = [System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)
            if ($content -match '\{\{SKILL') {
                $content = $content -replace '\{\{SKILL_DIR\}\}', "$skills/$name"
                $content = $content -replace '\{\{SKILLS_DIR\}\}', "$skills"
                [System.IO.File]::WriteAllText($_.FullName, $content, $utf8Bom)
            }
        }
    }
} else { Write-Host "[1/7] Скиллы: пропуск" }

# --- 2. Агенты ---
if ($config.copyAgents) {
    Write-Host "[2/7] Агенты -> $agents"
    $agentSrc = "$repo\core\agents"
    $fmSrc = "$repo\adapters\$Tool\frontmatter"
    Get-ChildItem $agentSrc -Filter "*.md" | ForEach-Object {
        $name = $_.BaseName
        $fmPath = "$fmSrc\$name.yml"
        $body = [System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)
        $body = $body -replace '\{\{CONTEXT_DIR\}\}', $ctx
        $body = $body -replace '\{\{LOGS_DIR\}\}', $logs
        $body = $body -replace '\{\{SKILLS_DIR\}\}', $skills
        $body = $body -replace '\{\{AGENTS_DIR\}\}', $agents
        if (Test-Path $fmPath) {
            $fm = [System.IO.File]::ReadAllText($fmPath, [System.Text.Encoding]::UTF8)
            $fm = $fm -replace '(?s)^\s*---\s*\r?\n', ''
            $fm = $fm -replace '(?s)\r?\n\s*---\s*$', ''
            $fm = $fm.TrimEnd("`r", "`n")
            $combined = "---`r`n$fm`r`n---`r`n`r`n$body"
        } else { $combined = $body }
        $dstPath = Join-Path $Target "$agents/$name.md"
        $relPath = "$agents/$name.md" -replace '\\','/'
        if (-not (Test-ShouldOverwrite $relPath)) { return }
        $dstDir = Split-Path $dstPath -Parent
        New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
        [System.IO.File]::WriteAllText($dstPath, $combined, $utf8Bom)
    }
} else { Write-Host "[2/7] Агенты: пропуск" }

# --- 3. Корневой конфиг ---
Write-Host "[3/7] Корневой конфиг -> $($config.rootConfig)"
$tplPath = "$repo\adapters\$Tool\$($config.rootConfig).tpl"
if (-not (Test-Path $tplPath)) {
    $tpls = Get-ChildItem "$repo\adapters\$Tool" -Filter "*.tpl" -ErrorAction SilentlyContinue
    if ($tpls) { $tplPath = $tpls[0].FullName }
}
if (Test-Path $tplPath) {
    $tplContent = [System.IO.File]::ReadAllText($tplPath, [System.Text.Encoding]::UTF8)
    $dstPath = Join-Path $Target $config.rootConfig
    Safe-CopyFile $tplPath $dstPath $config.rootConfig
}

# --- 4. Контекст ---
Write-Host "[4/7] Контекст -> $ctx"
$ctxSrc = "$repo\core\context"
$ctxDst = Join-Path $Target $ctx
# При update: не перезаписывать context-файлы целиком, а копировать пофайлово с защитой
if ($Mode -eq "update") {
    Get-ChildItem $ctxSrc -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($ctxSrc.Length).TrimStart('\','/') -replace '\\','/'
        $dstFile = Join-Path $ctxDst $rel
        $relPath = "$ctx/$rel" -replace '\\','/'
        if (Test-ShouldOverwrite $relPath) {
            $dstDir = Split-Path $dstFile -Parent
            if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
            Copy-Item $_.FullName $dstFile -Force
        }
    }
} else {
    Copy-Item $ctxSrc $ctxDst -Recurse -Force
}
# Подстановка плейсхолдеров
Get-ChildItem $ctxDst -Recurse -File -Filter "*.md" -ErrorAction SilentlyContinue | ForEach-Object {
    $content = [System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)
    if ($content -match '\{\{') {
        $content = $content -replace '\{\{CONTEXT_DIR\}\}', $ctx
        $content = $content -replace '\{\{LOGS_DIR\}\}', $logs
        $content = $content -replace '\{\{SKILLS_DIR\}\}', $skills
        $content = $content -replace '\{\{AGENTS_DIR\}\}', $agents
        [System.IO.File]::WriteAllText($_.FullName, $content, $utf8Bom)
    }
}
if ($config.instructionsInRoot) {
    $instrSrc = "$ctxDst\INSTRUCTIONS.md"
    $instrDst = Join-Path $Target "INSTRUCTIONS.md"
    if (Test-Path $instrSrc) { Safe-CopyFile $instrSrc $instrDst "INSTRUCTIONS.md" }
    $bslSrc = "$ctxDst\BslChecklists.md"
    $bslDst = Join-Path $Target "AGENTS.md"
    if (Test-Path $bslSrc) { Safe-CopyFile $bslSrc $bslDst "AGENTS.md" }
}

# --- 5. On-demand правила ---
Write-Host "[5/7] On-demand правила -> $ctx/rules"
$rulesSrc = "$repo\core\rules"
$rulesDst = Join-Path $Target "$ctx/rules"
if (Test-Path $rulesSrc) {
    if ($Mode -eq "update") {
        Get-ChildItem $rulesSrc -Recurse -File | ForEach-Object {
            $rel = $_.FullName.Substring($rulesSrc.Length).TrimStart('\','/') -replace '\\','/'
            $dstFile = Join-Path $rulesDst $rel
            $relPath = "$ctx/rules/$rel" -replace '\\','/'
            if (Test-ShouldOverwrite $relPath) {
                $dstDir = Split-Path $dstFile -Parent
                if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
                Copy-Item $_.FullName $dstFile -Force
            }
        }
    } else {
        Copy-Item $rulesSrc $rulesDst -Recurse -Force
    }
    Get-ChildItem $rulesDst -Recurse -File -Filter "*.md" -ErrorAction SilentlyContinue | ForEach-Object {
        $content = [System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)
        if ($content -match '\{\{') {
            $content = $content -replace '\{\{CONTEXT_DIR\}\}', $ctx
            $content = $content -replace '\{\{LOGS_DIR\}\}', $logs
            $content = $content -replace '\{\{SKILLS_DIR\}\}', $skills
            $content = $content -replace '\{\{AGENTS_DIR\}\}', $agents
            [System.IO.File]::WriteAllText($_.FullName, $content, $utf8Bom)
        }
    }
}

# Копировать AGENT-INSTALL.md (но НЕ LICENSE — P0-2.2)
if (Test-Path "$repo\AGENT-INSTALL.md") {
    Safe-CopyFile "$repo\AGENT-INSTALL.md" (Join-Path $Target "AGENT-INSTALL.md") "AGENT-INSTALL.md"
}
# LICENSE harness НЕ копируется в корень целевого проекта (P0-2.2)
# Сведения о лицензии harness — в NOTICE.md/THIRD_PARTY_LICENSES.md (копируются с контекстом)

# --- 6. .dev.env ---
Write-Host "[6/7] .dev.env -> параметры проекта"
$devEnvPath = Join-Path $Target ".dev.env"
if (Test-Path $devEnvPath) {
    Write-Host "[6/7] .dev.env уже существует — сохранён без изменений"
} else {
    $envExample = "$repo\core\context\.dev.env.example"
    if (Test-Path $envExample) {
        $envContent = [System.IO.File]::ReadAllText($envExample, [System.Text.Encoding]::UTF8)
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
        if ($detectedVersion) { $envContent = $envContent -replace 'PLATFORM_VERSION=8.3.27', "PLATFORM_VERSION=$detectedVersion" }
        $detectedPath = ""
        foreach ($baseDir in @("C:\Program Files\1cv8", "C:\Program Files (x86)\1cv8")) {
            if (Test-Path $baseDir) {
                $candidates = Get-ChildItem $baseDir -Directory | Sort-Object Name -Descending
                foreach ($cand in $candidates) {
                    $exePath = Join-Path $cand.FullName "bin\1cv8.exe"
                    if (Test-Path $exePath) { $detectedPath = $exePath; break }
                }
            }
            if ($detectedPath) { break }
        }
        if ($detectedPath) { $envContent = $envContent -replace 'PLATFORM_PATH=', "PLATFORM_PATH=$detectedPath" }
        $detectedPrefix = ""
        if (Test-Path $configExtXml) {
            $extFile = (Get-ChildItem $configExtXml | Select-Object -First 1).FullName
            $xmlContent = [System.IO.File]::ReadAllText($extFile, [System.Text.Encoding]::UTF8)
            if ($xmlContent -match 'NamePrefix\s*>\s*([A-Za-zА-Яа-яЁё_]+)') { $detectedPrefix = $matches[1] + "_" }
        }
        if ($detectedPrefix) { $envContent = $envContent -replace 'PREFIX=', "PREFIX=$detectedPrefix" }
        [System.IO.File]::WriteAllText($devEnvPath, $envContent, [System.Text.Encoding]::UTF8)
        Write-Host "[6/7] .dev.env создан $(if ($detectedVersion) {'(version=' + $detectedVersion + ')'})$(if ($detectedPath) {' (path autodetected)'})"
    } else { Write-Host "[6/7] .dev.env: шаблон не найден — пропуск" }
}

# --- 7. Скрипты + SDD + манифест ---
Write-Host "[7/7] Скрипты + SDD + манифест"
$scriptsDst = Join-Path $Target "scripts"
Copy-Item "$repo\core\scripts" $scriptsDst -Recurse -Force

$specsDst = Join-Path $Target "specs"
New-Item -ItemType Directory -Force -Path $specsDst | Out-Null
$specsReadmeSrc = "$repo\core\sdd\README.md"
$specsReadmeDst = Join-Path $specsDst "README.md"
Safe-CopyFile $specsReadmeSrc $specsReadmeDst "specs/README.md"
# Подстановка плейсхолдеров
if (Test-Path $specsReadmeDst) {
    $content = [System.IO.File]::ReadAllText($specsReadmeDst, [System.Text.Encoding]::UTF8)
    if ($content -match '\{\{') {
        $content = $content -replace '\{\{CONTEXT_DIR\}\}', $ctx
        $content = $content -replace '\{\{LOGS_DIR\}\}', $logs
        $content = $content -replace '\{\{SKILLS_DIR\}\}', $skills
        $content = $content -replace '\{\{AGENTS_DIR\}\}', $agents
        [System.IO.File]::WriteAllText($specsReadmeDst, $content, $utf8Bom)
    }
}

$examplesDst = Join-Path $Target "examples"
New-Item -ItemType Directory -Force -Path $examplesDst | Out-Null
Safe-CopyFile "$repo\examples\v8-project.example.json" (Join-Path $examplesDst "v8-project.example.json") "examples/v8-project.example.json"

# --- Манифест .ai-rules.json ---
Write-Host "[7/7] Генерация манифеста .ai-rules.json"
$manifestPath = Join-Path $Target ".ai-rules.json"
$utcNow = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$targetFull = (Get-Item $Target).FullName.TrimEnd('\','/')
$files = @()

function Add-ManifestEntry($dirPath, $filter, $sourcePrefix) {
    if (Test-Path $dirPath) {
        Get-ChildItem $dirPath -Filter $filter | ForEach-Object {
            $rel = $_.FullName.Substring($script:targetFull.Length).TrimStart('\','/') -replace '\\','/'
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
            $files += [PSCustomObject]@{ path=$rel; source="$sourcePrefix/$($_.BaseName)$($_.Extension)"; installedHash=$hash; userModified=$false }
        }
    }
}

# Агенты
Add-ManifestEntry (Join-Path $Target $agents) "*.md" "core/agents"
# On-demand правила
Add-ManifestEntry (Join-Path $Target "$ctx/rules") "*.md" "core/rules"
# Контекст (*.md в context/)
Add-ManifestEntry (Join-Path $Target $ctx) "*.md" "core/context"
# Скрипты (*.py)
Add-ManifestEntry (Join-Path $Target "scripts") "*.py" "core/scripts"
# Skills (по одному каталогу на скилл)
$skillsDirFull = Join-Path $Target $skills
if (Test-Path $skillsDirFull) {
    Get-ChildItem $skillsDirFull -Directory | ForEach-Object {
        $skillName = $_.Name
        $rel = "$skills/$skillName" -replace '\\','/'
        $skillFiles = Get-ChildItem $_.FullName -Recurse -File
        foreach ($sf in $skillFiles) {
            $sfRel = $sf.FullName.Substring($targetFull.Length).TrimStart('\','/') -replace '\\','/'
            $hash = (Get-FileHash -LiteralPath $sf.FullName -Algorithm SHA256).Hash
            $files += [PSCustomObject]@{ path=$sfRel; source="core/skills/$skillName/$($sf.Name)"; installedHash=$hash; userModified=$false }
        }
    }
}

# specs/README.md
$specsReadmePath = Join-Path $Target "specs/README.md"
if (Test-Path $specsReadmePath) {
    $hash = (Get-FileHash -LiteralPath $specsReadmePath -Algorithm SHA256).Hash
    $files += [PSCustomObject]@{ path="specs/README.md"; source="core/sdd/README.md"; installedHash=$hash; userModified=$false }
}
# examples
$examplePath = Join-Path $Target "examples/v8-project.example.json"
if (Test-Path $examplePath) {
    $hash = (Get-FileHash -LiteralPath $examplePath -Algorithm SHA256).Hash
    $files += [PSCustomObject]@{ path="examples/v8-project.example.json"; source="examples/v8-project.example.json"; installedHash=$hash; userModified=$false }
}
# Корневой конфиг
$rootConfigPath = Join-Path $Target $config.rootConfig
if (Test-Path $rootConfigPath) {
    $hash = (Get-FileHash -LiteralPath $rootConfigPath -Algorithm SHA256).Hash
    $files += [PSCustomObject]@{ path=$config.rootConfig; source="adapters/$Tool/$($config.rootConfig).tpl"; installedHash=$hash; userModified=$false }
}
# .dev.env
if (Test-Path $devEnvPath) {
    $hash = (Get-FileHash -LiteralPath $devEnvPath -Algorithm SHA256).Hash
    $files += [PSCustomObject]@{ path=".dev.env"; source="core/context/.dev.env.example"; installedHash=$hash; userModified=$false }
}
# AGENT-INSTALL.md
$aiPath = Join-Path $Target "AGENT-INSTALL.md"
if (Test-Path $aiPath) {
    $hash = (Get-FileHash -LiteralPath $aiPath -Algorithm SHA256).Hash
    $files += [PSCustomObject]@{ path="AGENT-INSTALL.md"; source="AGENT-INSTALL.md"; installedHash=$hash; userModified=$false }
}

# Сериализация манифеста (не перезаписывать существующий без -Force в update-режиме)
$manifest = [PSCustomObject]@{
    protocolVersion = "1.0"
    tool = $Tool
    installedAt = $utcNow
    updatedAt = $utcNow
    files = $files
}
if ($Mode -eq "update" -and -not $Force -and (Test-Path $manifestPath)) {
    # Сохраняем installedAt, обновляем только updatedAt
    try {
        $oldManifest = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $manifest.installedAt = $oldManifest.installedAt
    } catch {}
}
$manifestJson = $manifest | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText($manifestPath, $manifestJson, $utf8Bom)

Write-Host ""
Write-Host "=== Готово! Схема установлена для '$Tool' в '$Target'. ==="
Write-Host ""
Write-Host "Следующие шаги:"
Write-Host "  1. Создайте .v8-project.json из examples/v8-project.example.json"
Write-Host "  2. Разместите исходники конфигурации в projects/<источник>/src/"
Write-Host "  3. Первичный индекс: python scripts/build_summaries.py --project <имя> --scan --context-dir $ctx/projects"
Write-Host "  4. Проверка: python scripts/doctor.py && python scripts/validate.py"
