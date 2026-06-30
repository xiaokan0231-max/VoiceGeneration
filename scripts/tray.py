#!/usr/bin/env python3
"""VoiceGeneration 统一托盘程序（macOS 菜单栏 / Windows 托盘，同一套逻辑）。

- 启动时按 models.yaml 的 cluster.role 运行；role 为空 → 菜单里选「主服务器 / 副节点」。
- 选定角色后启动并**看护**对应后端子进程（崩溃自动按退避重启）：
    coordinator → uvicorn gateway.main:app  (:8080 工作台)
    agent       → python -m gateway.agent   (:8090 副节点控制台 + 认领循环)
- 每 3s 轮询后端状态，更新托盘图标(在线/离线) + 悬停提示。
- 菜单：状态 · 打开网页 · 重启/启停后端 · 切换角色 · 开机自启 · 退出。
退出托盘 = 停掉本机后端（托盘是宿主）。
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.request import Request, urlopen

# 让脚本既能 `python scripts/tray.py` 也能在 .app 里运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pystray  # noqa: E402
from PIL import Image  # noqa: E402

from gateway.config import load_config, load_raw_models, save_raw_models  # noqa: E402

PKG = ROOT / "packaging"
LOG_DIR = ROOT / "logs"
ROLE_NAMES = {"coordinator": "主服务器", "agent": "副节点", "": "未选择角色"}


# --------------------------------------------------------------------------- #
# 开机自启（mac=LaunchAgent / windows=注册表 Run）
# --------------------------------------------------------------------------- #
def _autostart_command() -> list[str]:
    if getattr(sys, "frozen", False):       # PyInstaller 打包后的 exe
        return [sys.executable]
    return [sys.executable, os.path.abspath(sys.argv[0])]


_MAC_PLIST = Path.home() / "Library/LaunchAgents/local.voicegeneration.tray.plist"
_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_WIN_VALUE = "VoiceGeneration"


def autostart_enabled() -> bool:
    if sys.platform == "darwin":
        return _MAC_PLIST.exists()
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY) as k:
                winreg.QueryValueEx(k, _WIN_VALUE)
            return True
        except OSError:
            return False
    return False


def set_autostart(on: bool) -> None:
    if sys.platform == "darwin":
        if on:
            args = "".join(f"    <string>{a}</string>\n" for a in _autostart_command())
            _MAC_PLIST.parent.mkdir(parents=True, exist_ok=True)
            _MAC_PLIST.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>\n'
                '  <key>Label</key><string>local.voicegeneration.tray</string>\n'
                f'  <key>ProgramArguments</key><array>\n{args}  </array>\n'
                '  <key>RunAtLoad</key><true/>\n'
                '  <key>ProcessType</key><string>Interactive</string>\n'
                '</dict></plist>\n', encoding="utf-8")
            subprocess.run(["launchctl", "load", str(_MAC_PLIST)], capture_output=True)
        else:
            subprocess.run(["launchctl", "unload", str(_MAC_PLIST)], capture_output=True)
            _MAC_PLIST.unlink(missing_ok=True)
    elif os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, _WIN_VALUE, 0, winreg.REG_SZ,
                                  subprocess.list2cmdline(_autostart_command()))
            else:
                try:
                    winreg.DeleteValue(k, _WIN_VALUE)
                except OSError:
                    pass


# --------------------------------------------------------------------------- #
def _load_icon(name: str) -> Image.Image:
    path = PKG / name
    if path.exists():
        return Image.open(path)
    import make_icons  # 兜底：现画一个
    return make_icons._render(128, with_bg=False,
                              color=make_icons.COPPER if "off" not in name else make_icons.GRAY)


def _http_json(url: str, token: str = "", timeout: float = 3.0) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with urlopen(Request(url, headers=headers), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _child_env() -> dict:
    """后端子进程的环境变量：从 Finder/launchd 启动时 PATH 极简，会找不到
    ffmpeg / ffprobe / brew / mysql（它们在 /opt/homebrew/bin）。这里补回常见路径，
    否则协调端转码失败、brew 调用报错。"""
    env = os.environ.copy()
    if os.name != "nt":
        extra = "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin"
        env["PATH"] = extra + os.pathsep + env.get("PATH", "")
    return env


def _find_brew() -> str | None:
    for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if os.path.exists(p):
            return p
    return shutil.which("brew")


def _notify(title: str, message: str) -> None:
    """尽力而为的系统通知：菜单栏程序没有窗口/Dock 图标，启动后容易被以为「没反应」。"""
    try:
        if sys.platform == "darwin":
            subprocess.run(
                ["osascript", "-e", f'display notification "{message}" with title "{title}"'],
                capture_output=True, timeout=5)
    except Exception:  # noqa: BLE001
        pass


class TrayApp:
    def __init__(self) -> None:
        cfg = load_config()
        self.role = cfg.settings.cluster.role or ""
        self.host = cfg.settings.host
        self.port = cfg.settings.port
        self.agent_port = cfg.settings.cluster.agent_port
        self.token = cfg.settings.api_token
        self.proc: subprocess.Popen | None = None
        self.logfile = None
        self.want = bool(self.role)         # 是否应保持后端运行
        self.online = False
        self.status_text = "未选择角色"
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self.icon_on = _load_icon("tray.png")
        self.icon_off = _load_icon("tray_off.png")
        # macOS：必须在创建状态栏项【之前】把进程设为正常 UI 应用。launchd 经 .app 启动器
        # exec python 后丢了 .app 的 LSUIElement，进程默认是 Prohibited(2)，状态栏图标根本
        # 不显示；而且策略要在 NSStatusItem 创建前设好，事后再改无法「复活」已建的状态项。
        # 用 Regular(0)：会有一个 Dock 图标，但状态栏图标稳定显示。
        if sys.platform == "darwin":
            try:
                import AppKit
                AppKit.NSApplication.sharedApplication().setActivationPolicy_(
                    AppKit.NSApplicationActivationPolicyRegular)
            except Exception:  # noqa: BLE001
                pass
        self.icon = pystray.Icon("voicegeneration", self.icon_off,
                                 "VoiceGeneration", menu=self._build_menu())

    # ---- 后端进程命令 ---------------------------------------------------- #
    def _backend_cmd(self, role: str) -> list[str]:
        if role == "coordinator":
            return [sys.executable, "-m", "uvicorn", "gateway.main:app",
                    "--host", self.host, "--port", str(self.port)]
        return [sys.executable, "-m", "gateway.agent"]

    def _spawn(self, role: str) -> None:
        LOG_DIR.mkdir(exist_ok=True)
        self.logfile = open(LOG_DIR / f"{role}.log", "a", buffering=1, encoding="utf-8")
        env = _child_env()
        kwargs: dict = {"cwd": str(ROOT), "stdout": self.logfile,
                        "stderr": subprocess.STDOUT, "env": env}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        if role == "coordinator":
            if sys.platform == "darwin":
                brew = _find_brew()
                if brew:  # 尽力而为：拉起 MySQL，失败/无 brew 都不影响托盘
                    try:
                        subprocess.run([brew, "services", "start", "mysql"],
                                       capture_output=True, env=env, timeout=60)
                    except Exception:  # noqa: BLE001
                        pass
            # 与 run_gateway.sh 对齐：起服务前先把数据库迁到最新（尽力而为）
            try:
                subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                               cwd=str(ROOT), capture_output=True, env=env, timeout=120)
            except Exception:  # noqa: BLE001
                pass
        self.proc = subprocess.Popen(self._backend_cmd(role), **kwargs)

    def _kill(self) -> None:
        proc = self.proc
        if not proc or proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                proc.terminate()
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=8)
        except Exception:  # noqa: BLE001
            try:
                if os.name == "nt":
                    proc.kill()
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:  # noqa: BLE001
                pass
        finally:
            if self.logfile:
                self.logfile.close()
                self.logfile = None

    # ---- 看护线程：want 为真且进程不在 → 按退避重启 ---------------------- #
    def _supervise(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            with self.lock:
                want, role = self.want, self.role
            if want and role and (self.proc is None or self.proc.poll() is not None):
                try:
                    self._spawn(role)
                except Exception:  # noqa: BLE001 — 任何启动错误都不许杀死看护线程
                    backoff = min(backoff * 2, 30)
                    self.status_text = f"{ROLE_NAMES.get(role, role)} · 启动失败，{backoff}s 后重试"
                    self._stop.wait(backoff)
                    continue
                started = time.monotonic()
                while not self._stop.is_set():
                    with self.lock:
                        if not self.want:
                            break
                    if self.proc.poll() is not None:        # 子进程退出了
                        backoff = min(backoff * 2, 30) if time.monotonic() - started < 12 else 1
                        self._stop.wait(backoff)
                        break
                    self._stop.wait(0.5)
            else:
                self._stop.wait(0.5)

    # ---- 状态轮询线程 ---------------------------------------------------- #
    def _poll(self) -> None:
        while not self._stop.is_set():
            with self.lock:
                want, role = self.want, self.role
            if not (want and role):
                self.online = False
                self.status_text = "未选择角色" if not role else f"{ROLE_NAMES[role]} · 已停止"
            else:
                try:
                    if role == "coordinator":
                        _http_json(f"http://127.0.0.1:{self.port}/health")
                        d = _http_json(f"http://127.0.0.1:{self.port}/v1/cluster/nodes", self.token)
                        n = len(d.get("nodes", []))
                        self.status_text = f"主服务器 · 运行中 · 节点 {n} · 队列 {d.get('queue_depth', 0)}"
                    else:
                        d = _http_json(f"http://127.0.0.1:{self.agent_port}/api/status")
                        state = {"connected": "已连接", "connecting": "连接中",
                                 "disconnected": "未连接"}.get(d.get("connection_state"), "未连接")
                        self.status_text = (f"副节点 · {state} · "
                                            f"执行 {d.get('inflight', 0)}/{d.get('total_slots', 0)}")
                    self.online = True
                except Exception:  # noqa: BLE001
                    self.online = False
                    self.status_text = f"{ROLE_NAMES[role]} · 启动中…"
            self._push_ui()
            self._stop.wait(3)

    # ---- 配置落盘 -------------------------------------------------------- #
    def _persist_role(self, role: str) -> None:
        raw = load_raw_models()
        raw.setdefault("settings", {}).setdefault("cluster", {})["role"] = role
        save_raw_models(raw)

    # ---- 菜单动作 -------------------------------------------------------- #
    def _open_ui(self) -> None:
        url = (f"http://localhost:{self.port}/" if self.role == "coordinator"
               else f"http://127.0.0.1:{self.agent_port}/")
        webbrowser.open(url)

    def _set_role(self, role: str) -> None:
        with self.lock:
            if role == self.role and self.want:
                return
            self._kill()
            self.role = role
            self.want = True
            self._persist_role(role)
        self.icon.update_menu()

    def _restart(self) -> None:
        with self.lock:
            self._kill()                    # 看护线程会自动拉起
            self.want = True
        self.icon.update_menu()

    def _toggle_backend(self) -> None:
        with self.lock:
            self.want = not self.want
            if not self.want:
                self._kill()
        self.icon.update_menu()

    def _toggle_autostart(self) -> None:
        set_autostart(not autostart_enabled())
        self.icon.update_menu()

    def _quit(self) -> None:
        self._stop.set()
        with self.lock:
            self.want = False
            self._kill()
        self.icon.stop()

    # ---- 菜单结构（用 callable 控制 文本/可见/勾选，开菜单时实时求值）--- #
    def _build_menu(self) -> pystray.Menu:
        Item = pystray.MenuItem
        chosen = lambda item: bool(self.role)        # noqa: E731
        unchosen = lambda item: not self.role        # noqa: E731
        return pystray.Menu(
            Item(lambda item: self.status_text, None, enabled=lambda item: False),
            pystray.Menu.SEPARATOR,
            Item("启动为主服务器", lambda: self._set_role("coordinator"), visible=unchosen),
            Item("启动为副节点", lambda: self._set_role("agent"), visible=unchosen),
            Item(lambda item: "打开工作台" if self.role == "coordinator" else "打开副节点控制台",
                 lambda: self._open_ui(), visible=chosen, default=True),
            Item(lambda item: "停止后端" if self.want else "启动后端",
                 lambda: self._toggle_backend(), visible=chosen),
            Item("重启后端", lambda: self._restart(), visible=chosen),
            Item("角色", pystray.Menu(
                Item("主服务器", lambda: self._set_role("coordinator"),
                     checked=lambda item: self.role == "coordinator", radio=True),
                Item("副节点", lambda: self._set_role("agent"),
                     checked=lambda item: self.role == "agent", radio=True),
            ), visible=chosen),
            pystray.Menu.SEPARATOR,
            Item("开机自启", lambda: self._toggle_autostart(),
                 checked=lambda item: autostart_enabled()),
            Item("退出", lambda: self._quit()),
        )

    # ---- UI 更新必须回到主线程（macOS AppKit 非线程安全）---------------- #
    def _apply_ui(self) -> None:
        self.icon.icon = self.icon_on if self.online else self.icon_off
        self.icon.title = f"VoiceGeneration\n{self.status_text}"

    def _push_ui(self) -> None:
        if sys.platform == "darwin":
            try:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(self._apply_ui)
                return
            except Exception:  # noqa: BLE001
                pass
        self._apply_ui()

    def _setup(self, icon) -> None:
        """run loop 就绪后再起线程，避免启动期跨线程访问 AppKit。"""
        icon.visible = True
        threading.Thread(target=self._supervise, daemon=True).start()
        threading.Thread(target=self._poll, daemon=True).start()

    def run(self) -> None:
        tip = {"coordinator": "主服务器启动中 · 点菜单栏图标→打开工作台",
               "agent": "副节点启动中 · 点菜单栏图标→打开控制台"}.get(
            self.role, "已启动 · 点菜单栏右上角图标选择角色")
        _notify("VoiceGeneration 已在菜单栏运行", tip)
        self.icon.run(setup=self._setup)


if __name__ == "__main__":
    TrayApp().run()
