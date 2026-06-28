import importlib.util
from pathlib import Path


def _launcher_module():
    path = Path(__file__).parents[1] / "scripts" / "launcher.py"
    spec = importlib.util.spec_from_file_location("vg_launcher_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_launcher_restarts_instead_of_reusing(monkeypatch):
    launcher = _launcher_module()
    events = []
    monkeypatch.setattr(launcher, "prepare_path", lambda: events.append("path"))
    monkeypatch.setattr(launcher, "ensure_mysql", lambda: events.append("mysql"))
    monkeypatch.setattr(launcher, "command", lambda *_: "/conda")
    monkeypatch.setattr(launcher, "stop_running_gateway", lambda: events.append("stop"))
    monkeypatch.setattr(launcher, "run_migration", lambda _: events.append("migration"))
    monkeypatch.setattr(launcher, "start_gateway", lambda _: events.append("start"))
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: events.append("open"))

    assert launcher.main() == 0
    assert events == ["path", "mysql", "stop", "migration", "start", "open"]


def test_launcher_prepends_homebrew_path(monkeypatch):
    launcher = _launcher_module()
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    launcher.prepare_path()
    parts = launcher.os.environ["PATH"].split(launcher.os.pathsep)
    assert parts[0] == "/opt/homebrew/bin"
    assert len(parts) == len(set(parts))
