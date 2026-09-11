# Corporate overlay: инструкция по подключению

## Назначение

Корпоративный overlay — закрытый каталог, содержащий стандарты разработки и per-project
контексты, которые не входят в публичную поставку harness.

## Архитектура

```
<private-overlay>/
├── standards/
│   └── standards.md          — корпоративные стандарты разработки
└── projects/
    └── <project>/
        ├── context.md         — per-project контекст
        ├── objects-index.md   — индекс объектов проекта
        └── analyst-scope.md   — справочный список объектов
```

## Подключение через установщик

```powershell
.\install.ps1 -Tool kilo -Target . -OverlayPath C:\path\to\overlay
```

Установщик копирует overlay в runtime-context выбранного адаптера:
- `standards/standards.md` → `<context>/standards/standards.md`
- `projects/<project>/` → `<context>/projects/<project>/`

Без `-OverlayPath` ставится только нейтральное публичное ядро с шаблонами.

## Требования

- Overlay не копируется в публичный репозиторий.
- Overlay устанавливается только в runtime-context выбранного адаптера.
- Исходный внешний overlay не изменяется.
- Путь проверяется (symlink/path escape блокируется).
- Содержимое overlay не выводится в лог.
- Отсутствие overlay не является ошибкой для универсальной установки.
- `doctor --pilot` требует `standards.md` в corporate-ready режиме.

## Нейтральный пример

См. `examples/overlay.example/` — содержит вымышленные значения для тестирования.
