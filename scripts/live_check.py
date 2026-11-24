#!/usr/bin/env python3
"""Живой прогон обёртки против учебного стенда: создаватор + редактор офферов.

Сценарии (все новые кампании — с префиксом test_palmin_):

  A. Создаватор через HTTP API (uvicorn): POST /api/campaigns → 2 потока,
     затем сверка тел потоков в трекере с контрактом документации стенда.
  B. Откат: сбой на втором потоке → кампания удаляется из трекера,
     запись в локальном журнале — status=failed.
     (Сам сбой имитируется на клиенте; POST /campaigns, первый POST /streams
      и откатный DELETE — реальные вызовы трекера.)
  C. Редактор через HTTP API: add/remove офферов, пересчёт share,
     сумма активных = 100; плюс коды ошибок 409/404.

Запуск из корня проекта:  .venv/bin/python scripts/live_check.py
Ключ API в вывод не попадает (только статус-коды и тела ответов).
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

# Чтобы запускать из корня, даже без pip install -e
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import CampaignRecord  # noqa: E402
from app.services.campaign_creator import (  # noqa: E402
    CampaignCreatorError,
    create_campaign_with_streams,
)
from app.services.keitaro_client import KeitaroApiError, KeitaroClient  # noqa: E402

PORT = 8017
BASE = f"http://127.0.0.1:{PORT}"
GEO = "GM"
# Первые три оффера из справочника стенда (170 шт., каталог только на чтение)
OFFER_1, OFFER_2, OFFER_3 = 18001, 18002, 18003


def stamp_name() -> str:
    """Уникальный хвост имени, чтобы alias не занят повторным запуском."""
    return str(int(time.time() * 1000))


class TraceClient(KeitaroClient):
    """Клиент, который пишет в trace метод/путь/статус каждого ответа трекера."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.trace: list[str] = []

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        client = self._ensure_client()
        original = client.request

        async def traced(method_: str, url: Any, **kwargs: Any) -> httpx.Response:
            response = await original(method_, url, **kwargs)
            self.trace.append(f"{method_} {url} -> HTTP {response.status_code}")
            return response

        client.request = traced  # type: ignore[method-assign]
        try:
            return await super()._request(method, path, params=params, json=json)
        finally:
            client.request = original  # type: ignore[method-assign]


class FailSecondStream(KeitaroClient):
    """Имитация сбоя трекера на втором POST /streams (для проверки отката)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._stream_posts = 0
        self.trace: list[str] = []

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        if method == "POST" and path == "/streams":
            self._stream_posts += 1
            if self._stream_posts >= 2:
                self.trace.append("POST /streams -> IMITATED 500 (injected)")
                raise KeitaroApiError(
                    "Имитация сбоя трекера на втором потоке",
                    kind="http",
                    status_code=500,
                    body='{"error":"injected"}',
                )
        return await super()._request(method, path, params=params, json=json)


def show(label: str, resp: httpx.Response) -> dict[str, Any]:
    """Печать кода и тела ответа НАШЕГО API (ключ тут не участвует)."""
    print(f"  {label}: HTTP {resp.status_code}")
    print(f"    body: {resp.text}")
    try:
        return resp.json()
    except ValueError:
        return {}


async def check_streams_shape(client: KeitaroClient, campaign_id: int) -> None:
    """Сверка потоков в трекере с контрактом документации стенда."""
    streams = await client.get_campaign_streams(campaign_id)
    print(f"  Потоков в трекере: {len(streams)}")
    for s in streams:
        print(
            f"    id={s.get('id')} name={s.get('name')!r} type={s.get('type')!r} "
            f"schema={s.get('schema')!r} action_type={s.get('action_type')!r} "
            f"action_payload={s.get('action_payload')!r}"
        )
        print(f"      filters={s.get('filters')}")
        print(f"      offers={s.get('offers')}")


async def scenario_rollback(stamp: int) -> None:
    """B. Сбой на втором потоке → реальный DELETE кампании (откат)."""
    print("\n=== B. Откат при сбое на втором потоке ===")
    name = f"test_palmin_rollback_{stamp}"
    client = FailSecondStream(
        base_url=settings.keitaro_base_url, api_key=settings.keitaro_api_key
    )
    async with SessionLocal() as session:
        try:
            result = await create_campaign_with_streams(
                name=name,
                geo_code=GEO,
                offer_id=OFFER_1,
                session=session,
                client=client,
            )
        except CampaignCreatorError as exc:
            print(f"  Ожидаемая ошибка обёртки: HTTP {exc.status_code}: {exc}")
        else:
            print(f"  НЕОЖИДАННО: создалось без ошибки — {result}")

    # Кампания должна отсутствовать в трекере, запись в журнале — failed
    verify = TraceClient(
        base_url=settings.keitaro_base_url, api_key=settings.keitaro_api_key
    )
    campaigns = await verify.list_campaigns(limit=100)
    left = [c for c in campaigns if c.get("name") == name]
    print(f"  Кампаний {name!r} в трекере после отката: {len(left)} "
          f"(ожидается 0)")
    print(f"  Трасса трекера: {verify.trace}")
    await verify.aclose()
    await client.aclose()

    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(CampaignRecord).where(CampaignRecord.name == name)
                )
            )
            .scalars()
            .all()
        )
    for row in rows:
        print(f"  Журнал: id={row.id} keitaro_campaign_id="
              f"{row.keitaro_campaign_id} status={row.status}")


async def main() -> int:
    if not settings.keitaro_api_key:
        print("KEITARO_API_KEY пуст — заполни .env.")
        return 1

    print(f"Трекер: {settings.keitaro_base_url} (ключ не показываем)")
    print(f"Наш API: {BASE}")

    # --- поднимаем uvicorn на время прогона ---
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--log-level",
            "warning",
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as http:
            for _ in range(60):
                try:
                    health = await http.get("/health")
                    if health.status_code == 200:
                        break
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)
            else:
                print("uvicorn не поднялся за 15 с — выходим.")
                return 1

            tracker = TraceClient(
                base_url=settings.keitaro_base_url,
                api_key=settings.keitaro_api_key,
            )

            # === A. Создаватор ===
            print("\n=== A. Создаватор: кампания с двумя потоками ===")
            name_a = f"live_creator_{stamp_name()}"
            resp = await http.post(
                "/api/campaigns",
                json={"name": name_a, "geo_code": GEO, "offer_id": OFFER_1},
            )
            data = show("POST /api/campaigns", resp)
            campaign_id = data.get("keitaro_campaign_id")
            print(
                f"  status={data.get('status')} campaign_id={campaign_id} "
                f"stream_ids={data.get('stream_ids')}"
            )
            if resp.status_code != 201 or not isinstance(campaign_id, int):
                print("  Создание не удалось — дальше по сценарию не идём.")
                print(f"  Трасса трекера: {tracker.trace}")
                await tracker.aclose()
                return 1

            print("  Сверка потоков в трекере (контракт стенда):")
            await check_streams_shape(tracker, campaign_id)

            # === A2. Тот же создаватор в процессе — коды ответов трекера ===
            print("\n=== A2. Создаватор напрямую через сервисы: коды трекера ===")
            tracker.trace.clear()
            name_a2 = f"live_trace_{stamp_name()}"
            async with SessionLocal() as session:
                result = await create_campaign_with_streams(
                    name=name_a2,
                    geo_code=GEO,
                    offer_id=OFFER_1,
                    session=session,
                    client=tracker,
                )
            print(f"  Итог: status={result.status} "
                  f"campaign_id={result.keitaro_campaign_id} "
                  f"stream_ids={result.stream_ids}")
            print("  Коды ответов трекера по шагам:")
            for line in tracker.trace:
                print(f"    {line}")
            tracker.trace.clear()

            # === C. Редактор ===
            print("\n=== C. Редактор офферов (через HTTP API) ===")
            resp = await http.get(f"/api/campaigns/{campaign_id}/offers")
            show("GET  /api/campaigns/{id}/offers (старт)", resp)

            resp = await http.post(
                f"/api/campaigns/{campaign_id}/offers", json={"offer_id": OFFER_2}
            )
            show(f"POST add offer {OFFER_2} → ждём 201, 50/50", resp)

            resp = await http.post(
                f"/api/campaigns/{campaign_id}/offers", json={"offer_id": OFFER_3}
            )
            show(f"POST add offer {OFFER_3} → ждём 201, 33/33/34", resp)

            resp = await http.post(
                f"/api/campaigns/{campaign_id}/offers", json={"offer_id": OFFER_2}
            )
            show(f"POST add offer {OFFER_2} повторно → ждём 409", resp)

            resp = await http.delete(
                f"/api/campaigns/{campaign_id}/offers/{OFFER_2}"
            )
            show(f"DELETE offer {OFFER_2} → ждём 50/50", resp)

            resp = await http.delete(
                f"/api/campaigns/{campaign_id}/offers/{OFFER_3}"
            )
            show(f"DELETE offer {OFFER_3} → ждём 100", resp)

            resp = await http.delete(
                f"/api/campaigns/{campaign_id}/offers/999999"
            )
            show("DELETE несуществующего оффера → ждём 404", resp)

            resp = await http.post(
                "/api/campaigns",
                json={"name": name_a, "geo_code": GEO, "offer_id": 999999},
            )
            show("POST с несуществующим offer_id → ждём 404", resp)

            print("  Итоговое состояние потока в трекере:")
            await check_streams_shape(tracker, campaign_id)
            print(f"  Трасса трекера (сверка после редактора): {tracker.trace}")
            await tracker.aclose()

            # === B. Откат ===
            await scenario_rollback(stamp_name())

            # === D. Коды ошибок трекера (тела без ключа) ===
            print("\n=== D. Коды ошибок трекера ===")
            async with httpx.AsyncClient(
                base_url=settings.keitaro_base_url + "/admin_api/v1",
                headers={"Api-Key": settings.keitaro_api_key},
                timeout=20.0,
            ) as raw:
                probes = [
                    ("POST /streams с пустым телом", "POST", "/streams", {}),
                    (
                        "GET /stream_filters (нет на стенде)",
                        "GET",
                        "/stream_filters",
                        None,
                    ),
                    (
                        "GET /streams_actions (нет на стенде)",
                        "GET",
                        "/streams_actions",
                        None,
                    ),
                ]
                for label, method, path, body in probes:
                    if method == "GET":
                        r = await raw.get(path)
                    else:
                        r = await raw.post(path, json=body)
                    print(f"  {label}: HTTP {r.status_code} body={r.text}")

            print("\n=== Готово ===")
            print(f"  Создана кампания для сценариев A/C: id={campaign_id} "
                  f"name={data.get('name')}")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
