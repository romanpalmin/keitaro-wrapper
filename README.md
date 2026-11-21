# keitaro-wrapper

Тестовое задание: веб-обёртка над API рекламного трекера — создаватор кампаний и редактор офферов.

**Стек:** Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, pytest.

## Запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # заполнить KEITARO_API_KEY и другие значения

uvicorn app.main:app --reload
```

Сервис: http://127.0.0.1:8000  
Health: http://127.0.0.1:8000/health  
Swagger: http://127.0.0.1:8000/docs

## Миграции

```bash
alembic upgrade head
```

_(раздел дополняется по мере появления моделей)_

## Тесты

```bash
pytest
```

## Структура

См. `docs/study-guide.md` (приватное учебное руководство, в публичный репозиторий не входит).
