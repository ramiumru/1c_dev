# Example external overlay

Универсальный dummy-пример структуры external overlay для `install.ps1 -OverlayPath`.
Все значения вымышленные; реальный corporate overlay живёт в private-репозитории и
в публичный репозиторий не попадает.

Структура и контракт — `docs/corporate-overlay-recommendations.md`.

```text
examples/overlay/
├── agents/     → <agentDir>/                 (add/override агентов)
├── rules/      → <contextDir>/rules/         (add/override on-demand правил)
├── context/    → <contextDir>/              (generic: любые файлы контекста)
├── standards/  → <contextDir>/standards/     (add/override стандартов)
├── projects/   → <contextDir>/projects/     (per-project контекст)
└── tool/
    └── kilo/  → <target root>/              (tool-specific config, только для -Tool kilo)
```

Применение:

```powershell
.\install\install.ps1 -Tool kilo -Target C:\MyProject -OverlayPath .\examples\overlay
```