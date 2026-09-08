<#
.SYNOPSIS
    Установка агентской схемы 1C Dev под целевой AI-кодинг-инструмент.

.DESCRIPTION
    Собирает раскладку схемы из core/ + adapters/ в целевой каталог.
    Поддерживаемые инструменты: kilo, claude, codex, opencode.

.PARAMETER Tool
    Целевой инструмент: kilo | claude | codex | opencode

.PARAMETER Target
    Целевой каталог (по умолчанию ".").

.EXAMPLE
    .\install.ps1 -Tool kilo
    .\install.ps1 -Tool claude -Target C:\MyProject
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet("kilo","claude","codex","opencode")]
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
    opencode = @{
        skillDir = ".opencode/skills"
        agentDir = ".opencode/agents"
        contextDir = ".opencode/context"
        logsDir = ".opencode/logs"
        rootConfig = "opencode.json"
        instructionsInRoot = $false
        copySkills = $true
        copyAgents = $true
    }
    codex = @{
        skillDir = ""
        agentDir = ""
        contextDir = "context"
        logsDir = "logs"
        rootConfig = "AGENTS.md"
        instructionsInRoot = $false
        copySkills = $false
        copyAgents = $false
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
        $fm = [System.IO.File]::ReadAllText($fmPath, [System.Text.Encoding]::UTF8)
        # Подстановка путей в теле
        $body = $body -replace '\{\{CONTEXT_DIR\}\}', $ctx
        $body = $body -replace '\{\{LOGS_DIR\}\}', $logs
        $body = $body -replace '\{\{SKILLS_DIR\}\}', $skills
        $body = $body -replace '\{\{AGENTS_DIR\}\}', $agents
        # Сборка: ---\n<FM>\n---\n\n<body>
        $combined = "---`r`n$fm---`r`n`r`n$body"
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
    # Fallback: kilo.json.tpl, opencode.json.tpl
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

# --- Итог ---
Write-Host ""
Write-Host "=== Готово! Схема установлена для '$Tool' в '$Target'. ==="
Write-Host ""
Write-Host "Следующие шаги:"
Write-Host "  1. Создайте .v8-project.json из examples/v8-project.example.json"
Write-Host "  2. Разместите исходники конфигурации в projects/<источник>/src/"
Write-Host "  3. Первичный индекс: python scripts/build_summaries.py --project <имя> --scan --context-dir $ctx/projects"
