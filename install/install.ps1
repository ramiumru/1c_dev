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
    [string]$Target = "."
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

Write-Host "=== Установка 1c-dev для '$Tool' в '$Target' ==="

# --- 1. Скиллы ---
if ($config.copySkills) {
    Write-Host "[1/5] Скиллы -> $skills"
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
    Write-Host "[1/5] Скиллы: пропуск (одноагентный режим)"
}

# --- 2. Агенты ---
if ($config.copyAgents) {
    Write-Host "[2/5] Агенты -> $agents"
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
        $dstDir = Split-Path $dstPath -Parent
        New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
        [System.IO.File]::WriteAllText($dstPath, $combined, $utf8Bom)
    }
} else {
    Write-Host "[2/5] Агенты: пропуск (одноагентный режим)"
}

# --- 3. Корневой конфиг ---
Write-Host "[3/5] Корневой конфиг -> $($config.rootConfig)"
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
Write-Host "[4/5] Контекст -> $ctx"
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

# --- 5. Скрипты + SDD ---
Write-Host "[5/5] Скрипты + SDD"
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

# --- Итог ---
Write-Host ""
Write-Host "=== Готово! Схема установлена для '$Tool' в '$Target'. ==="
Write-Host ""
Write-Host "Следующие шаги:"
Write-Host "  1. Создайте .v8-project.json из examples/v8-project.example.json"
Write-Host "  2. Разместите исходники конфигурации в projects/<источник>/src/"
Write-Host "  3. Первичный индекс: python scripts/build_summaries.py --project <имя> --scan --context-dir $ctx/projects"
