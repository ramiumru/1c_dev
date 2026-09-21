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

.PARAMETER OverlayPath
    Optional external overlay (private-слой поверх base install). Структура:
    agents/, skills/, rules/, context/, standards/, projects/, tool/<tool>/.
    Применяется ПОСЛЕ базовой установки: overlay-файл заменяет установленную
    копию (override wins), новый файл добавляется (add). Без параметра поведение
    идентично установке без overlay. См. docs/corporate-overlay-recommendations.md.

.PARAMETER OverlayDryRun
    Показать план overlay (add/override/skip) без записи файлов и манифеста.
    Требует -OverlayPath.

.EXAMPLE
    .\install.ps1 -Tool kilo
    .\install.ps1 -Tool claude -Target C:\MyProject
    .\install.ps1 -Tool kilo -Mode update
    .\install.ps1 -Tool kilo -Target C:\MyProject -OverlayPath C:\MyPrivateRepo\overlay
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet("kilo","claude","codex","openworks")]
    [string]$Tool,
    [string]$Target = ".",
    [ValidateSet("install","update")]
    [string]$Mode = "install",
    [switch]$Force,
    [string]$OverlayPath,
    [switch]$OverlayDryRun
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot   # корень репо
$utf8Bom = [System.Text.UTF8Encoding]::new($true)

if ($OverlayDryRun -and -not $OverlayPath) {
    throw "-OverlayDryRun требует -OverlayPath."
}

if (-not (Test-Path "$repo\core\agents")) {
    throw "core/agents не найден. Запускайте из корня репозитория."
}

# --- Конфигурация путей по инструменту ---
$config = @{
    kilo = @{ skillDir=".kilo/skills"; agentDir=".kilo/agent"; contextDir=".kilo/context"; logsDir=".kilo/logs"; rootConfig="kilo.json"; instructionsInRoot=$true; copySkills=$true; copyAgents=$true }
    claude = @{ skillDir=".claude/skills"; agentDir=".claude/agents"; contextDir=".claude/context"; logsDir=".claude/logs"; rootConfig="CLAUDE.md"; instructionsInRoot=$false; copySkills=$true; copyAgents=$true }
    openworks = @{ skillDir=".opencode/skills"; agentDir=".opencode/agents"; contextDir=".opencode/context"; logsDir=".opencode/logs"; rootConfig=""; instructionsInRoot=$false; copySkills=$true; copyAgents=$true }
    codex = @{ skillDir="skills"; agentDir="agents"; contextDir="context"; logsDir="logs"; rootConfig="AGENTS.md"; instructionsInRoot=$false; copySkills=$true; copyAgents=$true }
}[$Tool]

$ctx = $config.contextDir
$logs = $config.logsDir
$skills = $config.skillDir
$agents = $config.agentDir

# --- Защищённые файлы: никогда не перезаписывать без явного разрешения ---
$PROTECTED_ROOT_FILES = @(
    "AGENTS.md", "CLAUDE.md", "INSTRUCTIONS.md", "kilo.json", "openworks.json",
    ".ai-rules.json", ".dev.env", ".v8-project.json", "LICENSE", "specs/README.md"
)

# Файлы, которые LICENSE никогда не заменяется (P0-2.2)
$LICENSE_NEVER_OVERWRITE = @("LICENSE")

# --- External overlay: границы записи ---
# Никогда не пишется overlay (независимо от -Force)
$OVERLAY_NEVER_WRITE = @(
    "LICENSE", ".ai-rules.json", ".install-manifest-overlay.json",
    ".dev.env", ".v8-project.json"
)
# Корневые конфиги: override только с -Force (с бэкапом); add разрешён (если файла нет)
$OVERLAY_PROTECTED_ROOT = @(
    "AGENTS.md", "CLAUDE.md", "INSTRUCTIONS.md", "kilo.json", "openworks.json",
    "specs/README.md"
)

Write-Host "=== Установка 1c-dev для '$Tool' в '$Target' (mode: $Mode) ==="

# --- External overlay (-OverlayPath): фаза A — валидация ДО базовой установки ---
# Все фатальные проверки (path traversal, symlink/junction, absolute, .git) выполняются
# до первой записи в target. Фаза B (применение) — шаг 8, после base-манифеста.

# Overlay-хелперы (path traversal protection)
function Test-OverlayRelPath([string]$RelPath) {
    # Запрещены: абсолютные (drive/rooted), сегменты '..'/'.' и любые записи в .git
    if (-not $RelPath) { return $false }
    if ($RelPath -match '^[a-zA-Z]:') { return $false }
    if ($RelPath.StartsWith('\') -or $RelPath.StartsWith('/')) { return $false }
    $segments = $RelPath -split '[\\/]'
    foreach ($s in $segments) {
        if ($s -eq '' -or $s -eq '.' -or $s -eq '..') { return $false }
        if ($s -eq '.git') { return $false }
    }
    return $true
}

function Get-OverlayFiles([string]$Dir) {
    # Рекурсивный обход БЕЗ следования symlink/junction (path traversal protection)
    $result = @()
    foreach ($item in @(Get-ChildItem -LiteralPath $Dir -Force)) {
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "Overlay содержит symlink/junction (path traversal protection): $($item.FullName)"
        }
        if ($item.PSIsContainer) {
            $result += Get-OverlayFiles $item.FullName
        } else {
            $result += $item
        }
    }
    return $result
}

$overlayPlan = $null
$overlayRoot = ""
$overlayManifestPath = Join-Path $Target ".install-manifest-overlay.json"
$oldOverlayFiles = @()
$overlayManagedPaths = @{}

if (Test-Path $overlayManifestPath) {
    try {
        $oldOverlayManifest = Get-Content $overlayManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $oldOverlayFiles = @($oldOverlayManifest.files | Where-Object { $_ })
    } catch {
        $oldOverlayFiles = @()
        Write-Host "  overlay: .install-manifest-overlay.json не читается — overlay-файлы не отслеживаются"
    }
    if ($oldOverlayFiles.Count -gt 0 -and -not $OverlayPath) {
        throw "Обнаружен overlay-манифест предыдущей установки, но -OverlayPath не задан. Передайте -OverlayPath для повторного применения overlay; для полной очистки выполните update с пустым overlay (orphan-файлы будут удалены, base восстановлен), либо удалите .install-manifest-overlay.json вручную."
    }
    foreach ($of in $oldOverlayFiles) { $overlayManagedPaths[$of.path] = $true }
}

if ($OverlayPath) {
    if (-not (Test-Path -LiteralPath $OverlayPath -PathType Container)) {
        throw "OverlayPath не найден или не является каталогом: $OverlayPath"
    }
    $overlayRoot = (Get-Item -LiteralPath $OverlayPath).FullName
    if ((Get-Item -LiteralPath $overlayRoot).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "OverlayPath является symlink/junction: $OverlayPath — запрещено (path traversal protection)"
    }
    $targetRootFull = [System.IO.Path]::GetFullPath($Target).TrimEnd('\','/')

    # Категории overlay (порядок: generic → специализированные → tool/<tool>).
    # При конфликте двух категорий на один target-путь побеждает применённая позже.
    $overlayCategories = @(
        @{ src = "context";     dst = $ctx },
        @{ src = "standards";   dst = "$ctx/standards" },
        @{ src = "projects";    dst = "$ctx/projects" },
        @{ src = "rules";       dst = "$ctx/rules" },
        @{ src = "agents";      dst = $agents },
        @{ src = "skills";      dst = $skills },
        @{ src = "tool/$Tool";  dst = $null }   # tool/<tool>/** → <target>/**
    )

    $overlayPlan = @()
    foreach ($cat in $overlayCategories) {
        $catSrc = Join-Path $overlayRoot ($cat.src -replace '/','\')
        if (-not (Test-Path -LiteralPath $catSrc -PathType Container)) { continue }
        foreach ($sf in (Get-OverlayFiles $catSrc)) {
            $relSrc = $sf.FullName.Substring($catSrc.Length).TrimStart('\','/') -replace '\\','/'
            $relDst = if ($cat.dst) { "$($cat.dst)/$relSrc" } else { $relSrc }
            if (-not (Test-OverlayRelPath $relDst)) {
                throw "Overlay path отклонён (absolute/../.git): $($cat.src)/$relSrc"
            }
            $dstPath = [System.IO.Path]::GetFullPath((Join-Path $Target ($relDst -replace '/','\')))
            if (-not $dstPath.StartsWith($targetRootFull + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "Overlay target вне project root: $relDst"
            }
            $skillName = $null
            if ($cat.src -eq "skills") { $skillName = ($relSrc -split '/')[0] }
            $overlayPlan += @{ src = $sf.FullName; relSrc = "$($cat.src)/$relSrc"; relDst = $relDst; dst = $dstPath; skill = $skillName }
        }
    }
}

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
                # Overlay-managed пути исключаются: их обновляет overlay (шаг 8), не base
                if ($currentHash -and $currentHash -ne $f.installedHash -and -not $overlayManagedPaths.ContainsKey($f.path)) {
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
    Write-Host "[1/8] Скиллы -> $skills"
    $skillSrc = "$repo\core\skills"
    Get-ChildItem $skillSrc -Directory | ForEach-Object {
        $name = $_.Name
        $dst = Join-Path $Target "$skills/$name"
        # Копировать содержимое каталога, а не сам каталог (предотвращает nesting)
        if (Test-Path $dst) {
            Copy-Item "$($_.FullName)\*" $dst -Recurse -Force
        } else {
            Copy-Item $_.FullName $dst -Recurse -Force
        }
        Get-ChildItem $dst -Recurse -File | ForEach-Object {
            $content = [System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)
            if ($content -match '\{\{SKILL') {
                $content = $content -replace '\{\{SKILL_DIR\}\}', "$skills/$name"
                $content = $content -replace '\{\{SKILLS_DIR\}\}', "$skills"
                [System.IO.File]::WriteAllText($_.FullName, $content, $utf8Bom)
            }
        }
    }
} else { Write-Host "[1/8] Скиллы: пропуск" }

# --- 2. Агенты ---
if ($config.copyAgents) {
    Write-Host "[2/8] Агенты -> $agents"
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
} else { Write-Host "[2/8] Агенты: пропуск" }

# --- 3. Корневой конфиг ---
if ($config.rootConfig -and $config.rootConfig -ne "") {
    Write-Host "[3/8] Корневой конфиг -> $($config.rootConfig)"
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
} else {
    Write-Host "[3/8] Корневой конфиг: пропуск (rootConfig не задан)"
}

# --- 4. Контекст ---
Write-Host "[4/8] Контекст -> $ctx"
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
Write-Host "[5/8] On-demand правила -> $ctx/rules"
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

# Создать pilot-control/ каталог (read-only для агентов)
$pilotControlDir = Join-Path $Target "pilot-control"
if (-not (Test-Path $pilotControlDir)) {
    New-Item -ItemType Directory -Force -Path $pilotControlDir | Out-Null
    Write-Host "[5.5/8] pilot-control/ создан (для review.md, backup.md — read-only для агентов)"
}

# --- 6. .dev.env ---
Write-Host "[6/8] .dev.env -> параметры проекта"
$devEnvPath = Join-Path $Target ".dev.env"
if (Test-Path $devEnvPath) {
    Write-Host "[6/8] .dev.env уже существует — сохранён без изменений"
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
        Write-Host "[6/8] .dev.env создан $(if ($detectedVersion) {'(version=' + $detectedVersion + ')'})$(if ($detectedPath) {' (path autodetected)'})"
    } else { Write-Host "[6/8] .dev.env: шаблон не найден — пропуск" }
}

# --- 7. Скрипты + SDD + манифест ---
Write-Host "[7/8] Скрипты + SDD + манифест"
$scriptsDst = Join-Path $Target "scripts"
# Копировать содержимое каталога, а не сам каталог (предотвращает nesting: scripts/scripts/)
if (Test-Path $scriptsDst) {
    # Update: копировать пофайлово с перезаписью
    Get-ChildItem "$repo\core\scripts" -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring("$repo\core\scripts".Length).TrimStart('\','/') -replace '\\','/'
        $dstFile = Join-Path $scriptsDst $rel
        $dstDir = Split-Path $dstFile -Parent
        if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
        Copy-Item $_.FullName $dstFile -Force
    }
} else {
    # Install: копировать содержимое (не сам каталог)
    New-Item -ItemType Directory -Force -Path $scriptsDst | Out-Null
    Copy-Item "$repo\core\scripts\*" $scriptsDst -Recurse -Force
}

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
Safe-CopyFile "$repo\examples\project-context.example.md" (Join-Path $examplesDst "project-context.example.md") "examples/project-context.example.md"

# --- Манифест .ai-rules.json ---
Write-Host "[7/8] Генерация манифеста .ai-rules.json"
$manifestPath = Join-Path $Target ".ai-rules.json"
$utcNow = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$files = @()

# Перечисление из ИСТОЧНИКА (core/**): base-манифест отражает только public-файлы.
# Overlay-файлы (add/override) не попадают в base-манифест — их ведёт
# .install-manifest-overlay.json (шаг 8).
function Add-ManifestEntryFromSource($srcDir, $targetRelBase, $sourcePrefix) {
    if (-not (Test-Path -LiteralPath $srcDir)) { return }
    Get-ChildItem -LiteralPath $srcDir -File | ForEach-Object {
        $relTarget = "$targetRelBase/$($_.Name)" -replace '\\','/'
        $dstFile = Join-Path $Target ($relTarget -replace '/','\')
        if (Test-Path -LiteralPath $dstFile) {
            $hash = (Get-FileHash -LiteralPath $dstFile -Algorithm SHA256).Hash
            # $script:files — иначе += создаёт локальную копию и записи теряются
            $script:files += [PSCustomObject]@{ path=$relTarget; source="$sourcePrefix/$($_.Name)"; installedHash=$hash; userModified=$false }
        }
    }
}

# Агенты (*.md в core/agents)
Add-ManifestEntryFromSource "$repo\core\agents" $agents "core/agents"
# On-demand правила (*.md в core/rules)
Add-ManifestEntryFromSource "$repo\core\rules" "$ctx/rules" "core/rules"
# Контекст (*.md top-level в core/context)
Add-ManifestEntryFromSource "$repo\core\context" $ctx "core/context"
# Скрипты (*.py в core/scripts)
Add-ManifestEntryFromSource "$repo\core\scripts" "scripts" "core/scripts"
# Skills (по одному каталогу на скилл — из core/skills)
$skillsSrc = "$repo\core\skills"
if (Test-Path -LiteralPath $skillsSrc) {
    Get-ChildItem -LiteralPath $skillsSrc -Directory | ForEach-Object {
        $skillDir = $_.FullName
        $skillName = $_.Name
        Get-ChildItem -LiteralPath $skillDir -Recurse -File | ForEach-Object {
            $relInside = $_.FullName.Substring($skillDir.Length).TrimStart('\','/') -replace '\\','/'
            if (-not $relInside) { return }
            $relTarget = "$skills/$skillName/$relInside" -replace '\\','/'
            $dstFile = Join-Path $Target ($relTarget -replace '/','\')
            if (Test-Path -LiteralPath $dstFile) {
                $hash = (Get-FileHash -LiteralPath $dstFile -Algorithm SHA256).Hash
                $script:files += [PSCustomObject]@{ path=$relTarget; source="core/skills/$skillName/$relInside"; installedHash=$hash; userModified=$false }
            }
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
$projCtxExamplePath = Join-Path $Target "examples/project-context.example.md"
if (Test-Path $projCtxExamplePath) {
    $hash = (Get-FileHash -LiteralPath $projCtxExamplePath -Algorithm SHA256).Hash
    $files += [PSCustomObject]@{ path="examples/project-context.example.md"; source="examples/project-context.example.md"; installedHash=$hash; userModified=$false }
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

# --- 8. External overlay: фаза B — применение ПОСЛЕ базовой установки и base-манифеста ---
# Контракт: overlay > base installed copy (внутри target installation); public source не меняется.
$ovEntries = @()
$ovAdded = 0
$ovOverridden = 0
$ovSkipped = 0
$ovOrphans = 0
if ($OverlayPath) {
    Write-Host ""
    Write-Host "[8/8] External overlay -> $overlayRoot"
    foreach ($plan in $overlayPlan) {
        $relDst = $plan.relDst
        $dstPath = $plan.dst
        $exists = (Test-Path -LiteralPath $dstPath -PathType Leaf)
        # Никогда не пишется overlay (LICENSE, манифесты, данные проекта) — независимо от -Force
        if ($OVERLAY_NEVER_WRITE -contains $relDst) {
            Write-Host "    skip (never-write): $relDst"
            $ovSkipped++
            continue
        }
        # Защищённые корневые конфиги: override только с -Force (add разрешён)
        if ($exists -and ($OVERLAY_PROTECTED_ROOT -contains $relDst) -and -not $Force) {
            Write-Host "    skip (protected, нужен -Force): $relDst"
            $ovSkipped++
            continue
        }
        $op = "add"
        if ($exists) { $op = "override" }
        if ($OverlayDryRun) {
            Write-Host "    dry-run ${op}: $relDst"
            if ($op -eq "add") { $ovAdded++ } else { $ovOverridden++ }
            continue
        }
        $dstDir = Split-Path $dstPath -Parent
        if (-not (Test-Path -LiteralPath $dstDir)) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
        if ($exists -and $Force -and ($OVERLAY_PROTECTED_ROOT -contains $relDst)) {
            $bakPath = "$dstPath.bak"
            $bakIdx = 1
            while (Test-Path -LiteralPath $bakPath) { $bakPath = "$dstPath.bak$bakIdx"; $bakIdx++ }
            Copy-Item -LiteralPath $dstPath -Destination $bakPath -Force
            Write-Host "    backup: $relDst -> $(Split-Path $bakPath -Leaf)"
        }
        Copy-Item -LiteralPath $plan.src -Destination $dstPath -Force
        # Подстановка install-плейсхолдеров (как в base install) для текстовых файлов
        $ext = [System.IO.Path]::GetExtension($dstPath).ToLower()
        if (@(".md", ".json", ".jsonc", ".yml", ".yaml", ".toml", ".tpl") -contains $ext) {
            $content = [System.IO.File]::ReadAllText($dstPath, [System.Text.Encoding]::UTF8)
            if ($content -match '\{\{') {
                $content = $content -replace '\{\{CONTEXT_DIR\}\}', $ctx
                $content = $content -replace '\{\{LOGS_DIR\}\}', $logs
                $content = $content -replace '\{\{SKILLS_DIR\}\}', $skills
                $content = $content -replace '\{\{AGENTS_DIR\}\}', $agents
                if ($plan.skill) { $content = $content -replace '\{\{SKILL_DIR\}\}', "$skills/$($plan.skill)" }
                [System.IO.File]::WriteAllText($dstPath, $content, $utf8Bom)
            }
        }
        $hash = (Get-FileHash -LiteralPath $dstPath -Algorithm SHA256).Hash
        $ovEntries += [PSCustomObject]@{ path=$relDst; source=$plan.relSrc; operation=$op; installedHash=$hash }
        if ($op -eq "add") { $ovAdded++ } else { $ovOverridden++; Write-Host "    override: $relDst" }
    }

    # Orphan cleanup: файлы старого overlay, отсутствующие в новом.
    # base-манифест ($files) уже обновлён на шаге 7: override-осиротевшие пути,
    # которыми владеет base, не трогаем (base их уже обновил).
    if (-not $OverlayDryRun -and $oldOverlayFiles.Count -gt 0) {
        $newOverlayPaths = @{}
        foreach ($e in $ovEntries) { $newOverlayPaths[$e.path] = $true }
        $basePaths = @{}
        foreach ($f in $files) { $basePaths[$f.path] = $true }
        foreach ($old in $oldOverlayFiles) {
            if ($newOverlayPaths.ContainsKey($old.path) -or $basePaths.ContainsKey($old.path)) { continue }
            $orphanPath = Join-Path $Target ($old.path -replace '/','\')
            if (-not (Test-Path -LiteralPath $orphanPath -PathType Leaf)) { continue }
            $curHash = (Get-FileHash -LiteralPath $orphanPath -Algorithm SHA256).Hash
            if ($curHash -eq $old.installedHash) {
                Remove-Item -LiteralPath $orphanPath -Force
                $ovOrphans++
                Write-Host "    orphan removed: $($old.path)"
            } else {
                Write-Host "    orphan kept (изменён после установки): $($old.path)"
            }
        }
    }

    # Overlay-манифест .install-manifest-overlay.json (содержимое файлов НЕ хранится)
    if (-not $OverlayDryRun) {
        $ovInstalledAt = $utcNow
        if ($Mode -eq "update" -and (Test-Path $overlayManifestPath)) {
            try {
                $oldOvManifest = Get-Content $overlayManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($oldOvManifest.installedAt) { $ovInstalledAt = $oldOvManifest.installedAt }
            } catch {}
        }
        $ovManifest = [PSCustomObject]@{
            protocolVersion = "1.0"
            tool = $Tool
            overlayPath = $overlayRoot
            installedAt = $ovInstalledAt
            updatedAt = $utcNow
            files = $ovEntries
        }
        [System.IO.File]::WriteAllText($overlayManifestPath, ($ovManifest | ConvertTo-Json -Depth 4), $utf8Bom)
    }

    Write-Host ""
    Write-Host "Overlay:"
    Write-Host "  added: $ovAdded"
    Write-Host "  overridden: $ovOverridden"
    Write-Host "  skipped: $ovSkipped"
    if ($ovOrphans -gt 0) { Write-Host "  orphans removed: $ovOrphans" }
    if ($OverlayDryRun) { Write-Host "  (dry-run: записи не выполнялись, манифест не обновлён)" }
}

Write-Host ""
Write-Host "=== Готово! Схема установлена для '$Tool' в '$Target'. ==="
Write-Host ""
Write-Host "Следующие шаги:"
Write-Host "  1. Создайте .v8-project.json из examples/v8-project.example.json"
Write-Host "  2. Разместите исходники конфигурации в projects/<источник>/src/"
Write-Host "  3. Первичный индекс: python scripts/build_summaries.py --project <имя> --scan --context-dir $ctx/projects"
Write-Host "  4. Проверка: python scripts/doctor.py && python scripts/validate.py"
