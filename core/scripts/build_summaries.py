#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Генерация summary по объектам контура (per-project).

Что делает:
- читает per-project индекс .kilo/context/objects-index/<project>.md;
- извлекает объекты и пути;
- читает XML/BSL только из указанных путей;
- формирует markdown summary;
- сохраняет в .kilo/context/summaries/<project>/.

Режимы:
1. Без LLM:
   python scripts/build_summaries.py --project finance

2. Через LiteLLM/OpenAI-compatible API:
   export LITELLM_API_BASE="http://localhost:4000/v1"
   export LITELLM_API_KEY="..."
   export LITELLM_MODEL="claude-3-5-haiku"
   python scripts/build_summaries.py --project finance --use-llm

Важно:
- скрипт не изменяет projects/**;
- summaries являются вспомогательным индексом;
- источником истины остаются XML/BSL-файлы конфигурации.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import textwrap
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]

# Default: Kilo layout (.kilo/context/projects). Override with --context-dir.
PROJECTS_CONTEXT_DIR = ROOT / ".kilo" / "context" / "projects"
# Per-project структура:
#   .kilo/context/projects/<project>/context.md       — per-project контекст (домен, Источники)
#   .kilo/context/projects/<project>/objects-index.md — индекс объектов проекта
#   .kilo/context/projects/<project>/summaries/       — summaries (+ .cache.json)
#   .kilo/context/projects/<project>/requirements/    — per-project требования
#   .kilo/context/projects/<project>/analyst-scope.md — финанс-специфичный справочник (для finance)
# Проект = 1..N источников (основа + расширения). Маппинг source→project — из раздела
# «Источники» в context.md (см. build_source_to_project_map). Пока расширений нет — 1:1.
# Пути вывода summaries/кэша/индекса вычисляются из PROJECTS_CONTEXT_DIR (аргумент --out удалён).

# Английский тип метаданных 1С (как в XML подсистем, напр. Catalog) → русский
# префикс заголовка в objects-index (напр. Справочник).
EN_TO_RU_TYPE = {
    "Catalog": "Справочник",
    "Document": "Документ",
    "Enum": "Перечисление",
    "Constant": "Константа",
    "CommonModule": "ОбщийМодуль",
    "EventSubscription": "ПодпискаНаСобытие",
    "InformationRegister": "РегистрСведений",
    "AccumulationRegister": "РегистрНакопления",
    "AccountingRegister": "РегистрБухгалтерии",
    "CalculationRegister": "РегистрРасчёта",
    "ChartOfAccounts": "ПланСчетов",
    "ChartOfCharacteristicTypes": "ПланВидовХарактеристик",
    "ChartOfCalculationTypes": "ПланВидовРасчета",
    "ExchangePlan": "ПланОбмена",
    "BusinessProcess": "БизнесПроцесс",
    "Task": "Задача",
    "Report": "Отчет",
    "DataProcessor": "Обработка",
    "Role": "Роль",
    "DocumentJournal": "ЖурналДокументов",
    "CommonCommand": "ОбщаяКоманда",
    "CommonForm": "ОбщаяФорма",
    "CommonTemplate": "ОбщийМакет",
    "SettingsStorage": "ХранилищеНастроек",
    "ScheduledJob": "РегламентноеЗадание",
    "HTTPService": "HTTPСервис",
    "WebService": "WebСервис",
    "Subsystem": "Подсистема",
    "Sequence": "Последовательность",
    "CommandGroup": "ГруппаКоманд",
    "CommonAttribute": "ОбщийРеквизит",
    "CommonPicture": "ОбщаяКартинка",
    "DefinedType": "ОпределяемыйТип",
    "FilterCriterion": "КритерийОтбора",
    "FunctionalOption": "ФункциональнаяОпция",
    "FunctionalOptionParameter": "ПараметрФункциональнойОпции",
    "IntegrationService": "СервисИнтеграции",
    "SessionParameter": "ПараметрСеанса",
    "StyleItem": "ЭлементСтиля",
    "WSReference": "WSСсылка",
    "XDTOPackage": "ПакетXDTO",
    "ExternalDataSource": "ВнешнийИсточникДанных",
}

# Имя папки типа в projects/<проект>/src/ (напр. Catalogs) → английский тип (Catalog).
FOLDER_TO_EN_TYPE = {
    "Catalogs": "Catalog",
    "Documents": "Document",
    "Enums": "Enum",
    "Constants": "Constant",
    "CommonModules": "CommonModule",
    "EventSubscriptions": "EventSubscription",
    "InformationRegisters": "InformationRegister",
    "AccumulationRegisters": "AccumulationRegister",
    "AccountingRegisters": "AccountingRegister",
    "CalculationRegisters": "CalculationRegister",
    "ChartsOfAccounts": "ChartOfAccounts",
    "ChartsOfCharacteristicTypes": "ChartOfCharacteristicTypes",
    "ChartsOfCalculationTypes": "ChartOfCalculationTypes",
    "ExchangePlans": "ExchangePlan",
    "BusinessProcesses": "BusinessProcess",
    "Tasks": "Task",
    "Reports": "Report",
    "DataProcessors": "DataProcessor",
    "Roles": "Role",
    "DocumentJournals": "DocumentJournal",
    "CommonCommands": "CommonCommand",
    "CommonForms": "CommonForm",
    "CommonTemplates": "CommonTemplate",
    "SettingsStorages": "SettingsStorage",
    "ScheduledJobs": "ScheduledJob",
    "HTTPServices": "HTTPService",
    "WebServices": "WebService",
    "Subsystems": "Subsystem",
    "Sequences": "Sequence",
    "CommandGroups": "CommandGroup",
    "CommonAttributes": "CommonAttribute",
    "CommonPictures": "CommonPicture",
    "DefinedTypes": "DefinedType",
    "FilterCriteria": "FilterCriterion",
    "FunctionalOptions": "FunctionalOption",
    "FunctionalOptionsParameters": "FunctionalOptionParameter",
    "IntegrationServices": "IntegrationService",
    "SessionParameters": "SessionParameter",
    "StyleItems": "StyleItem",
    "WSReferences": "WSReference",
    "XDTOPackages": "XDTOPackage",
    "ExternalDataSources": "ExternalDataSource",
}

# Обратные маппинги для целевого --scan по каноническим заголовкам 'РусТип.Имя'.
RU_TO_EN_TYPE = {ru: en for en, ru in EN_TO_RU_TYPE.items()}
RU_TYPE_TO_FOLDER = {EN_TO_RU_TYPE[en]: folder for folder, en in FOLDER_TO_EN_TYPE.items()}

# Полный --scan — dry-run (без --objects): только отчёт о кандидатах без
# записи в индекс, чтобы избежать массового засорения кураторского индекса.
# Вывод ограничен этим числом; добавление — только через --scan --objects.
FULL_SCAN_REPORT_LIMIT = 200


@dataclass
class IndexedObject:
    title: str
    path: Path
    purpose: str = ""
    what_to_check: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    project: str = ""


_SOURCE_MAP: Optional[dict] = None  # кэш source→project, строится лениво


def _ensure_source_map() -> dict:
    """Лениво строит и кэширует маппинг source→project."""
    global _SOURCE_MAP
    if _SOURCE_MAP is None:
        _SOURCE_MAP = build_source_to_project_map()
    return _SOURCE_MAP


def project_of(path: Path) -> str:
    """Логический проект из пути вида projects/<source>/src/...
    Источник (source) — папка под projects/; проект — логическая единица, которой
    принадлежит источник (основа + расширения). Маппинг source→project — из раздела
    «Источники» в projects/<proj>/context.md (см. build_source_to_project_map).
    Fallback: если источник не найден в маппинге, но есть projects/<source>/context.md —
    source=project (identity). Иначе 'unknown'."""
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return "unknown"
    parts = rel.parts
    if "projects" not in parts:
        return "unknown"
    i = parts.index("projects")
    if i + 1 >= len(parts):
        return "unknown"
    source = parts[i + 1]
    if source == "unknown":
        return "unknown"
    smap = _ensure_source_map()
    if source in smap:
        return smap[source]
    # Fallback identity: источник сам является проектом (нет расширений)
    if (PROJECTS_CONTEXT_DIR / source / "context.md").exists():
        return source
    return "unknown"


@dataclass
class ObjectContext:
    obj: IndexedObject
    xml_files: list[Path]
    bsl_files: list[Path]
    xml_text: str
    bsl_text: str
    procedures: list[str]
    functions: list[str]
    file_hash: str


def read_text_safe(path: Path, max_chars: Optional[int] = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        try:
            text = path.read_text(encoding="cp1251", errors="ignore")
        except Exception:
            return ""

    if max_chars and len(text) > max_chars:
        return text[:max_chars] + "\n\n... [truncated] ..."
    return text


def objects_index_path(project: str) -> Path:
    """Per-project файл индекса: .kilo/context/projects/<project>/objects-index.md."""
    return PROJECTS_CONTEXT_DIR / project / "objects-index.md"


def project_summary_dir(project: str) -> Path:
    """Per-project каталог summaries: projects/<project>/summaries/."""
    return PROJECTS_CONTEXT_DIR / project / "summaries"


def project_cache_path(project: str) -> Path:
    """Per-project файл кэша: projects/<project>/summaries/.cache.json."""
    return PROJECTS_CONTEXT_DIR / project / "summaries" / ".cache.json"


def parse_sources_section(context_path: Path) -> list:
    """Парсит раздел «## Источники» из context.md. Возвращает список имён источников."""
    text = read_text_safe(context_path)
    sources: list = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped.lower().startswith("## источники")
            continue
        if in_section:
            m = re.match(r"^\s*-\s*`([^`]+)`", line)
            if m:
                sources.append(m.group(1).strip())
    return sources


def build_source_to_project_map() -> dict:
    """Маппинг source→project из разделов «Источники» всех projects/<proj>/context.md.
    Каждый источник (база + расширения) маппится к своему проекту.
    Fallback identity (source=project) добавляется в project_of, не здесь."""
    mapping: dict = {}
    conflicts: list = []
    if not PROJECTS_CONTEXT_DIR.exists():
        return mapping
    for context_file in sorted(PROJECTS_CONTEXT_DIR.glob("*/context.md")):
        project = context_file.parent.name
        for source in parse_sources_section(context_file):
            if source in mapping and mapping[source] != project:
                conflicts.append((source, mapping[source], project))
            mapping[source] = project
    if conflicts:
        for source, p1, p2 in conflicts:
            print(f"WARN source-mapping-conflict: источник '{source}' в проектах '{p1}' и '{p2}' (взято последнее)")
    return mapping


def sources_of(project: str) -> list:
    """Источники проекта (основа + расширения) из раздела «Источники» context.md.
    Fallback: [project] (один источник = сам проект)."""
    context_path = PROJECTS_CONTEXT_DIR / project / "context.md"
    if not context_path.exists():
        return [project]
    sources = parse_sources_section(context_path)
    return sources if sources else [project]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def safe_filename(name: str) -> str:
    name = name.replace("Справочник.", "Catalog.")
    name = name.replace("Документ.", "Document.")
    name = name.replace("Перечисление.", "Enum.")
    name = name.replace("ОбщийМодуль.", "CommonModule.")
    name = re.sub(r"[^\w.\-а-яА-ЯёЁ]+", "_", name, flags=re.UNICODE)
    return name.strip("_") + ".md"


def parse_objects_index(index_path: Path) -> list[IndexedObject]:
    if not index_path.exists():
        raise FileNotFoundError(f"Не найден индекс: {index_path}")

    text = read_text_safe(index_path)
    blocks = re.split(r"\n---\s*\n", text)

    objects: list[IndexedObject] = []

    for block in blocks:
        title_match = re.search(r"^#\s+(.+?)\s*$", block, re.MULTILINE)
        if not title_match:
            continue

        title = title_match.group(1).strip()

        if title.lower().startswith("индекс объектов"):
            continue

        path_match = re.search(r"Путь:\s*\n`([^`]+)`", block)
        if not path_match:
            continue

        rel_path = path_match.group(1).strip()
        abs_path = ROOT / rel_path

        purpose = ""
        purpose_match = re.search(
            r"Назначение:\s*\n(.+?)(?:\nЧто смотреть:|\nСвязанные объекты:|\Z)",
            block,
            re.S,
        )
        if purpose_match:
            purpose = purpose_match.group(1).strip()

        what_to_check = []
        what_match = re.search(
            r"Что смотреть:\s*\n(.+?)(?:\nСвязанные объекты:|\Z)",
            block,
            re.S,
        )
        if what_match:
            what_to_check = [
                line.strip("- ").strip()
                for line in what_match.group(1).splitlines()
                if line.strip().startswith("-")
            ]

        related = []
        related_match = re.search(
            r"Связанные объекты:\s*\n(.+?)\Z",
            block,
            re.S,
        )
        if related_match:
            related = [
                line.strip("- ").strip()
                for line in related_match.group(1).splitlines()
                if line.strip().startswith("-")
            ]

        objects.append(
            IndexedObject(
                title=title,
                path=abs_path,
                purpose=purpose,
                what_to_check=what_to_check,
                related=related,
                project=project_of(abs_path),
            )
        )

    return objects


def parse_all_indexes() -> list:
    """Объединяет IndexedObject из всех per-project файлов индекса
    (projects/<project>/objects-index.md). Для fallback-режима без --project."""
    all_objects: list = []
    if not PROJECTS_CONTEXT_DIR.exists():
        return all_objects
    for index_file in sorted(PROJECTS_CONTEXT_DIR.glob("*/objects-index.md")):
        all_objects.extend(parse_objects_index(index_file))
    return all_objects


def find_files(obj_path: Path) -> tuple[list[Path], list[Path]]:
    if not obj_path.exists():
        return [], []

    xml_files = sorted(obj_path.rglob("*.xml"))
    bsl_files = sorted(obj_path.rglob("*.bsl"))

    return xml_files, bsl_files


def extract_procedures_and_functions(bsl_text: str) -> tuple[list[str], list[str]]:
    procedures = re.findall(
        r"(?im)^\s*Процедура\s+([A-Za-zА-Яа-яЁё0-9_]+)\s*\(",
        bsl_text,
    )
    functions = re.findall(
        r"(?im)^\s*Функция\s+([A-Za-zА-Яа-яЁё0-9_]+)\s*\(",
        bsl_text,
    )

    return sorted(set(procedures)), sorted(set(functions))


def collect_object_context(
    obj: IndexedObject,
    max_xml_chars: int,
    max_bsl_chars: int,
) -> ObjectContext:
    xml_files, bsl_files = find_files(obj.path)

    xml_parts = []
    for file in xml_files:
        rel = file.relative_to(ROOT)
        text = read_text_safe(file, max_chars=max_xml_chars)
        if text.strip():
            xml_parts.append(f"\n\n### FILE: {rel}\n{text}")

    bsl_parts = []
    for file in bsl_files:
        rel = file.relative_to(ROOT)
        text = read_text_safe(file, max_chars=max_bsl_chars)
        if text.strip():
            bsl_parts.append(f"\n\n### FILE: {rel}\n{text}")

    xml_text = "\n".join(xml_parts)
    bsl_text = "\n".join(bsl_parts)

    procedures, functions = extract_procedures_and_functions(bsl_text)

    combined_hash = sha256_text(
        obj.title
        + str(obj.path)
        + xml_text
        + bsl_text
        + "\n".join(obj.related)
        + "\n".join(obj.what_to_check)
    )

    return ObjectContext(
        obj=obj,
        xml_files=xml_files,
        bsl_files=bsl_files,
        xml_text=xml_text,
        bsl_text=bsl_text,
        procedures=procedures,
        functions=functions,
        file_hash=combined_hash,
    )


def load_cache(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {}

    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache: dict, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_heuristic_summary(ctx: ObjectContext) -> str:
    obj = ctx.obj
    rel_path = obj.path.relative_to(ROOT) if obj.path.exists() else obj.path

    xml_rel = [str(p.relative_to(ROOT)) for p in ctx.xml_files[:20]]
    bsl_rel = [str(p.relative_to(ROOT)) for p in ctx.bsl_files[:20]]

    procedures = ctx.procedures[:50]
    functions = ctx.functions[:50]

    result = f"""# Summary: {obj.title}

## Назначение

{obj.purpose or "Не указано в objects-index/<проект>.md. Нужно уточнить по XML/BSL."}

## Путь

`{rel_path}`

## Что проверять в первую очередь

"""

    if obj.what_to_check:
        for item in obj.what_to_check:
            result += f"- {item}\n"
    else:
        result += "- XML объекта\n- BSL-модули объекта при вопросах про поведение\n"

    result += "\n## Найденные XML-файлы\n\n"
    if xml_rel:
        for item in xml_rel:
            result += f"- `{item}`\n"
        if len(ctx.xml_files) > 20:
            result += f"- ... ещё {len(ctx.xml_files) - 20} файлов\n"
    else:
        result += "XML-файлы не найдены.\n"

    result += "\n## Найденные BSL-файлы\n\n"
    if bsl_rel:
        for item in bsl_rel:
            result += f"- `{item}`\n"
        if len(ctx.bsl_files) > 20:
            result += f"- ... ещё {len(ctx.bsl_files) - 20} файлов\n"
    else:
        result += "BSL-файлы не найдены.\n"

    result += "\n## Процедуры\n\n"
    if procedures:
        for name in procedures:
            result += f"- `{name}`\n"
    else:
        result += "Процедуры не найдены или BSL не анализировался.\n"

    result += "\n## Функции\n\n"
    if functions:
        for name in functions:
            result += f"- `{name}`\n"
    else:
        result += "Функции не найдены или BSL не анализировался.\n"

    result += "\n## Связанные объекты из индекса\n\n"
    if obj.related:
        result += "Важно: это ориентиры из objects-index/<проект>.md, не доказанные ссылки в метаданных.\n\n"
        for item in obj.related:
            result += f"- {item}\n"
    else:
        result += "Связанные объекты в индексе не указаны.\n"

    result += f"""

## Ограничения

- Summary сформирован автоматически.
- Для точного вывода нужно читать XML/BSL объекта.
- Связи из индекса являются ориентирами, а не подтверждёнными фактами.
- Hash контекста: `{ctx.file_hash}`
"""

    return result


def call_litellm(prompt: str, model: str, api_base: str, api_key: str) -> str:
    url = api_base.rstrip("/") + "/chat/completions"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты аналитик 1С. Делай краткое summary объекта конфигурации "
                    "только по предоставленному контексту. Не выдумывай. "
                    "Если данных недостаточно, явно укажи ограничения."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0.1,
        "max_tokens": 1800,
    }

    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw)
            return parsed["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"LiteLLM HTTP error {e.code}: {body}") from e
    except Exception as e:
        raise RuntimeError(f"LiteLLM request failed: {e}") from e


def build_llm_prompt(ctx: ObjectContext, max_context_chars: int) -> str:
    obj = ctx.obj
    rel_path = obj.path.relative_to(ROOT) if obj.path.exists() else obj.path

    xml_text = ctx.xml_text[: max_context_chars // 2]
    bsl_text = ctx.bsl_text[: max_context_chars // 2]

    related = "\n".join(f"- {x}" for x in obj.related) or "Не указаны."
    what_to_check = "\n".join(f"- {x}" for x in obj.what_to_check) or "Не указано."

    return f"""
Сформируй markdown summary для объекта 1С.

Объект:
{obj.title}

Путь:
{rel_path}

Назначение из objects-index/<проект>.md:
{obj.purpose or "Не указано."}

Что смотреть из objects-index/<проект>.md:
{what_to_check}

Связанные объекты из objects-index/<проект>.md:
{related}

Важно:
- Связанные объекты из индекса — только ориентиры, не доказанные ссылки.
- Не выдумывай реквизиты, табличные части и поведение.
- Если что-то не подтверждено XML/BSL, напиши "не подтверждено локальными исходниками".

Сделай summary в формате:

# Summary: <объект>

## Назначение

## Структура

## Бизнес-логика

## BSL-модули и процедуры

## Связи

## Что проверять при анализе

## Ограничения

XML-контекст:
{xml_text}

BSL-контекст:
{bsl_text}
""".strip()


def build_llm_summary(
    ctx: ObjectContext,
    model: str,
    api_base: str,
    api_key: str,
    max_context_chars: int,
) -> str:
    prompt = build_llm_prompt(ctx, max_context_chars=max_context_chars)
    summary = call_litellm(
        prompt=prompt,
        model=model,
        api_base=api_base,
        api_key=api_key,
    )

    return summary.strip() + f"\n\n---\n\nHash контекста: `{ctx.file_hash}`\n"


def find_object_dir_for_title(title: str, project: str = "") -> Optional[Path]:
    """Для канонического заголовка 'РусТип.Имя' возвращает каталог объекта
    (projects/<source>/src/<TypeFolder>/<Имя>/) если он существует, иначе None.
    При заданном project — поиск ограничен источниками проекта (sources_of(project),
    включая расширения). Иначе — все источники из маппинга."""
    if "." not in title:
        return None
    ru_type, name = title.split(".", 1)
    folder = RU_TYPE_TO_FOLDER.get(ru_type)
    if not folder:
        return None
    src_root = ROOT / "projects"
    if not src_root.exists():
        return None
    source_names: list = []
    if project:
        source_names = sources_of(project)
    else:
        source_names = list(_ensure_source_map().keys())
    for source in source_names:
        candidate = src_root / source / "src" / folder / name
        if candidate.is_dir():
            return candidate
    return None


def scan_projects_for_objects(
    index_titles: set, requested_titles: Optional[set] = None, project: str = ""
) -> list:
    """Обнаружение объектов, отсутствующих в per-project индексе
    (projects/<project>/objects-index.md).

    При заданном project — обход/поиск ограничен источниками проекта (sources_of(project),
    включая расширения). Иначе — все источники из маппинга.

    Два режима:
    - requested_titles задан (целевой --scan --objects): для каждого
      канонического заголовка 'РусТип.Имя' найти каталог объекта через
      find_object_dir_for_title; вернуть найденные и не входящие в индекс
      (с проверкой наличия .xml/.bsl). Безопасно для больших конфигураций.
    - requested_titles None (полный --scan, dry-run): быстрый обход
      projects/<source>/src/<TypeFolder>/<Имя>/ (только перечисление
      каталогов, без rglob) и возврат кандидатов, не входящих в индекс.
      НЕ выполняет запись и НЕ фильтрует пустые каталоги — это только
      отчёт; вызывающая сторона решает, какие объекты добавить через
      --scan --objects. Вывод ограничен FULL_SCAN_REPORT_LIMIT элементами.

    Возвращает список кортежей (title, abs_path).
    """
    src_root = ROOT / "projects"
    if not src_root.exists():
        return []

    if project:
        source_names = sources_of(project)
    else:
        source_names = list(_ensure_source_map().keys())
    project_dirs = [src_root / s for s in source_names if (src_root / s).is_dir()]

    if requested_titles is not None:
        new_objects: list = []
        for title in sorted(requested_titles):
            if title in index_titles:
                print(f"  already-indexed: {title}")
                continue
            obj_dir = find_object_dir_for_title(title, project=project)
            if obj_dir is None:
                print(f"  WARN scan-object-not-found: {title}")
                continue
            xmls, bsls = find_files(obj_dir)
            if not xmls and not bsls:
                print(f"  WARN scan-empty-dir: {obj_dir.relative_to(ROOT)}")
                continue
            new_objects.append((title, obj_dir))
        return new_objects

    candidates: list = []
    for project_dir in project_dirs:
        src_dir = project_dir / "src"
        if not src_dir.exists():
            continue
        for type_folder in sorted(src_dir.iterdir()):
            if not type_folder.is_dir():
                continue
            en_type = FOLDER_TO_EN_TYPE.get(type_folder.name)
            if not en_type:
                continue
            ru_type = EN_TO_RU_TYPE.get(en_type)
            if not ru_type:
                continue
            for obj_dir in sorted(type_folder.iterdir()):
                if not obj_dir.is_dir():
                    continue
                title = f"{ru_type}.{obj_dir.name}"
                if title in index_titles:
                    continue
                candidates.append((title, obj_dir))
                if len(candidates) >= FULL_SCAN_REPORT_LIMIT:
                    break
            if len(candidates) >= FULL_SCAN_REPORT_LIMIT:
                break
        if len(candidates) >= FULL_SCAN_REPORT_LIMIT:
            break
    return candidates


def build_skeleton_block(title: str, obj_path: Path, project: str) -> str:
    """Skeleton-блок для objects-index/<project>.md: проект, путь и заглушку назначения.
    Семантику (Назначение/Связанные) уточняет аналитик позже."""
    rel_path = obj_path.relative_to(ROOT) if obj_path.exists() else obj_path
    return (
        f"---\n\n# {title}\n\n"
        f"Проект:\n`{project}`\n\n"
        f"Путь:\n`{rel_path}/`\n\n"
        f"Назначение:\n(автодобавлено скриптом --scan, уточнить аналитику)\n\n"
        f"Что смотреть:\n- XML объекта\n- BSL-модули объекта при вопросах про поведение\n\n"
        f"Связанные объекты:\n"
    )


def append_objects_to_index(new_objects: list) -> None:
    r"""Дописывает skeleton-блоки для новых объектов в per-project индекс
    (objects-index/<project>.md). Группировка по project_of(obj_path): каждый
    проект дописывается в свой файл.
    Формат: блоки разделены '\n---\s*\n'. Парсер parse_objects_index сплитит по
    '\n---\s*\n', поэтому новый блок начинается с '---' на отдельной строке.
    Идемпотентно по title: повторный вызов не дублирует (вызывающая сторона
    фильтрует по index_titles)."""
    if not new_objects:
        return

    # Группировка по проекту
    by_project: dict[str, list] = {}
    for title, obj_path in new_objects:
        project = project_of(obj_path)
        by_project.setdefault(project, []).append((title, obj_path))

    PROJECTS_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

    for project, items in by_project.items():
        index_path = objects_index_path(project)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        text = ""
        if index_path.exists():
            text = read_text_safe(index_path)

        suffix = ""
        if not text.endswith("\n"):
            text = text + "\n"

        for title, obj_path in items:
            block = build_skeleton_block(title, obj_path, project)
            if block.startswith("---\n\n"):
                # первый блок после преамбулы — ставим разделитель перед заголовком
                block = block[len("---\n\n"):]
            # Гарантируем, что перед '# ' стоит разделитель блока
            suffix += f"---\n\n{block}"

        index_path.write_text(text + suffix, encoding="utf-8")


def collect_subsystem_objects(subsystem_name: str, project: str = "") -> tuple:
    """Парсит XML подсистемы и её дочерних подсистем.
    При заданном project — обход ограничен источниками проекта (sources_of(project),
    включая расширения). Иначе — все источники из маппинга.
    Возвращает (ru_titles, not_indexed_en_titles, found).
    ru_titles — множество 'РусТип.Имя' объектов подсистемы.
    not_indexed_en_titles не определяется здесь (нет контекста индекса);
    фильтрацию по индексу выполняет вызывающая сторона.
    found = True, если хотя бы один XML подсистемы найден."""
    en_titles: set = set()
    unknown_types: set = set()
    found = False

    src_root = ROOT / "projects"
    if not src_root.exists():
        return en_titles, unknown_types, False

    if project:
        source_names = sources_of(project)
    else:
        source_names = list(_ensure_source_map().keys())
    project_dirs = [src_root / s for s in source_names if (src_root / s).is_dir()]

    xml_files = []
    for project_dir in project_dirs:
        sub_root = project_dir / "src" / "Subsystems"
        if not sub_root.exists():
            continue
        # XML подсистемы: Subsystems/<Name>.xml
        top_xml = sub_root / f"{subsystem_name}.xml"
        if top_xml.exists():
            found = True
            xml_files.append(top_xml)
        # Дочерние подсистемы: Subsystems/<Name>/Subsystems/<Child>.xml
        child_dir = sub_root / subsystem_name / "Subsystems"
        if child_dir.exists():
            for child_xml in sorted(child_dir.glob("*.xml")):
                found = True
                xml_files.append(child_xml)

    for xml_file in xml_files:
        text = read_text_safe(xml_file)
        # <xr:Item xsi:type="xr:MDObjectRef">Catalog.Имя</xr:Item>
        for m in re.finditer(
            r'<xr:Item\s+xsi:type="xr:MDObjectRef">([A-Za-z]+)\.([^<]+)</xr:Item>',
            text,
        ):
            en_type, obj_name = m.group(1), m.group(2)
            ru_type = EN_TO_RU_TYPE.get(en_type)
            if ru_type:
                en_titles.add(f"{ru_type}.{obj_name}")
            else:
                unknown_types.add(en_type)

    return en_titles, unknown_types, found


def main() -> int:
    # Принудительно UTF-8 для stdout/stderr: на Windows при piped stdout
    # (захват внешними инструментами — Kilo bash, CI) Python по умолчанию
    # использует ANSI-кодировку (cp1251) вместо UTF-8, что ломает кириллицу
    # в заголовках объектов 1С и путях. chcp 65001 на piped stdout не влияет.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project",
        default="",
        help="Имя проекта (папка projects/<проект>/). При заданном --project чтение "
             "индекса и --scan/--subsystem ограничены источниками проекта (основа + "
             "расширения); индекс — projects/<проект>/objects-index.md. Без --project — "
             "все per-project индексы (fallback для ручного запуска).",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Генерировать summary через LiteLLM/OpenAI-compatible API",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Перегенерировать даже если hash не изменился",
    )
    parser.add_argument(
        "--object",
        default="",
        help="Сгенерировать summary только для объекта, например: Документ.лвл_Смета",
    )
    parser.add_argument(
        "--objects",
        default="",
        help="Список объектов через запятую, точное совпадение, напр. "
             "Документ.лвл_Смета,Справочник.лвл_Работы. "
             "--objects имеет приоритет над --object.",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Обойти projects/<проект>/src/** и обнаружить объекты, отсутствующие в "
             "per-project индексе. Без --objects: dry-run (только отчёт о "
             "кандидатах, без записи в индекс). С --objects: добавить "
             "skeleton-блоки для указанных канонических заголовков и построить "
             "summaries (новые + перестроить изменённые индексированные по hash).",
    )
    parser.add_argument(
        "--subsystem",
        default="",
        help="Перестроить summaries по объектам подсистемы 1С (напр. "
             "Администрирование). Парсит Subsystems/<Name>.xml и дочерние "
             "подсистемы. Комбинируется с --force для принудительной "
             "перестройки. Игнорирует --objects/--object.",
    )
    parser.add_argument(
        "--max-xml-chars",
        type=int,
        default=12000,
        help="Максимум символов на один XML-файл",
    )
    parser.add_argument(
        "--max-bsl-chars",
        type=int,
        default=16000,
        help="Максимум символов на один BSL-файл",
    )
    parser.add_argument(
        "--max-context-chars",
        type=int,
        default=45000,
        help="Максимум символов контекста для LLM",
    )
    parser.add_argument(
        "--context-dir",
        default="",
        help="Каталог per-project контекста (содержит <project>/ папки с context.md, "
             "objects-index.md, summaries/). По умолчанию — .kilo/context/projects "
             "(Kilo layout). Для других инструментов — указать путь (напр. "
             "context/projects).",
    )

    args = parser.parse_args()

    # Override PROJECTS_CONTEXT_DIR if --context-dir is given
    if args.context_dir:
        global PROJECTS_CONTEXT_DIR
        PROJECTS_CONTEXT_DIR = Path(args.context_dir).resolve()

    project = args.project.strip()

    def load_indexed_objects() -> list:
        """Чтение индекса: per-project при --project, иначе все per-project файлы."""
        if project:
            path = objects_index_path(project)
            return parse_objects_index(path) if path.exists() else []
        return parse_all_indexes()

    PROJECTS_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

    if args.scan:
        existing = load_indexed_objects()
        index_titles = {o.title for o in existing}
        if args.objects:
            requested = {t.strip() for t in args.objects.split(",") if t.strip()}
            new_objects = scan_projects_for_objects(index_titles, requested, project=project)
            new_only = list(new_objects)
            if new_only:
                print(f"Найдено новых объектов: {len(new_only)}")
                for title, _ in new_only:
                    print(f"  new: {title}")
                append_objects_to_index(new_only)
            all_objects = load_indexed_objects()
            # обрабатывать ВСЕ запрошенные объекты: новые (только что добавленные
            # skeleton-блоки) + уже-индексированные (чтобы перестроить их summaries,
            # если исходники изменились — hash-проверка ниже пропустит неизменённые).
            # Без этого гибридный post-SDD сценарий (часть объектов новые, часть
            # изменённые) пропускал бы перестройку изменённых индексированных.
            requested_titles = {t for t, _ in new_only} | {t for t in requested if t in index_titles}
            objects = [o for o in all_objects if o.title in requested_titles]
            if not objects:
                print("Не удалось перечитать индекс после добавления. Запустите повторно без --scan.")
                return 1
        else:
            # dry-run: только отчёт о кандидатах, без записи в индекс
            candidates = scan_projects_for_objects(index_titles, project=project)
            print(f"Кандидатов на добавление (dry-run): {len(candidates)}")
            for title, obj_dir in candidates:
                print(f"  candidate: {title}  ({obj_dir.relative_to(ROOT)})")
            if len(candidates) == FULL_SCAN_REPORT_LIMIT:
                print(f"  ... список ограничен {FULL_SCAN_REPORT_LIMIT}; фактически кандидатов может быть больше")
            print("Чтобы добавить конкретные объекты, выполните:")
            scope = f"--project {project} " if project else ""
            print(f'  python scripts/build_summaries.py {scope}--scan --objects "Тип.Имя1,Тип.Имя2"')
            return 0
    elif args.subsystem:
        ru_titles, unknown_types, found = collect_subsystem_objects(args.subsystem, project=project)
        if not found:
            scope = f"проекте {project}" if project else "projects/*/src/Subsystems/"
            print(f"Подсистема '{args.subsystem}' не найдена в {scope}.")
            return 1
        if unknown_types:
            print(f"WARN unknown-subsystem-type: {sorted(unknown_types)}")
        existing = load_indexed_objects()
        index_by_title = {o.title: o for o in existing}
        objects = [index_by_title[t] for t in sorted(ru_titles) if t in index_by_title]
        not_indexed = sorted(t for t in ru_titles if t not in index_by_title)
        if not_indexed:
            print(f"not-indexed: {not_indexed}")
            print("  (запустите --scan, чтобы добавить их в индекс)")
        if not objects:
            print("Нет объектов подсистемы в индексе. Запустите сначала --scan.")
            return 1
        print(f"Объектов подсистемы '{args.subsystem}' в индексе: {len(objects)}")
    else:
        objects = load_indexed_objects()
        # При --project фильтруем только объекты этого проекта (защита от
        # cross-project совпадений заголовков при отсутствии --project fallback).
        if project:
            objects = [o for o in objects if o.project == project]

        if args.objects:
            wanted = {t.strip() for t in args.objects.split(",") if t.strip()}
            objects = [obj for obj in objects if obj.title in wanted]
        elif args.object:
            objects = [
                obj for obj in objects
                if args.object.lower() in obj.title.lower()
            ]

        if not objects:
            print("Не найдено объектов для обработки.")
            return 1

    api_base = os.getenv("LITELLM_API_BASE", "http://localhost:4000/v1")
    api_key = os.getenv("LITELLM_API_KEY", "")
    model = os.getenv("LITELLM_MODEL", "claude-3-5-haiku")

    if args.use_llm and not api_key:
        print("Ошибка: для --use-llm нужно задать LITELLM_API_KEY.")
        return 1

    processed = 0
    skipped = 0

    # Группировка объектов по логическому проекту: summaries и кэш per-project
    # (projects/<project>/summaries/, projects/<project>/summaries/.cache.json).
    objects_by_project: dict[str, list] = {}
    for obj in objects:
        objects_by_project.setdefault(obj.project or "unknown", []).append(obj)

    for project, proj_objects in objects_by_project.items():
        proj_out_dir = project_summary_dir(project)
        proj_cache_path = project_cache_path(project)
        proj_out_dir.mkdir(parents=True, exist_ok=True)
        cache = load_cache(proj_cache_path)

        for obj in proj_objects:
            print(f"Обработка: {obj.title}  [project: {project}]")

            ctx = collect_object_context(
                obj=obj,
                max_xml_chars=args.max_xml_chars,
                max_bsl_chars=args.max_bsl_chars,
            )

            output_file = proj_out_dir / safe_filename(obj.title)
            cached_hash = cache.get(obj.title, {}).get("hash")

            if not args.force and cached_hash == ctx.file_hash and output_file.exists():
                print(f"  skip: не изменился hash")
                skipped += 1
                continue

            if args.use_llm:
                try:
                    summary = build_llm_summary(
                        ctx=ctx,
                        model=model,
                        api_base=api_base,
                        api_key=api_key,
                        max_context_chars=args.max_context_chars,
                    )
                except Exception as e:
                    print(f"  LLM ошибка: {e}")
                    print("  fallback: эвристическое summary")
                    summary = build_heuristic_summary(ctx)
            else:
                summary = build_heuristic_summary(ctx)

            output_file.write_text(summary, encoding="utf-8")

            cache[obj.title] = {
                "hash": ctx.file_hash,
                "summary": str(output_file.relative_to(ROOT)),
                "path": str(obj.path.relative_to(ROOT)) if obj.path.exists() else str(obj.path),
            }

            print(f"  ok: {output_file.relative_to(ROOT)}")
            processed += 1

        save_cache(cache, proj_cache_path)

    print()
    print(f"Готово. Обработано: {processed}, пропущено: {skipped}.")
    print(f"Summaries: {PROJECTS_CONTEXT_DIR.relative_to(ROOT)}/<project>/summaries/")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as e:
        # Дружественное сообщение без traceback для обычных ошибок выполнения.
        print(f"Ошибка: {e}", file=sys.stderr)
        raise SystemExit(1)