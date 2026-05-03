# Storage Layer

## Что делает storage layer

`storage.py` инкапсулирует выбор backend-а хранения и предоставляет единые операции:
- `load_collection(...)`
- `save_collection(...)`
- `init_storage()`
- миграции между JSON и SQLite

Поддерживаемые коллекции:
- `tasks`
- `releases`
- `decisions_index`

## JSON vs SQLite

### JSON (default)
- просто читать и править руками
- удобно для локального MVP
- хранение в нескольких файлах

### SQLite (optional)
- единый файл `db`
- более предсказуемые записи
- полезно для роста данных

## Как включить SQLite

```env
STORAGE_BACKEND=sqlite
SQLITE_DB_PATH=data/team_agents.db
```

## Инициализация

```bash
python3 run.py storage-init
```

## Миграция JSON -> SQLite

```bash
python3 run.py migrate-json-to-sqlite
python3 run.py migrate-json-to-sqlite --force
```

## Откат / экспорт SQLite -> JSON

```bash
python3 run.py export-sqlite-to-json
python3 run.py export-sqlite-to-json --force
```

## Ограничения текущей реализации

- SQLite схема хранит коллекции как JSON документы (`collections.name + payload`)
- пока нет multi-project support
- пока нет advanced querying
