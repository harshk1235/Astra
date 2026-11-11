"""ClashKing client failure handling, against a real local HTTP server that
misbehaves on purpose. No network access needed."""
import asyncio

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from astra.clashking import ClashKingClient, ClashKingError, NotFound


def run(coro):
    return asyncio.run(coro)


async def _with_server(responses, test_fn, **client_kwargs):
    """Serve `responses` in order (status, body, headers) and call test_fn(client, calls, sleeps)."""
    calls = []
    queue = list(responses)

    async def handler(request):
        calls.append(request.path_qs)
        status, body, headers = queue.pop(0) if queue else (200, {"ok": True}, {})
        if status == "hang":
            await asyncio.sleep(5)
            status = 200
        return web.json_response(body, status=status, headers=headers)

    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    sleeps = []

    async def fake_sleep(seconds):  # record backoff instead of waiting
        sleeps.append(seconds)

    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = ClashKingClient(
            str(server.make_url("")), session=session, sleep=fake_sleep, jitter=lambda: 0.0, **client_kwargs
        )
        return await test_fn(client, calls, sleeps)


def test_success_returns_json():
    async def t(client, calls, sleeps):
        data = await client.get_json("/player/%23AAA/stats", params={"x": 1})
        assert data == {"items": [1]}
        assert calls == ["/player/%23AAA/stats?x=1"]
        assert sleeps == []
    run(_with_server([(200, {"items": [1]}, {})], t))


def test_retries_5xx_with_exponential_backoff():
    async def t(client, calls, sleeps):
        assert await client.get_json("/x") == {"ok": 1}
        assert len(calls) == 3
        assert sleeps == [1.0, 2.0]          # 1s, then 2s
    run(_with_server([(500, {}, {}), (503, {}, {}), (200, {"ok": 1}, {})], t))


def test_429_honours_retry_after():
    async def t(client, calls, sleeps):
        assert await client.get_json("/x") == {"ok": 1}
        assert sleeps == [7.0]
    run(_with_server([(429, {}, {"Retry-After": "7"}), (200, {"ok": 1}, {})], t))


def test_gives_up_after_max_retries():
    async def t(client, calls, sleeps):
        with pytest.raises(ClashKingError, match="after 3 attempts"):
            await client.get_json("/x")
        assert len(calls) == 3
    run(_with_server([(500, {}, {})] * 5, t, max_retries=2))


def test_404_is_not_retried():
    async def t(client, calls, sleeps):
        with pytest.raises(NotFound):
            await client.get_json("/player/%23BAD/stats")
        assert len(calls) == 1 and sleeps == []
    run(_with_server([(404, {"detail": "no"}, {})], t))


def test_other_4xx_is_not_retried():
    async def t(client, calls, sleeps):
        with pytest.raises(ClashKingError, match="HTTP 400"):
            await client.get_json("/x")
        assert len(calls) == 1
    run(_with_server([(400, {"detail": "bad"}, {})], t))


def test_timeout_is_retried():
    async def t(client, calls, sleeps):
        assert await client.get_json("/x") == {"ok": 1}
        assert len(calls) == 2 and sleeps == [1.0]
    run(_with_server([("hang", {}, {}), (200, {"ok": 1}, {})], t, timeout_s=0.2))


def test_concurrency_is_limited():
    async def t(client, calls, sleeps):
        in_flight = peak = 0
        original = client._session.get

        class Tracking:
            def __init__(self, ctx): self.ctx = ctx
            async def __aenter__(self):
                nonlocal in_flight, peak
                in_flight += 1; peak = max(peak, in_flight)
                await asyncio.sleep(0.02)
                return await self.ctx.__aenter__()
            async def __aexit__(self, *a):
                nonlocal in_flight
                in_flight -= 1
                return await self.ctx.__aexit__(*a)

        client._session.get = lambda *a, **k: Tracking(original(*a, **k))
        await asyncio.gather(*(client.get_json(f"/p{i}") for i in range(10)))
        assert peak == 2
    run(_with_server([], t, max_concurrency=2))
