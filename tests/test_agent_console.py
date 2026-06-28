import asyncio
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from gateway.agent import EmbeddedAgent, RemoteAgent, connection_state


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
    agent.status = {
        "connected": False,
        "last_error": None,
        "counters": {"leased": 0, "completed": 0, "failed": 0, "upload_retries": 0},
    }
    agent._stop = False
    agent._logs = deque(maxlen=300)
    agent._last_log = None
    agent._models = lambda: ["cosyvoice3"]
    return agent


def test_full_agent_still_polls_lease_to_keep_node_online():
    async def scenario():
        agent = _agent_for_cluster_test()
        agent.active = {"active-job": {"phase": "waiting_worker"}}
        agent.supervisor = SimpleNamespace(
            total_slots=lambda: 1,
            available_by_model=lambda: {"cosyvoice3": 1},
            runtime_metrics=lambda: {"workers": []},
        )
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
    assert calls[0][1]["json"]["capacities"] == {"cosyvoice3": 0}
    assert "metrics" in calls[0][1]["json"]


def test_agent_reports_free_capacity_per_model():
    async def scenario():
        agent = _agent_for_cluster_test()
        agent._models = lambda: ["cosyvoice3", "f5_tts"]
        agent.active = {"active-job": {"model": "cosyvoice3", "phase": "synthesizing"}}
        agent.supervisor = SimpleNamespace(
            total_slots=lambda: 3,
            available_by_model=lambda: {"cosyvoice3": 1, "f5_tts": 1},
            runtime_metrics=lambda: {"workers": []},
        )
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
        return calls[0][1]["json"]

    payload = asyncio.run(scenario())
    assert payload["capacity"] == 2
    assert payload["capacities"] == {"cosyvoice3": 1, "f5_tts": 1}


def test_embedded_agent_renews_slow_local_job(monkeypatch):
    calls = []

    async def immediate_sleep(_seconds):
        return None

    def extend(job_id, lease_ttl):
        calls.append((job_id, lease_ttl))
        return False

    agent = EmbeddedAgent.__new__(EmbeddedAgent)
    agent.state = SimpleNamespace(
        config=SimpleNamespace(
            settings=SimpleNamespace(cluster=SimpleNamespace(lease_ttl=120))
        )
    )
    agent._stop = False
    monkeypatch.setattr("gateway.agent.asyncio.sleep", immediate_sleep)
    monkeypatch.setattr("gateway.cluster.extend_lease", extend)

    asyncio.run(agent._job_heartbeat("local-job"))

    assert calls == [("local-job", 120)]


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


def test_heartbeat_cannot_revive_a_lost_lease_without_reacquiring(monkeypatch):
    async def scenario():
        agent = _agent_for_cluster_test()
        state = {"lease_valid": False}
        agent.active = {"job-123": state}

        async def immediate_sleep(_seconds):
            return None

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True}

        class FakeClient:
            async def post(self, _url, **_kwargs):
                agent._stop = True
                return FakeResponse()

        monkeypatch.setattr("gateway.agent.asyncio.sleep", immediate_sleep)
        agent._client = FakeClient()
        await agent._job_heartbeat("job-123")
        return state

    state = asyncio.run(scenario())

    assert state["lease_valid"] is False


def test_result_upload_retries_buffered_audio_without_resynthesis(monkeypatch):
    async def scenario():
        agent = _agent_for_cluster_test()
        agent.active = {"job-123": {"phase": "uploading", "lease_valid": True}}
        calls = []

        async def immediate_sleep(_seconds):
            return None

        class FakeResponse:
            def raise_for_status(self):
                return None

        class FakeClient:
            async def post(self, url, **kwargs):
                calls.append((url, kwargs))
                if len(calls) == 1:
                    raise httpx.ConnectError("coordinator unavailable")
                return FakeResponse()

        monkeypatch.setattr("gateway.agent.asyncio.sleep", immediate_sleep)
        agent._client = FakeClient()
        state = agent.active["job-123"]
        await agent._upload_result("job-123", b"wav-data", 12.5, state, attempts=3)
        return calls, state, agent.status["counters"]

    calls, state, counters = asyncio.run(scenario())

    assert len(calls) == 2
    assert all(call[1]["files"]["audio"][1] == b"wav-data" for call in calls)
    assert state["phase"] == "upload_retry"
    assert counters["upload_retries"] == 1


def test_duplicate_lease_reuses_existing_job():
    async def scenario():
        agent = _agent_for_cluster_test()
        existing = {
            "model": "cosyvoice3", "text": "same", "started": 1.0,
            "phase": "upload_retry", "lease_valid": False, "lease_count": 1,
        }
        agent.active = {"job-123": existing}
        agent.supervisor = SimpleNamespace(
            available_by_model=lambda: {"cosyvoice3": 1},
            total_slots=lambda: 1,
            runtime_metrics=lambda: {"workers": []},
        )

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"jobs": [{"id": "job-123", "model": "cosyvoice3"}]}

        class FakeClient:
            async def post(self, _url, **_kwargs):
                agent.reconnect.set()
                return FakeResponse()

        agent._client = FakeClient()
        await agent._lease_loop(agent.base)
        return existing, agent.active["job-123"]

    existing, current = asyncio.run(scenario())

    assert current is existing
    assert current["lease_valid"] is True
    assert current["lease_count"] == 2


def test_upload_backlog_stops_unbounded_leasing():
    async def scenario():
        agent = _agent_for_cluster_test()
        agent.active = {
            "upload": {"phase": "upload_retry"},
            "synth": {"phase": "synthesizing"},
        }
        agent.supervisor = SimpleNamespace(
            available_by_model=lambda: {"cosyvoice3": 1},
            total_slots=lambda: 1,
            runtime_metrics=lambda: {"workers": []},
        )
        requests = []

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"jobs": []}

        class FakeClient:
            async def post(self, _url, **kwargs):
                requests.append(kwargs["json"])
                agent.reconnect.set()
                return FakeResponse()

        agent._client = FakeClient()
        await agent._lease_loop(agent.base)
        return requests

    requests = asyncio.run(scenario())

    assert requests[0]["capacity"] == 0
