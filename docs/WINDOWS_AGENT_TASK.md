# Windows 托盘程序 —— 给 Codex 的实现任务书

> 这份文档交给 **Windows 上的 Codex** 执行。目标：让这台 Windows（RTX 4060）以
> **和 Mac 完全相同的逻辑**跑起 VoiceGeneration 的托盘程序，并打包/设置开机自启后「发布」。
>
> **核心前提：跨平台托盘逻辑已经写好，就是仓库里的 [`scripts/tray.py`](../scripts/tray.py)。
> 你要复用它，不要重写。** Mac 端已用它构建 `.app` 并验证通过；它内部已经处理了 Windows
> 的分支（`CREATE_NEW_PROCESS_GROUP` 进程组、`winreg` 开机自启、托盘图标）。
> 你的活儿是：装环境 → 用 vg-gateway 的 python 跑 `tray.py` → 打包成 `VoiceGeneration.exe` →
> 设好开机自启 → 验收。

---

## 0. 这个程序是什么（运行模型，和 Mac 一致）

点托盘图标启动 `tray.py`：

```
托盘程序 tray.py
  ├─ 读 models.yaml 的 cluster.role
  ├─ role 为空 → 托盘菜单出现「启动为主服务器 / 启动为副节点」，选了才落盘+启动
  ├─ role = coordinator → 看护后端 uvicorn gateway.main:app   (:8080 工作台)
  ├─ role = agent       → 看护后端 python -m gateway.agent     (:8090 副节点控制台 + 认领循环)
  └─ 每 3s 轮询后端状态 → 托盘图标在线/离线 + 悬停提示；崩溃自动按退避重启
```

Windows 这台机器**通常选「副节点(agent)」**（主服务器在 Mac 上）。但程序两种角色都支持，
菜单里可随时「角色 ▸ 主服务器 / 副节点」切换，逻辑与 Mac 不变。

托盘菜单项：状态行 · 打开网页 · 停止/启动后端 · 重启后端 · 角色切换 · 开机自启(勾选) · 退出。
**退出托盘 = 停掉本机后端**（托盘是宿主进程）。

---

## 1. 环境准备

仓库已 `git clone`/`git pull` 到本机（假设根目录 `%REPO%`，例如 `D:\VoiceGeneration`）。
**模型在磁盘上跑、worker 用各自的 conda 环境从仓库目录启动，所以仓库必须保留在磁盘上——
打不打 exe 都改变不了这一点。**

1. **网关环境 `vg-gateway`（python 3.11）** —— 托盘和后端都用它：
   ```powershell
   conda create -y -n vg-gateway python=3.11
   conda activate vg-gateway
   pip install -r requirements-gateway.txt   # 已含 pystray / pillow
   ```
   > `requirements-gateway.txt` 里已经加了 `pystray>=0.19`、`pillow>=10.0`，pystray 会自动选用
   > Windows 托盘后端，无需额外配置。

2. **模型环境（副节点要跑推理）** —— 按 `models.yaml` 里各模型的 `python` 字段：
   - `vg-cosyvoice`、`vg-f5` 等，**torch 装 CUDA 版**（cu121/cu124，配 4060 驱动），
     验证 `python -c "import torch;print(torch.cuda.is_available())"` 为 `True`。
   - 参考 `scripts/setup_worker.sh` 的依赖清单（Windows 上手动 `pip install` 对应包）。

3. **图标**：`packaging/AppIcon.ico` 和 `packaging/tray.png` / `packaging/tray_off.png` 已由
   Mac 端 `scripts/make_icons.py` 生成并提交进仓库。若缺失，运行
   `python scripts/make_icons.py` 重新生成（Windows 上会跳过 `.icns`，只出 `.ico` + 托盘 png）。

---

## 2. 配置（与协调端对接）

副节点的连接信息从**主机网页**复制：Mac 工作台 →「服务设置 → 副节点接入信息」卡，
里面有协调端 URL、token、建议的 node_id。把它们写进本机 `models.yaml` 的 `settings.cluster`：

```yaml
settings:
  cluster:
    role: agent                 # 让 tray 直接以副节点启动；留空则启动后在菜单里选
    coordinator_url: "http://<主机IP>:8080"   # 从「副节点接入信息」复制
    token: "<如有>"
    node_id: win-4060
    node_name: "Windows 4060"
    agent_host: 127.0.0.1
    agent_port: 8090
    enabled: true               # 是否主动连接协调端（也可在 :8090 控制台点「连接」）
```

> 也可用环境变量覆盖：`VG_CLUSTER_ROLE / VG_COORDINATOR_URL / VG_CLUSTER_TOKEN /
> VG_NODE_ID / VG_NODE_NAME`（见 `gateway/config.py` 的 `load_config`）。

`models.yaml` 不进 git（含密钥/私有信息），每台机器本地维护。可拷 `models.example.yaml` 起步。

### 与协调端的接口契约（tray/agent 已实现，仅供你核对，**不要改协议**）

副节点 `python -m gateway.agent` 会向协调端调用这些路由（见 `gateway/main.py`）：

| 路由 | 作用 |
|---|---|
| `POST /v1/cluster/register` | 注册本节点 + 上报模型/容量 |
| `POST /v1/cluster/lease` | 按容量认领任务（`FOR UPDATE SKIP LOCKED` 原子租约） |
| `GET  /v1/cluster/asset/{voice_id}` | 拉取克隆音色参考音频 |
| `POST /v1/cluster/jobs/{job_id}/result` / `/fail` / `/heartbeat` | 回传结果 / 失败 / 续租 |
| `GET  /v1/cluster/nodes` | 集群节点与队列深度（托盘主服务器模式读它做状态） |

副节点本机控制台 `:8090`（`gateway/agent.py` 的 `build_agent_app`）提供
`/api/status`、`/api/jobs`、`/api/config`、`/api/connect`、`/api/disconnect`、`/api/coordinator`，
托盘的 agent 状态就读 `:8090/api/status`。

### 网络（重要）

- Windows ↔ Mac 必须互通：**同一局域网**直接用内网 IP，或装 **Tailscale** 用 100.x 段地址。
- 系统若开了代理/TUN（Clash 等），LAN/Tailscale 段要走直连。agent 的 httpx 已设
  `trust_env=False` 绕过系统代理（这是之前 502 的修复），但仍请确认能
  `curl http://<主机IP>:8080/health` 返回 200。

---

## 3. 你要产出的文件（Windows 专属，放 `scripts/` 或 `packaging/windows/`）

> `tray.py`、`make_icons.py`、`gateway/*` 都已存在，**不要重写**。下面是 Windows 入口/打包/自启。

### 3.1 `scripts/start_tray.bat`（开发期直接双击启动，无控制台黑窗）
- 切到仓库根目录。
- 解析 vg-gateway 的 **`pythonw.exe`**（无窗口）。conda 定位逻辑可照抄
  `scripts/agent.ps1` 里那段 `$condaCandidates`（CONDA_EXE / `%USERPROFILE%\miniconda3` / `anaconda3` …）。
- `start "" "<...\envs\vg-gateway\pythonw.exe>" "%REPO%\scripts\tray.py"`。
- 期望行为：双击后右下角出现托盘图标；无黑色 cmd 窗口残留。

### 3.2 `scripts/install_autostart.ps1`（登录自启，二选一，**推荐 A**）
- **A. 内置开关（最省事）**：直接在托盘菜单点「开机自启」即可——`tray.py` 已实现
  Windows 注册表 `HKCU\...\Run` 写入（`set_autostart`）。这个脚本只需提示用户用菜单开关，
  或等价地写一条 Run 值指向 `pythonw.exe tray.py`。
- **B. 计划任务（更稳，掉了会重启）**：用 `Register-ScheduledTask` 建任务：
  - 触发器：**用户登录时**；
  - 操作：`pythonw.exe "%REPO%\scripts\tray.py"`；
  - 设置：**失败后重启**（RestartCount≥3、RestartInterval 1 分钟）、AllowStartIfOnBatteries、不超时。
- 同时提供 `uninstall_autostart.ps1` 反向操作。

### 3.3 打包成 `VoiceGeneration.exe`（PyInstaller，可选但用户要「发布」，请产出）
- 用 vg-gateway 环境：`pip install pyinstaller`。
- 生成 `packaging/windows/VoiceGeneration.spec`，关键参数：
  - **windowed**（`console=False`，等价 `--noconsole`）；
  - `--icon ..\AppIcon.ico`（用 `packaging/AppIcon.ico`）；
  - 入口 `scripts/tray.py`；
  - `--add-data` 打包 `packaging/tray.png;packaging`、`packaging/tray_off.png;packaging`；
  - hidden-import：`pystray._win32`、`PIL.Image`（按需补全 `gateway.*`）。
- **关键坑（务必处理）**：`tray.py` 用 `ROOT = Path(__file__).parent.parent` 推断仓库根；
  PyInstaller 冻结后 `__file__` 在临时解包目录，会指错。请让 exe **以仓库根为工作目录运行**
  （快捷方式「起始位置」设为 `%REPO%`，或在 spec 里用 runtime hook 把 cwd/ROOT 指回真实仓库），
  否则它找不到 `models.yaml` 和模型 conda 环境。`tray.py` 已对 `sys.frozen` 做了开机自启分支
  （冻结时自启命令用 `sys.executable` 单独指向该 exe）。
- 产物 `dist\VoiceGeneration.exe`，配 `packaging/AppIcon.ico` 显示图标。

> **若 PyInstaller 的路径问题难搞，可降级为「方案 A」**：不打 exe，直接用
> `pythonw.exe scripts/tray.py` + 桌面/开始菜单快捷方式（快捷方式图标指
> `packaging\AppIcon.ico`，起始位置 `%REPO%`）。功能完全一致，且没有冻结路径坑。
> 这台机器无论如何都需要仓库在磁盘上（worker 从仓库目录用 conda 环境启动），所以不打 exe
> 并不会损失「绿色单文件」之外的任何东西。**优先保证能跑、能自启、不挂。**

---

## 4. 验收清单（逐条自测）

1. `conda activate vg-gateway; python -c "import pystray, PIL; print('ok')"` 通过。
2. 双击 `start_tray.bat`（或 exe）→ 右下角出现托盘图标，无残留黑窗。
3. `models.yaml` 里 `role: agent` 时：托盘直接以副节点启动；悬停显示
   「副节点 · 已连接/未连接 · 执行 K/总槽位」。
4. 托盘菜单「打开副节点控制台」→ 浏览器打开 `http://127.0.0.1:8090`，能看到状态/任务池，
   能「连接/断开」协调端。
5. 在 Mac 工作台提交一条 TTS 任务 → Windows 控制台「正在执行」出现该任务，完成后历史里
   「由 <node_name> 生成」显示这台 Windows。
6. **看护**：用任务管理器结束后端 python 进程 → 托盘几秒内自动把它拉起（状态短暂离线后恢复）。
7. **角色切换**：菜单「角色 ▸ 主服务器」→ 落盘 `role=coordinator` 并改起 `:8080`；再切回副节点正常。
8. **开机自启**：勾选后注销/重登（或重启）→ 登录即自动出现托盘并连上协调端。
9. 退出托盘 → 后端进程随之停止（无孤儿 python 占着 8090/GPU）。

把以上结果回报；如某项不过，**优先改 Windows 入口/打包脚本，不要改 `tray.py` 的跨平台逻辑或集群协议**。
（若确认是 `tray.py` 的 Windows 分支 bug，可最小化修复并在 PR 里说明。）

---

## 5. 参考文件一览

- 托盘逻辑（复用，勿重写）：`scripts/tray.py`
- 图标生成：`scripts/make_icons.py` → `packaging/AppIcon.ico` / `tray.png` / `tray_off.png`
- 副节点后端 + :8090 控制台：`gateway/agent.py`
- 协调端路由：`gateway/main.py`（`/v1/cluster/*`）
- 配置加载/字段：`gateway/config.py`
- 现成的 PowerShell 启动样例（conda 定位可抄）：`scripts/agent.ps1`
- 集群说明：`docs/CLUSTER.md`
