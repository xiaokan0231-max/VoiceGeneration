#!/usr/bin/env python3
"""VoiceGeneration 菜单栏/托盘程序。

- macOS：用原生 rumps 显示菜单栏图标（状态项在主线程创建/刷新，稳定显示）。
- Windows / 其它：用 pystray 托盘。
- 两端共用同一套后端看护逻辑（BackendController）：
    coordinator → uvicorn gateway.main:app  (:8080 工作台)
    agent       → python -m gateway.agent   (:8090 副节点控制台 + 认领循环)
  按 models.yaml 的 cluster.role 启动；role 为空 → 菜单里选「主服务器 / 副节点」。
  看护子进程(崩溃自动按退避重启)，每 3s 轮询状态刷新图标/提示。退出 = 停本机后端。
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
def _http_json(url: str, token: str = "", timeout: float = 3.0) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with urlopen(Request(url, headers=headers), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _child_env() -> dict:
    """后端子进程的环境变量：从 Finder/launchd 启动时 PATH 极简，会找不到
    ffmpeg / ffprobe / brew / mysql（它们在 /opt/homebrew/bin）。这里补回常见路径。"""
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
    """尽力而为的系统通知。"""
    try:
        if sys.platform == "darwin":
            subprocess.run(
                ["osascript", "-e", f'display notification "{message}" with title "{title}"'],
                capture_output=True, timeout=5)
    except Exception:  # noqa: BLE001
        pass


def _tip(role: str) -> str:
    return {"coordinator": "主服务器启动中 · 点图标→打开工作台",
            "agent": "副节点启动中 · 点图标→打开控制台"}.get(
        role, "已启动 · 点菜单栏/托盘图标选择角色")


# --------------------------------------------------------------------------- #
# 与界面无关的后端看护逻辑（mac/win 共用）
# --------------------------------------------------------------------------- #
class BackendController:
    def __init__(self) -> None:
        cfg = load_config()
        self.role = cfg.settings.cluster.role or ""
        self.host = cfg.settings.host
        self.port = cfg.settings.port
        self.agent_port = cfg.settings.cluster.agent_port
        self.token = cfg.settings.api_token
        self.proc: subprocess.Popen | None = None
        self.logfile = None
        self.want = bool(self.role)
        self.online = False
        self.status_text = self._idle_text()
        self.lock = threading.RLock()
        self._stop = threading.Event()

    def _idle_text(self) -> str:
        return "未选择角色" if not self.role else f"{ROLE_NAMES[self.role]} · 已停止"

    # ---- 子进程 -------------------------------------------------------- #
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
                if brew:
                    try:
                        subprocess.run([brew, "services", "start", "mysql"],
                                       capture_output=True, env=env, timeout=60)
                    except Exception:  # noqa: BLE001
                        pass
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

    def _supervise(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            with self.lock:
                want, role = self.want, self.role
            if want and role and (self.proc is None or self.proc.poll() is not None):
                try:
                    self._spawn(role)
                except Exception:  # noqa: BLE001 — 启动出错也不许杀死看护线程
                    backoff = min(backoff * 2, 30)
                    self.status_text = f"{ROLE_NAMES.get(role, role)} · 启动失败，{backoff}s 后重试"
                    self._stop.wait(backoff)
                    continue
                started = time.monotonic()
                while not self._stop.is_set():
                    with self.lock:
                        if not self.want:
                            break
                    if self.proc.poll() is not None:
                        backoff = min(backoff * 2, 30) if time.monotonic() - started < 12 else 1
                        self._stop.wait(backoff)
                        break
                    self._stop.wait(0.5)
            else:
                self._stop.wait(0.5)

    def start(self) -> None:
        threading.Thread(target=self._supervise, daemon=True).start()

    # ---- 状态轮询（前端定时调用，更新 online/status_text；不碰 UI）------ #
    def refresh_status(self) -> None:
        with self.lock:
            want, role = self.want, self.role
        if not (want and role):
            self.online = False
            self.status_text = self._idle_text()
            return
        try:
            if role == "coordinator":
                _http_json(f"http://127.0.0.1:{self.port}/health")
                d = _http_json(f"http://127.0.0.1:{self.port}/v1/cluster/nodes", self.token)
                self.status_text = (f"主服务器 · 运行中 · 节点 {len(d.get('nodes', []))} · "
                                    f"队列 {d.get('queue_depth', 0)}")
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

    # ---- 动作（前端菜单调用）------------------------------------------ #
    def persist_role(self, role: str) -> None:
        raw = load_raw_models()
        raw.setdefault("settings", {}).setdefault("cluster", {})["role"] = role
        save_raw_models(raw)

    def set_role(self, role: str) -> None:
        with self.lock:
            if role == self.role and self.want:
                return
            self._kill()
            self.role = role
            self.want = True
            self.persist_role(role)

    def restart(self) -> None:
        with self.lock:
            self._kill()
            self.want = True

    def toggle_backend(self) -> None:
        with self.lock:
            self.want = not self.want
            if not self.want:
                self._kill()

    def open_ui(self) -> None:
        url = (f"http://localhost:{self.port}/" if self.role == "coordinator"
               else f"http://127.0.0.1:{self.agent_port}/")
        webbrowser.open(url)

    def stop_all(self) -> None:
        self._stop.set()
        with self.lock:
            self.want = False
            self._kill()

    @property
    def other_role(self) -> str:
        return "agent" if self.role == "coordinator" else "coordinator"


# --------------------------------------------------------------------------- #
# macOS：rumps 菜单栏
# --------------------------------------------------------------------------- #
def run_mac(ctrl: BackendController) -> None:
    import rumps

    icon_on = str(PKG / "tray.png")
    icon_off = str(PKG / "tray_off.png")

    def has(p: str) -> str | None:
        return p if os.path.exists(p) else None

    class MacTray(rumps.App):
        def __init__(self) -> None:
            # title 文本保证可见（即便图标渲染异常也能看到 “VG”）
            super().__init__("VoiceGeneration", title="VG",
                             icon=has(icon_off), template=False, quit_button=None)
            self._build()
            rumps.Timer(self._tick, 3).start()

        # 重新生成菜单（角色/开关/勾选变化时调用）
        def _build(self) -> None:
            self.menu.clear()
            self._status = rumps.MenuItem(ctrl.status_text)      # 状态行
            items = [self._status, None]
            if not ctrl.role:
                items += [rumps.MenuItem("启动为主服务器", callback=self._role("coordinator")),
                          rumps.MenuItem("启动为副节点", callback=self._role("agent"))]
            else:
                items.append(rumps.MenuItem(
                    "打开工作台" if ctrl.role == "coordinator" else "打开副节点控制台",
                    callback=lambda _: ctrl.open_ui()))
                items.append(rumps.MenuItem("停止后端" if ctrl.want else "启动后端",
                                            callback=lambda _: self._act(ctrl.toggle_backend)))
                items.append(rumps.MenuItem("重启后端", callback=lambda _: ctrl.restart()))
                items.append(rumps.MenuItem(f"切换为{ROLE_NAMES[ctrl.other_role]}",
                                            callback=self._role(ctrl.other_role)))
            auto = rumps.MenuItem("开机自启", callback=lambda _: self._autostart())
            auto.state = autostart_enabled()
            items += [None, auto, rumps.MenuItem("退出", callback=lambda _: self._quit())]
            for it in items:
                self.menu.add(rumps.separator if it is None else it)

        def _role(self, role: str):
            return lambda _: self._act(lambda: ctrl.set_role(role))

        def _act(self, fn) -> None:
            fn()
            self._build()

        def _autostart(self) -> None:
            set_autostart(not autostart_enabled())
            self._build()

        def _tick(self, _) -> None:
            ctrl.refresh_status()
            try:
                self._status.title = ctrl.status_text
                self.icon = icon_on if ctrl.online else icon_off
            except Exception:  # noqa: BLE001
                pass

        def _quit(self) -> None:
            ctrl.stop_all()
            rumps.quit_application()

    ctrl.start()
    _notify("VoiceGeneration 已在菜单栏运行", _tip(ctrl.role))
    app = MacTray()
    if os.environ.get("VG_TRAY_SELFTEST"):      # 自测：跑几秒自动退出
        rumps.Timer(lambda _: rumps.quit_application(),
                    float(os.environ["VG_TRAY_SELFTEST"])).start()
    app.run()


# --------------------------------------------------------------------------- #
# Windows / 其它：pystray 托盘
# --------------------------------------------------------------------------- #
def _load_icon(name: str):
    from PIL import Image
    path = PKG / name
    if path.exists():
        return Image.open(path)
    import make_icons
    return make_icons._render(128, with_bg=False,
                              color=make_icons.COPPER if "off" not in name else make_icons.GRAY)


def run_pystray(ctrl: BackendController) -> None:
    import pystray

    icon_on = _load_icon("tray.png")
    icon_off = _load_icon("tray_off.png")
    Item = pystray.MenuItem

    def menu() -> pystray.Menu:
        chosen = lambda i: bool(ctrl.role)        # noqa: E731
        unchosen = lambda i: not ctrl.role        # noqa: E731
        return pystray.Menu(
            Item(lambda i: ctrl.status_text, None, enabled=lambda i: False),
            pystray.Menu.SEPARATOR,
            Item("启动为主服务器", lambda: act(lambda: ctrl.set_role("coordinator")), visible=unchosen),
            Item("启动为副节点", lambda: act(lambda: ctrl.set_role("agent")), visible=unchosen),
            Item(lambda i: "打开工作台" if ctrl.role == "coordinator" else "打开副节点控制台",
                 lambda: ctrl.open_ui(), visible=chosen, default=True),
            Item(lambda i: "停止后端" if ctrl.want else "启动后端",
                 lambda: act(ctrl.toggle_backend), visible=chosen),
            Item("重启后端", lambda: ctrl.restart(), visible=chosen),
            Item("角色", pystray.Menu(
                Item("主服务器", lambda: act(lambda: ctrl.set_role("coordinator")),
                     checked=lambda i: ctrl.role == "coordinator", radio=True),
                Item("副节点", lambda: act(lambda: ctrl.set_role("agent")),
                     checked=lambda i: ctrl.role == "agent", radio=True),
            ), visible=chosen),
            pystray.Menu.SEPARATOR,
            Item("开机自启", lambda: (set_autostart(not autostart_enabled()), icon.update_menu()),
                 checked=lambda i: autostart_enabled()),
            Item("退出", lambda: (ctrl.stop_all(), icon.stop())),
        )

    icon = pystray.Icon("voicegeneration", icon_off, "VoiceGeneration", menu=menu())

    def act(fn) -> None:
        fn()
        icon.update_menu()

    def poll() -> None:
        while not ctrl._stop.is_set():
            ctrl.refresh_status()
            icon.icon = icon_on if ctrl.online else icon_off
            icon.title = f"VoiceGeneration\n{ctrl.status_text}"
            ctrl._stop.wait(3)

    def setup(ic) -> None:
        ic.visible = True
        threading.Thread(target=poll, daemon=True).start()

    ctrl.start()
    _notify("VoiceGeneration 已在托盘运行", _tip(ctrl.role))
    icon.run(setup=setup)


if __name__ == "__main__":
    controller = BackendController()
    if sys.platform == "darwin":
        run_mac(controller)
    else:
        run_pystray(controller)
