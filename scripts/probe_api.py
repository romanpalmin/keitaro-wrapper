#!/usr/bin/env python3
"""Ручной probe: проверить ключ и увидеть живые справочники Keitaro.

Запуск (из корня проекта, ключ уже в .env):

    .venv/bin/python scripts/probe_api.py

Ключ и пароли в логи/файлы не пишутся.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

# Чтобы запускать из корня, даже без pip install -e
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services.keitaro_client import KeitaroApiError, KeitaroClient  # noqa: E402

Check = tuple[str, Callable[[], Awaitable[Any]], tuple[str, ...]]


def _brief(items: list[dict], *, fields: tuple[str, ...], limit: int = 5) -> str:
    if not items:
        return "  (пусто)"
    lines = []
    for item in items[:limit]:
        parts = [f"{f}={item.get(f)!r}" for f in fields if f in item]
        lines.append("  " + (", ".join(parts) if parts else repr(item)))
    if len(items) > limit:
        lines.append(f"  … ещё {len(items) - limit}")
    return "\n".join(lines)


async def main() -> int:
    if not settings.keitaro_api_key:
        print("KEITARO_API_KEY пуст — открой .env и вставь ключ из админки.")
        return 1

    print(f"base_url = {settings.keitaro_base_url}")
    print("ключ: задан (значение не показываем)\n")

    async with KeitaroClient(timeout=20.0) as client:
        checks: list[Check] = [
            ("domains", client.list_domains, ("id", "name")),
            (
                "groups (campaigns)",
                lambda: client.list_groups(group_type="campaigns"),
                ("id", "name", "type"),
            ),
            ("traffic_sources", client.list_traffic_sources, ("id", "name")),
            ("offers", client.list_offers, ("id", "name")),
            ("campaigns", client.list_campaigns, ("id", "name", "alias")),
            ("stream_actions", client.list_stream_actions, ("key", "name")),
            ("stream_filters", client.list_stream_filters, ("name",)),
        ]

        failed = 0
        for label, fn, fields in checks:
            try:
                data = await fn()
                print(f"OK  {label}: {len(data)} шт.")
                print(_brief(data, fields=fields))
            except KeitaroApiError as exc:
                failed += 1
                print(f"ERR {label}: {exc}")
                if exc.status_code:
                    print(f"    status={exc.status_code} body={exc.body!r}")
            print()

        if failed:
            print(f"Готово, с ошибками: {failed}.")
            return 1
        print("Все справочники отвечают. Ключ рабочий.")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
