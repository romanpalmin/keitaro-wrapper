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
        # filters/offers — списки, печатаем целиком, если непустые (у потоков)
        for extra in ("filters", "offers"):
            value = item.get(extra)
            if value:
                parts.append(f"{extra}={value!r}")
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
        # Проверяем только те пути, что поддержаны на стенде. Путей
        # /streams_actions, /stream_filters, /stream_types, /stream_schemas
        # у стенда нет (его документация, раздел «Чего нет»), поэтому в probe
        # они не спрашиваются: константы потоков лежат в campaign_creator
        # (источник — раздел «Что поддержано»).
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
        ]

        failed = 0
        campaigns: list[dict] = []
        for label, fn, fields in checks:
            try:
                data = await fn()
                if label == "campaigns":
                    campaigns = data
                print(f"OK  {label}: {len(data)} шт.")
                print(_brief(data, fields=fields))
            except KeitaroApiError as exc:
                failed += 1
                print(f"ERR {label}: {exc}")
                if exc.status_code:
                    print(f"    status={exc.status_code} body={exc.body!r}")
            print()

        # Данные потоков: GET /campaigns/{id} и /campaigns/{id}/streams на живом
        # объекте стенда (своя кампания — первая с префиксом test_palmin_,
        # иначе первая из списка).
        target = next(
            (c for c in campaigns if str(c.get("name", "")).startswith("test_palmin_")),
            campaigns[0] if campaigns else None,
        )
        if target is None or not isinstance(target.get("id"), int):
            failed += 1
            print("ERR campaign detail/streams: своя кампания не найдена "
                  "— проверить потоки не на чем.\n")
        else:
            cid = target["id"]
            print(f"Кампания для проверки потоков: id={cid} "
                  f"name={target.get('name')!r}\n")

            async def _detail() -> list[dict]:
                return [await client.get_campaign(cid)]

            async def _streams() -> list[dict]:
                return await client.get_campaign_streams(cid)

            for label, fn, fields in (
                ("campaign detail", _detail, ("id", "name", "alias")),
                (
                    "campaign streams",
                    _streams,
                    ("id", "name", "type", "schema", "action_type", "action_payload"),
                ),
            ):
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
        print("Все 7 проверок прошли. Ключ рабочий.")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
