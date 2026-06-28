import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

from gateway.config import ModelConfig, Settings
from gateway.supervisor import Supervisor
from workers.f5_tts.backend import F5TTSBackend


class FakeProcess:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


def test_supervisor_terminates_worker_on_windows(monkeypatch):
    supervisor = Supervisor(Settings(), [ModelConfig(id="f5_tts")])
    process = FakeProcess()
    worker = supervisor.pools["f5_tts"][0]
    worker.process = process
    monkeypatch.setattr("gateway.supervisor.os.name", "nt")

    supervisor._stop(worker)

    assert process.terminated
    assert worker.process is None


def test_worker_health_check_bypasses_environment_proxy(monkeypatch):
    client_options = {}

    class FakeResponse:
        status_code = 200

    class FakeClient:
        def __init__(self, **kwargs):
            client_options.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return FakeResponse()

    supervisor = Supervisor(Settings(), [ModelConfig(id="cosyvoice3")])
    worker = supervisor.pools["cosyvoice3"][0]
    worker.process = FakeProcess()
    monkeypatch.setattr("gateway.supervisor.httpx.AsyncClient", FakeClient)

    asyncio.run(supervisor._wait_healthy(worker))

    assert client_options["trust_env"] is False


def test_worker_release_is_idempotent():
    async def scenario():
        supervisor = Supervisor(Settings(), [ModelConfig(id="cosyvoice3")])
        worker = supervisor.pools["cosyvoice3"][0]
        worker.process = FakeProcess()
        acquired = await supervisor.acquire("cosyvoice3")
        supervisor.release("cosyvoice3", acquired)
        supervisor.release("cosyvoice3", acquired)
        return supervisor.available_capacity(), supervisor.free["cosyvoice3"].qsize()

    available, queue_size = asyncio.run(scenario())

    assert available == 1
    assert queue_size == 1


def test_stale_worker_release_does_not_inflate_reconfigured_pool():
    async def scenario():
        original = ModelConfig(id="cosyvoice3", replicas=1)
        supervisor = Supervisor(Settings(), [original])
        old_worker = supervisor.pools["cosyvoice3"][0]
        old_worker.process = FakeProcess()
        acquired = await supervisor.acquire("cosyvoice3")
        replacement = ModelConfig(id="cosyvoice3", replicas=2)
        await supervisor.reconfigure([replacement])
        supervisor.release("cosyvoice3", acquired)
        return supervisor.available_capacity(), supervisor.free["cosyvoice3"].qsize()

    available, queue_size = asyncio.run(scenario())

    assert available == 2
    assert queue_size == 2


@pytest.mark.parametrize(
    ("parameters", "expected"),
    [
        ({"model": None, "device": None}, {"model": "F5TTS_v1_Base", "device": "cuda"}),
        ({"model_type": None, "device": None}, {"model_type": "F5-TTS", "device": "cuda"}),
    ],
)
def test_f5_backend_supports_modern_and_legacy_api(monkeypatch, parameters, expected):
    calls = []

    class FakeF5TTS:
        __signature__ = __import__("inspect").Signature(
            [__import__("inspect").Parameter(name, __import__("inspect").Parameter.KEYWORD_ONLY)
             for name in parameters]
        )

        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.target_sample_rate = 24000

    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(is_available=lambda: True)
    torch.backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False))
    api = ModuleType("f5_tts.api")
    api.F5TTS = FakeF5TTS
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "f5_tts", ModuleType("f5_tts"))
    monkeypatch.setitem(sys.modules, "f5_tts.api", api)

    backend = F5TTSBackend("f5_tts", {"model": "F5TTS_v1_Base", "device": "cuda"})
    backend._ensure_loaded()

    assert calls == [expected]
