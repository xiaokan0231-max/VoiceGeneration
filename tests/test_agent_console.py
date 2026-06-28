import asyncio
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.agent import RemoteAgent, connection_state


@pytest.mark.parametrize(
    ("enabled", "url", "connected", "error", "expected"),
    [
        (False, "", False, None, "disconnected"),
        (True, "", False, "未配置主节点地址", "unconfigured"),
        (True, "http://coordinator:8080", False, None, "connecting"),
        (True, "http://coordinator:8080", False, "timeout", "error"),
        (True, "http://coordinator:8080", True, None, "connected"),
    ],
)
def test_connection_state(enabled, url, connected, error, expected):
    assert connection_state(
        enabled=enabled,
        coordinator_url=url,
        connected=connected,
        last_error=error,
    ) == expected


def test_agent_console_renders_runtime_logs():
    html = (Path(__file__).parents[1] / "gateway" / "agent_web" / "index.html").read_text(
        encoding="utf-8"
    )
    assert 'id="logs"' in html
    assert "fetch('/api/logs')" in html
    assert "未配置主节点" in html


def _agent_for_cluster_test():
    agent = RemoteAgent.__new__(RemoteAgent)
    agent.config = SimpleNamespace(
        settings=SimpleNamespace(
            cluster=SimpleNamespace(
                coordinator_url="http://coordinator:8080",
                token="",
                node_id="win-4060",
                poll_interval=0.01,
                lease_ttl=120,
            )
        )
    )
    agent.enabled = True
    agent.reconnect = asyncio.Event()
    agent.status = {"connected": False, "last_error": None}
    agent._stop = False
    agent._logs = deque(maxlen=300)
    agent._last_log = None
    agent._models = lambda: ["cosyvoice3"]
    return agent


def test_full_agent_still_polls_lease_to_keep_node_online():
    async def scenario():
        agent = _agent_for_cluster_test()
        agent.active = {"active-job": {}}
        agent.supervisor = SimpleNamespace(total_slots=lambda: 1)
        calls = []

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"jobs": []}

        class FakeClient:
            async def post(self, url, **kwargs):
                calls.append((url, kwargs))
                agent.reconnect.set()
                return FakeResponse()

        agent._client = FakeClient()
        await agent._lease_loop(agent.base)
        return calls

    calls = asyncio.run(scenario())

    assert len(calls) == 1
    assert calls[0][0].endswith("/v1/cluster/lease")
    assert calls[0][1]["json"]["capacity"] == 0


def test_active_job_renews_its_lease(monkeypatch):
    async def scenario():
        agent = _agent_for_cluster_test()
        agent.active = {"job-123": {}}
        calls = []

        async def immediate_sleep(_seconds):
            return None

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True}

        class FakeClient:
            async def post(self, url, **kwargs):
                calls.append((url, kwargs))
                agent.active.clear()
                return FakeResponse()

        monkeypatch.setattr("gateway.agent.asyncio.sleep", immediate_sleep)
        agent._client = FakeClient()
        await agent._job_heartbeat("job-123")
        return calls

    calls = asyncio.run(scenario())

    assert len(calls) == 1
    assert calls[0][0].endswith("/v1/cluster/jobs/job-123/heartbeat")
