#!/usr/bin/env python3
"""Launch VoiceGeneration quietly from the macOS application bundle."""
from __future__ import annotations

import os
import shutil
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


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "launcher.log").open("a", encoding="utf-8") as stream:
        stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def command(name: str, fallback: str) -> str:
    return shutil.which(name) or fallback


def healthy() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=1.5) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


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


def start_gateway(conda: str) -> None:
    gateway_log = (LOG_DIR / "gateway.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        [conda, "run", "--no-capture-output", "-n", "vg-gateway", "uvicorn",
         "gateway.main:app", "--host", "127.0.0.1", "--port", "8080"],
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
        # 网关仍在时 MySQL 也可能被 Homebrew 单独停止，先恢复数据库再复用网关。
        ensure_mysql()
        if healthy():
            log("检测到现有服务，仅打开工作台")
            subprocess.run(["open", URL], check=False)
            return 0
        conda = command("conda", "/Users/kanxiao/miniconda3/bin/conda")
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
