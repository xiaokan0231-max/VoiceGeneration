#!/usr/bin/env python3
"""Launch VoiceGeneration quietly from the macOS application bundle."""
from __future__ import annotations

import os
import signal
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "cache" / "_logs"
PID_FILE = LOG_DIR / "gateway.pid"
URL = "http://127.0.0.1:8080/"
HEALTH = f"{URL}health"
APP_PATHS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "launcher.log").open("a", encoding="utf-8") as stream:
        stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def command(name: str, fallback: str) -> str:
    return shutil.which(name) or fallback


def prepare_path() -> None:
    """macOS GUI apps do not inherit the interactive shell's Homebrew PATH."""
    current = os.environ.get("PATH", "").split(os.pathsep)
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys([*APP_PATHS, *filter(None, current)]))


def healthy() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=1.5) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def port_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(.3)
        return sock.connect_ex(("127.0.0.1", 8080)) == 0


def _managed_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        command_line = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=3,
        ).stdout
        return pid if "gateway.main:app" in command_line else None
    except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError, subprocess.SubprocessError):
        return None


def _terminate_managed_process(pid: int | None = None, *, force: bool = False) -> bool:
    pid = pid or _managed_pid()
    if pid is None:
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL if force else signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        state = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "stat="],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        return bool(state and not state.startswith("Z"))
    except (ProcessLookupError, PermissionError):
        return False


def _listener_pid() -> int | None:
    try:
        output = subprocess.run(
            ["/usr/sbin/lsof", "-tiTCP:8080", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip().splitlines()
        for value in output:
            pid = int(value)
            command_line = subprocess.run(
                ["/bin/ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, timeout=3,
            ).stdout
            if "gateway.main:app" in command_line:
                return pid
    except (ValueError, subprocess.SubprocessError):
        pass
    return None


def _worker_pgids(parent_pid: int | None = None) -> set[int]:
    """Return local model-worker process groups, optionally limited to one gateway."""
    groups: set[int] = set()
    try:
        output = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid=,pgid=,command="],
            capture_output=True, text=True, timeout=5,
        ).stdout
        for line in output.splitlines():
            parts = line.strip().split(None, 3)
            if len(parts) != 4:
                continue
            command_parts = parts[3].split()
            is_model_worker = (
                command_parts
                and Path(command_parts[0]).name.startswith("python")
                and any(
                    command_parts[index:index + 2] == ["-m", "worker_runtime.server"]
                    for index in range(len(command_parts) - 1)
                )
            )
            if not is_model_worker:
                continue
            if parent_pid is None or int(parts[1]) == parent_pid:
                groups.add(int(parts[2]))
    except (ValueError, subprocess.SubprocessError):
        pass
    return groups


def _signal_groups(groups: set[int], sig: signal.Signals) -> None:
    for group in groups:
        try:
            os.killpg(group, sig)
        except (ProcessLookupError, PermissionError):
            pass


def _group_alive(group: int) -> bool:
    try:
        os.killpg(group, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def stop_running_gateway() -> None:
    """Gracefully stop the current gateway and all workers before relaunching."""
    if not port_open():
        return
    managed_pid = _managed_pid()
    listener_pid = _listener_pid()
    gateway_groups = {
        os.getpgid(pid) for pid in (managed_pid, listener_pid)
        if pid is not None and _pid_alive(pid)
    }
    worker_groups = _worker_pgids(listener_pid)
    log("检测到现有服务，正在重启网关和全部 worker")
    if healthy():
        try:
            request = urllib.request.Request(f"{URL}v1/service/shutdown", data=b"", method="POST")
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}")
        except (OSError, urllib.error.URLError, RuntimeError) as exc:
            log(f"优雅停止请求失败，尝试停止托管进程：{exc}")
            _terminate_managed_process(managed_pid)
    else:
        _terminate_managed_process(managed_pid)

    for _ in range(120):
        if not port_open() and not _pid_alive(managed_pid) and not _pid_alive(listener_pid):
            break
        time.sleep(.25)
    if _pid_alive(managed_pid) or _pid_alive(listener_pid):
        _signal_groups(worker_groups, signal.SIGTERM)
        _signal_groups(gateway_groups, signal.SIGTERM)
        for _ in range(40):
            if not _pid_alive(managed_pid) and not _pid_alive(listener_pid):
                break
            time.sleep(.25)
    if _pid_alive(managed_pid) or _pid_alive(listener_pid):
        _signal_groups(worker_groups, signal.SIGKILL)
        _signal_groups(gateway_groups, signal.SIGKILL)
        time.sleep(.5)

    # A busy gateway can briefly spawn a replacement worker while it is shutting
    # down. Once the listener is gone, every remaining local model worker belongs
    # to the old instance and must be removed before the new gateway starts.
    remaining_worker_groups = worker_groups | _worker_pgids()
    _signal_groups(remaining_worker_groups, signal.SIGTERM)
    for _ in range(20):
        if not any(_group_alive(group) for group in remaining_worker_groups):
            break
        time.sleep(.25)
    _signal_groups(
        {group for group in remaining_worker_groups if _group_alive(group)},
        signal.SIGKILL,
    )
    if port_open() or _pid_alive(managed_pid) or _pid_alive(listener_pid):
        raise RuntimeError("旧网关未能完整停止，请查看 cache/_logs/gateway.log")
    PID_FILE.unlink(missing_ok=True)


def mysql_ready() -> bool:
    mysqladmin = command("mysqladmin", "/opt/homebrew/bin/mysqladmin")
    return subprocess.run(
        [mysqladmin, "ping", "-h", "127.0.0.1", "-u", "root", "--silent"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def ensure_mysql() -> None:
    if mysql_ready():
        return
    brew = command("brew", "/opt/homebrew/bin/brew")
    log("MySQL 未运行，正在启动 Homebrew 服务")
    subprocess.run([brew, "services", "start", "mysql"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if mysql_ready():
            return
        time.sleep(.5)
    raise RuntimeError("MySQL 启动超时，请检查 Homebrew mysql 服务")


def run_migration(conda: str) -> None:
    subprocess.run(
        [conda, "run", "-n", "vg-gateway", "alembic", "upgrade", "head"],
        cwd=ROOT, check=True, stdout=subprocess.DEVNULL,
        stderr=(LOG_DIR / "migration.log").open("a", encoding="utf-8"),
    )


def gateway_host(conda: str) -> str:
    """读取 models.yaml 里的 settings.host（默认 127.0.0.1）。"""
    try:
        out = subprocess.run(
            [conda, "run", "-n", "vg-gateway", "python", "-c",
             "from gateway.config import load_config; print(load_config().settings.host)"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip() or "127.0.0.1"
    except Exception:
        return "127.0.0.1"


def start_gateway(conda: str) -> None:
    gateway_log = (LOG_DIR / "gateway.log").open("a", encoding="utf-8")
    host = gateway_host(conda)
    log(f"网关监听地址 host={host}")
    process = subprocess.Popen(
        [conda, "run", "--no-capture-output", "-n", "vg-gateway", "uvicorn",
         "gateway.main:app", "--host", host, "--port", "8080"],
        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=gateway_log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    log(f"网关进程已启动 pid={process.pid}")
    for _ in range(120):
        if healthy():
            return
        if process.poll() is not None:
            raise RuntimeError(f"网关启动失败，退出码 {process.returncode}")
        time.sleep(.5)
    process.terminate()
    raise RuntimeError("网关健康检查超时")


def alert(message: str) -> None:
    escaped = message.replace('"', '\\"')
    subprocess.run(["osascript", "-e", f'display alert "VoiceGeneration 启动失败" message "{escaped}" as critical'], check=False)


def main() -> int:
    os.chdir(ROOT)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        prepare_path()
        ensure_mysql()
        conda = command("conda", "/Users/kanxiao/miniconda3/bin/conda")
        stop_running_gateway()
        run_migration(conda)
        start_gateway(conda)
        subprocess.run(["open", URL], check=False)
        return 0
    except Exception as exc:
        log(f"启动失败: {exc}")
        alert(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
