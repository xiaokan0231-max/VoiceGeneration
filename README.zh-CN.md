# VoiceGeneration

一个本地、可插拔的文本转语音服务——在多个 TTS 模型之上提供统一的 REST API，具备语音克隆、项目管理、生成历史、React 网页工作台、多机集群以及跨平台托盘程序。

[English](README.md) · **简体中文** · [日本語](README.ja.md)

VoiceGeneration 运行在你自己的机器上（以 Apple Silicon 为主，可选 CUDA 工作节点）。它对外暴露单一的 REST API，在其后承载多个可插拔的 TTS 引擎（CosyVoice3、F5-TTS、macOS `say`），支持零样本语音克隆，将生成结果归入项目管理，把历史持久化到 MySQL，并直接由网关提供一个同源的 React/TypeScript/Vite 工作台。它最初是为 歴史（历史纪录片）这一用例而构建，随后被通用化。

## Features

- **跨平台托盘程序** —— 同一套代码既可运行在 macOS 菜单栏，也可运行在 Windows 系统托盘。启动时选择节点角色（协调节点 / 副节点），监管后端，并在其崩溃时自动重启。
- **多机集群** —— 一个 Mac 协调节点加上零个或多个工作副节点（例如一台 Windows RTX 4060）。所有音频、历史和音色都集中存放在协调节点上；各节点租取任务并将结果回传。
- **可插拔模型** —— 每个模型作为独立的工作子进程运行在各自的 conda 环境中。通过编辑 `models.yaml` 即可添加或移除模型；网关代码无需改动。
- **零样本语音克隆** —— 从一小段参考片段克隆音色；克隆音色以文件形式存储（不在数据库中）。
- **网页工作台** —— 生成、管理克隆音色、组织项目、浏览历史、调整服务设置——全部由网关同源提供。
- **内容寻址缓存 + 历史** —— 完全相同的请求会从 LRU 磁盘缓存中即时返回；每次生成都会记录到 MySQL。
- **副本池** —— 每个模型可运行多个工作进程以实现真正的并行（单个工作进程在内部是串行的）。

## Architecture

**FastAPI 网关**（conda 环境 `vg-gateway`，Python 3.11）是唯一的外部入口。它提供 REST API、由 `web/dist` 提供的已构建网页工作台、内容寻址的音频缓存、存放于 MySQL（`voice_generation` 数据库）的生成历史与项目，以及一个以文件为后端的音色库。

为了实现依赖隔离（例如不同的 torch 版本），每个模型作为**独立的工作子进程**运行在**各自的 conda 环境**中。监管程序为每个工作进程启动 `<python> -m worker_runtime.server`，由它加载 `workers/<model>/backend.py`。工作进程暴露 `/health`、`/info` 和 `/synthesize`，并返回 16 位 PCM WAV 字节；网关负责转码（通过 ffmpeg）并存储结果。

```
client ──► gateway (FastAPI :8080) ──► supervisor ──► worker replica pool ──► backend
              │  REST API + web/dist                    (own conda env, lazy spawn,
              │  cache/ (LRU disk)                        idle-unload, ports base..base+N-1)
              │  MySQL: history + projects
              │  voices.yaml + voices/<id>/ref.wav
              └─ cluster queue (MySQL row locks)
                    ├─ embedded local agent (coordinator)
                    └─ remote agents (Windows / others, lease + post results)
```

- **副本池** —— 单个工作进程是串行的（GIL + 单一 Metal/CUDA 队列），因此真正的并行来自为同一模型运行多个进程（`replicas=N`，每个进程占用各自的端口 `base..base+N-1`）。任务通过 `acquire()/release()` 派发给空闲副本。
- **惰性 + 空闲** —— 工作进程在首次请求时启动，并在空闲超过 `worker_idle_timeout` 后被回收。健康检查使用 `httpx` 并设置 `trust_env=False`，这样 VPN/TUN 代理（Clash/V2Ray）就无法拦截 `127.0.0.1`。
- **集群** —— 一个协调节点持有任务队列；各副节点租取任务、执行推理并将结果回传。协调节点也可运行一个内嵌的本地副节点（`coordinator_runs_jobs`）。

参见 [`gateway/main.py`](gateway/main.py)（全部路由）、[`gateway/supervisor.py`](gateway/supervisor.py)、[`gateway/cluster.py`](gateway/cluster.py) 和 [`gateway/agent.py`](gateway/agent.py)。

## Quick start

### Prerequisites

- 基于 Apple Silicon（M 系列）的 macOS，并安装 [conda](https://docs.conda.io)（脚本默认 `~/miniconda3`）
- **MySQL**（Homebrew）—— 网关连接到 `mysql+pymysql://root@127.0.0.1:3306/voice_generation`
- **ffmpeg / ffprobe**（Homebrew）—— 用于音频转码与时长探测
- **Node.js + npm** —— 用于构建网页 UI

```bash
brew install mysql ffmpeg node
brew services start mysql
```

### Install

一键完成网关搭建：创建 `vg-gateway` 环境（Python 3.11），把 `models.example.yaml` 复制为 `models.yaml`，安装依赖，构建网页 UI，运行数据库迁移，并构建 macOS 托盘程序。

```bash
bash scripts/setup_gateway.sh
```

按需搭建你想要的模型工作进程（每个都有自己的 conda 环境），然后按需下载权重：

```bash
bash scripts/setup_worker.sh cosyvoice3      # creates env vg-cosyvoice, clones the official repo
bash scripts/download_weights.sh cosyvoice3  # downloads weights to models/Fun-CosyVoice3-0.5B-2512
bash scripts/setup_worker.sh f5_tts          # creates env vg-f5 (F5-TTS weights auto-download on first synth)
```

运行 `setup_worker.sh` 之后，在 `models.yaml` 中将该模型设置为 `enabled: true`，并重启网关。

### Run — recommended: the tray app

构建 macOS 的 `.app`，然后启动它并从菜单中选择节点角色：

```bash
bash scripts/build_macos_app.sh
open VoiceGeneration.app
```

托盘程序监管后端（崩溃时自动重启），显示实时状态，可切换启动/停止/重启，可设置开机自启，并可退出。

### Run — alternatives

```bash
bash scripts/start.sh            # foreground: alembic upgrade head, then uvicorn at http://127.0.0.1:8080
bash scripts/install_service.sh  # run the gateway as a background launchd service (no tray)
pkill -f "uvicorn gateway.main:app"   # stop a foreground/CLI gateway
```

在 <http://127.0.0.1:8080> 打开工作台。通过 CLI 或 API 快速合成：

```bash
bash scripts/tts.sh "你好" cosyvoice3 narrator_zh out.wav

curl -s -X POST http://127.0.0.1:8080/v1/tts \
  -H 'content-type: application/json' \
  -d '{"text":"九一八事变后，东北局势急剧变化。","model":"cosyvoice3","voice":"narrator_zh","format":"wav"}' \
  -o out.wav
```

> 任何**后端**改动之后，请重启网关。**前端**改动之后，运行 `cd web && npm run build` 并刷新页面——网关实时提供 `web/dist`，无需重启。

## Models

[`models.example.yaml`](models.example.yaml) 中预置了三个模型：

| Model | Engine | Env | Device | Cloning | Modes | Notes |
|---|---|---|---|---|---|---|
| `system` | macOS `say` | gateway env | CPU | No | clone | 无需权重；内置音色（Tingting / Kyoko / Samantha）。默认启用以便链路测试。在非 macOS 节点上设置 `enabled: false`。 |
| `cosyvoice3` | Fun-CosyVoice3-0.5B-2512 | `vg-cosyvoice` (py3.10) | auto/mps (Mac), cuda (Win) | Yes | clone, instruct, cross_lingual | 每个副本约占用 2.6 GB 内存。`options.repo_dir`、`options.model_dir`。基础端口 8110，建议 `replicas: 2`。 |
| `f5_tts` | F5TTS_v1_Base | `vg-f5` (py3.10) | auto (Mac), cuda (Win) | Yes | clone | 首次合成时从 HuggingFace 自动下载权重。基础端口 8120，`replicas: 1`。 |

**生成模式**（`mode`，默认 `clone`）：

- `clone` —— 零样本语音克隆（任意克隆模型）。
- `instruct` —— 自然语言风格控制。**仅 `cosyvoice3`**；需要非空的 `instruct_text`。
- `cross_lingual` —— 跨语种克隆。**仅 `cosyvoice3`**。

向非 `cosyvoice3` 模型传入任何非 `clone` 的模式会返回 HTTP 400；`instruct` 模式而 `instruct_text` 为空也会返回 HTTP 400。

> 对真实模型的首次（冷）调用很慢（约 25 秒，含工作进程启动 + 权重加载），且两个真实模型的运行都慢于实时（RTF 约 1.5–2.2）。对批量负载请采用预生成 + 缓存。在 macOS 上**不要**在 Docker 中运行模型（MPS 无法透传）——请跨 conda 环境使用原生进程。

## Adding a model

添加模型时从不修改网关。有两个插入点。

1. **`models.yaml` 条目** —— 在 `models:` 下添加一个块，包含 `id`、`python`（解释器路径；留空表示用网关自身的）、`backend`（`module.path:ClassName`）、`host`、一个唯一的 `port`、`languages`、`supports_cloning`、`replicas`，以及一个 `options` 字典。
2. **一个 `TTSBackend` 子类** —— 编写 `workers/<name>/backend.py`，继承 [`worker_runtime/base.py`](worker_runtime/base.py) 中的抽象基类，并实现：

   ```python
   def synthesize(self, req: SynthRequest) -> bytes:  # MUST return 16-bit PCM WAV bytes
       ...
   ```

   在首次调用时惰性加载权重。`SynthRequest` 携带 `text, voice, language, speed, mode, instruct_text, ref_audio_path, ref_text`。使用辅助函数 `pcm_to_wav_bytes(samples, sample_rate)` 将浮点 `[-1,1]` 或 int16 一维数组编码为单声道 16 位 WAV。可选地重写 `list_voices() -> list[dict]` 以暴露内置音色。

然后：创建 conda 环境 + 依赖（参见 [`scripts/setup_worker.sh`](scripts/setup_worker.sh)），添加 YAML 条目，并重启网关。移除模型 = 删除其 YAML 块。

> `replicas=N` 会占用端口 `port..port+N-1`，因此请在各模型的基础端口之间留出间隔。每个工作进程只服务其配置的模型。

## Voice cloning

克隆音色以磁盘文件形式存放在 `voices.yaml` + `voices/<id>/ref.wav` 中（不在数据库里）。每个音色具有 `id`、`name`、`language`、`ref_audio`、`ref_text`（参考音频的逐字精确转写），以及 `models`（留空 = 所有具备克隆能力的模型）。

- 克隆模型**要求**提供一个克隆音色；该音色必须被允许用于该模型，且其 `ref_audio` 文件必须存在（否则返回 HTTP 400）。
- 在 **音色库 / Voice Library** 页面管理音色：上传（WAV/MP3/MP4/WebM，最大 20 MB，3–30 秒）或直接用 Mac 麦克风录制。
- 上传内容会被规范化为 **16 kHz 单声道 WAV**；只裁剪**开头**的静音（保留结尾的静音，因为 F5-TTS 需要它）。

> 参考音频质量很重要：转写必须与音频逐字一致。建议使用干净、单一说话人、6–10 秒的片段，并在结尾留约 1 秒静音。CosyVoice 上限约 30 秒；F5-TTS 仅使用前约 12 秒。

## Projects

项目是一个存放于 MySQL `projects` 表的组织性标签。它**不影响**音频输出，并且**被排除在缓存键之外**，因此相同的文本/模型/音色会在各项目间共享。删除一个项目会将其下的生成记录设为未分配（不会删除音频）。用 `/v1/history?project=<id>` 过滤历史，或用 `project=__none__` 过滤未分配项。

## Web workbench

一个位于 [`web/`](web/) 的 React 18 + TypeScript + Vite 单页应用，构建到 `web/dist` 并由网关在同源（默认 <http://127.0.0.1:8080>）提供。所有 API 调用都使用同源的 `/v1/*` 路径，因此无需 CORS 或独立服务器。共五个页面：

- **生成工作台 / Workbench**（`/`）—— 文本 → 语音。左侧为编辑器 + 结果播放器 + 最近列表；右侧为 CONTROL 控制面板（项目、模型、音色、模式、语言、格式、语速、风格指令）。生成是异步的，并在客户端轮询；进行中的任务会持久化到 `localStorage` 并在重新加载后恢复。
- **音色库 / Voice Library**（`/voices`）—— 创建、编辑、删除并试听克隆音色；上传或录制参考音频。
- **项目 / Projects**（`/projects`）—— 按项目（名称、描述、颜色）对生成结果进行分组。
- **生成历史 / History**（`/history`）—— 分页、可过滤的历史（搜索、模型、状态、项目）；过滤条件与 URL 同步。可在工作台中复用某次生成、将其移入某项目、下载或删除。
- **服务设置 / Settings**（`/settings`）—— 健康卡片（MySQL、缓存、Apple MPS、网关）、全局设置、集群运行时 + 性能指标、副节点连接信息，以及按模型的服务控制。

## Multi-machine cluster

一个**协调节点**（默认是 Mac）在 MySQL 中持有任务队列、音频缓存、历史和音色。**工作节点**（协调节点的内嵌副节点 + 一台 Windows RTX 4060 副节点 + …）租取任务、本地执行推理并将结果回传。工作节点既不需要 MySQL 也不需要 ffmpeg——转码和存储都在协调节点上完成。

- **任务生命周期** —— 每个任务都是一行 `generation_history`，沿 `queued → leased → completed | failed`（还有 `cancelled`）流转。队列纯粹用 MySQL 行锁实现——没有额外的消息代理。
- **原子租取** —— 各节点通过 `SELECT ... FOR UPDATE SKIP LOCKED` 认领任务，因此不会有两个节点抢到同一任务。租取是**按模型**进行的（`{model_id: capacity}`），因此空闲的 `system`/F5 槽位绝不会越权认领繁忙的 CosyVoice 任务。
- **容错** —— 租约有 TTL（`lease_ttl`，默认 120 秒）。过期的租约会重新入队（直到 `max_attempts`，默认 3 次）或失败；协调节点重启时，所有遗留的已租取行都会重置为 `queued`。慢任务会发送心跳，以免被当作重复任务重新派发。
- **去重** —— 具有相同缓存键的排队行会复用已经产出的音频。
- **无节点偏好** —— 所有在线节点并行清空队列；吞吐量是各节点之和。每个模型的真正并行度 = 该模型在所有节点上的副本数之和。
- **来源标识** —— HTTP 响应携带一个 `X-Node` 头，标明产出该音频的机器（对缓存命中而言，这是协调节点的 `node_id`）；历史中显示「由 &lt;node name&gt; 生成」。
- **远程副节点控制台** 位于 `:8090` —— 副节点自己的控制面板（连接 / 断开 / 刷新、容量、运行中的任务、协调节点的节点列表）。副节点**仅**在操作者按下「连接」后才会连接（`cluster.enabled` 默认 false；该状态会持久化到 `models.yaml`）。
- **仅协调模式** —— 设置 `cluster.coordinator_runs_jobs: false`，使 Mac 仅负责协调，所有任务都派发给其他节点。

### Networking

各节点通过 **Tailscale**（稳定的 `100.x` IP）或同一 **LAN**（协调节点的 LAN IP 或 `<hostname>.local`）连接。协调节点默认绑定 `127.0.0.1`——若要让其他机器连接，请以 `--host 0.0.0.0` 启动（或设置 `settings.host: 0.0.0.0`）**并且**设置 `cluster.token`（两端必须一致）。副节点的 `httpx` 客户端使用 `trust_env=False` 以绕过系统代理/VPN；请验证 `curl http://<host>:8080/health` 返回 200。

```bash
# expose the coordinator to other machines
conda run -n vg-gateway uvicorn gateway.main:app --host 0.0.0.0 --port 8080

# start a worker node agent (console at http://127.0.0.1:8090)
bash scripts/agent.sh                                   # macOS / Linux
./scripts/agent.ps1 -CoordinatorUrl http://<host>:8080 -ClusterToken <token> -NodeId win-4060 -NodeName 'Windows 4060'   # Windows
```

参见 [`docs/CLUSTER.md`](docs/CLUSTER.md)。

## Tray app

同一套代码（[`scripts/tray.py`](scripts/tray.py)，`pystray` + Pillow）既运行在 **macOS 菜单栏**，也运行在 **Windows 系统托盘**。

- **启动时的角色** —— 读取自 `models.yaml` 的 `cluster.role`。若为空，菜单会提供 *Start as coordinator / Start as agent*，仅在选定后才持久化并启动。协调节点启动 uvicorn 网关（`:8080`）；副节点启动 `python -m gateway.agent`（`:8090` 控制台 + 租取循环）。
- **监管** —— 一个线程保持后端存活：如果它退出了而本应运行，就会以指数退避（最长 30 秒）自动重启。
- **状态** —— 每 3 秒轮询一次。协调节点显示 `running · nodes N · queue D`；副节点显示 `connected/connecting/not-connected · running K/total slots`。图标在在线（铜色）与离线（灰色）间切换。
- **菜单** —— 状态 · 打开 UI · 停止/启动 · 重启 · 切换角色 · 开机自启 · 退出。退出托盘会停止**该机器的**后端。
- **开机自启** —— macOS 使用 LaunchAgent plist；Windows 使用注册表 `Run` 键。
- **协调节点启动流程** —— 在 macOS 上，托盘会先运行 `brew services start mysql`，再运行 `alembic upgrade head`，然后才启动 uvicorn。

**Windows 打包被委派**给 Codex-on-Windows，详见 [`docs/WINDOWS_AGENT_TASK.md`](docs/WINDOWS_AGENT_TASK.md)。约定如下：**原样复用 `scripts/tray.py`**（它已处理 Windows 分支——`CREATE_NEW_PROCESS_GROUP`、`winreg` 自启、托盘图标），并且**不要**改动集群协议。Windows 交付物包括 `scripts/start_tray.bat`、`install_autostart.ps1` / `uninstall_autostart.ps1`，以及一个可选的 PyInstaller `VoiceGeneration.exe`。

> PyInstaller 注意事项：被冻结的 exe 必须以**仓库根目录作为其工作目录**运行（否则它找不到 `models.yaml` 和各模型的 conda 环境）。一个 `pythonw.exe scripts/tray.py` 快捷方式是一种已记录在案、无需冻结的后备方案。无论如何，仓库都必须保留在每个工作节点的磁盘上——工作进程是从仓库目录用其 conda 环境启动的。

## REST API

仅当设置了 `settings.api_token`（或 `VG_API_TOKEN`）时，大多数面向用户的路由才要求 `Authorization: Bearer <token>`。集群路由（`/v1/cluster/*`）使用独立的 `cluster.token`。

> **安全默认值：** 该服务绑定 `127.0.0.1` 且**无鉴权**——仅供本地使用。仅当设置了 `settings.host: 0.0.0.0` **并且**同时设置了 `api_token` 和 `cluster.token` 时，才将其暴露给其他机器。

| Method & path | Description |
|---|---|
| `GET /health` | 健康检查 `{ok, version}` |
| `POST /v1/tts` | 合成；返回音频 + `X-Generation-Id`、`X-Cache`（HIT\|MISS）、`X-Node` |
| `POST /v1/generations` | 提交异步生成（200 缓存命中，202 已入队） |
| `GET /v1/generations/{id}` | 轮询某次生成的状态 |
| `DELETE /v1/generations/{id}` | 取消一个排队中的生成（若已被租取则返回 409） |
| `GET /v1/models` | 列出已启用的模型 |
| `GET /v1/voices?model=<id>` | 列出音色（克隆 + 内置）；仅读配置，绝不唤醒工作进程 |
| `GET /v1/voice-library` | 列出所有克隆音色 |
| `POST/PUT/DELETE /v1/voices[/{id}]` | 创建 / 更新 / 删除一个克隆音色（multipart） |
| `GET /v1/voices/{id}/audio` | 下载某个克隆音色的参考 WAV |
| `GET /v1/history` | 分页/过滤的历史（`page, page_size, model, status, q, project`） |
| `PATCH /v1/history/{id}` | 重新分配某次生成的项目 |
| `GET /v1/history/{id}/audio` | 获取音频（若已被驱逐则返回 410） |
| `DELETE /v1/history/{id}` | 删除一条记录（保留磁盘上的音频） |
| `GET/POST/PUT/DELETE /v1/projects[/{id}]` | 管理项目 |
| `GET /v1/settings` · `PUT /v1/settings` | 全局设置 + 按模型的运行时状态；更新会写入 `models.yaml` 并热重载 |
| `PUT /v1/models/{id}/config` | 更新某个模型的配置（校验端口/路径/设备） |
| `POST /v1/models/{id}/start\|stop\|restart` | 预热 / 停止 / 重启某个模型的副本池 |
| `GET /v1/system` | 服务/版本/平台、MPS 标志、MySQL 状态、缓存用量、模型 |
| `POST /v1/service/shutdown` | 优雅停止服务 |
| `POST /v1/cluster/register` | 副节点注册（cluster-token 鉴权） |
| `POST /v1/cluster/lease` | 副节点按各模型容量长轮询以租取任务 |
| `GET /v1/cluster/asset/{voice_id}` | 副节点下载某个克隆参考 WAV |
| `POST /v1/cluster/jobs/{id}/result\|fail\|heartbeat` | 副节点上传结果 / 报告失败 / 续租 |
| `GET /v1/cluster/nodes` | 集群概览：自身、各节点、`queue_depth` |
| `GET /v1/cluster/connect-info` | 副节点连接信息（候选 URL，Tailscale 优先，含令牌） |
| `GET /v1/jobs/{id}` | 获取单个任务/历史行 |
| `GET /` · `GET /{path:path}` | 提供网页 SPA（兜底路由必须保持在最后） |

> 任何新增的 `GET` 路由都**必须**注册在 [`gateway/main.py`](gateway/main.py) 底部的兜底 `@app.get('/{path:path}')` SPA 回退之前，否则该回退会把它吞掉。

## Configuration

`models.yaml` 是 **git 忽略的**、每台机器各自独立的。从 [`models.example.yaml`](models.example.yaml) 开始（`cp models.example.yaml models.yaml`）。`PUT /v1/settings` 和 `PUT /v1/models/{id}/config` 会原子地重写它，并保留一份 `models.yaml.bak`。

### Key settings

| Key | Default | Description |
|---|---|---|
| `settings.host` | `127.0.0.1` | 绑定地址；设为 `0.0.0.0` 以接受其他机器 |
| `settings.port` | `8080` | 网关 HTTP 端口（同时提供网页 UI） |
| `settings.api_token` | `''` | 若非空，REST 要求 `Bearer`（环境变量 `VG_API_TOKEN`） |
| `settings.cache_dir` / `cache_max_gb` | `cache` / `3.0` | 磁盘缓存目录 + 容量上限（GB）；按 LRU 驱逐。配置默认值为 `3.0`；`models.example.yaml` 使用 `30.0`。 |
| `settings.worker_idle_timeout` / `worker_start_timeout` | `300` · `180` | 空闲回收 · 启动等待（秒）。配置默认空闲为 `300`；`models.example.yaml` 使用 `3600`。 |
| `settings.default_model` / `default_format` | `cosyvoice3` / `wav` | 请求未指定时的默认值 |
| `settings.voices_file` | `voices.yaml` | 克隆音色清单路径 |

### Cluster keys

| Key | Default | Description |
|---|---|---|
| `cluster.role` | `''` | `''`（在托盘启动时选择）\| `coordinator` \| `agent` |
| `cluster.node_id` / `node_name` | `local` | 唯一节点 id / 显示名称 |
| `cluster.coordinator_url` | `''` | 副节点：协调节点的 URL（在协调节点上留空） |
| `cluster.token` | `''` | 共享的集群密钥；暴露协调节点时必填 |
| `cluster.coordinator_runs_jobs` | `true` | 协调节点也通过其内嵌副节点执行推理 |
| `cluster.max_concurrency` | `1` | 节点并发提示（本节点自身的推理并行度） |
| `cluster.poll_interval` | `1.0` | 副节点长轮询 / 空闲休眠间隔（秒） |
| `cluster.lease_ttl` / `node_timeout` / `max_attempts` | `120` / `60` / `3` | 租约 TTL · 离线超时 · 最大尝试次数 |
| `cluster.agent_host` / `agent_port` | `127.0.0.1` / `8090` | 副节点网页控制台绑定 / 端口 |
| `cluster.enabled` | `false` | 副节点是否主动连接（由 `:8090` 控制台切换） |

### Per-model keys

`id`、`enabled`、`description`、`python`（解释器；留空 = 网关自身的）、`backend`（`module:Class`）、`host`、`port`（副本基础端口）、`languages`、`supports_cloning`、`replicas`（并行进程数，1–8）、`options`（例如 `device: auto|mps|cpu|cuda`、`repo_dir`、`model_dir`、`model`），以及 `placement.allow`（被允许运行该模型的 node_ids 列表；留空 = 全部）。

### Environment overrides

`VG_API_TOKEN`、`VG_CLUSTER_ROLE`、`VG_NODE_ID`、`VG_NODE_NAME`、`VG_COORDINATOR_URL`、`VG_CLUSTER_TOKEN`、`VG_DATABASE_URL`、`VG_FFMPEG`、`VG_FFPROBE`。

> 在 Windows 工作节点上：将每个模型的 `python` 指向该环境的 `python.exe`，设置 `options.device: cuda`（验证 `torch.cuda.is_available()`），将 `model_dir`/`repo_dir` 指向本地路径，并将 `system` 模型设为 `enabled: false`。

## Data & cache

- **MySQL**（`voice_generation`；可用 `VG_DATABASE_URL` 覆盖，默认 `mysql+pymysql://root@127.0.0.1:3306/voice_generation`）。三张表：`generation_history`、`projects`、`cluster_nodes`。时间戳以朴素 UTC 存储，并以 `Z` 后缀序列化。
- **Alembic** —— `alembic upgrade head` 会先自动创建数据库，再应用迁移（一条由 4 个修订组成的线性链）。`init_database()` 也会运行 `create_all`，因此表可能从 ORM 端出现；在生产中应让 Alembic 保持为唯一可信来源。
- **内容寻址的磁盘缓存**（[`gateway/cache.py`](gateway/cache.py)）—— 音频存放于 `cache/<key[:2]>/<key>.<ext>`，以决定输出的参数（model、voice、language、text、speed、format、mode、instruct_text、options、ref、ref_text）的 SHA-256 作为键。它**排除** `project_id` 和 `assigned_node`，因此相同内容会被共享和去重。`cache_max_gb` 上的 LRU 驱逐绝不触碰 `_logs`。
- **转码**（[`gateway/audio.py`](gateway/audio.py)）—— 工作进程返回 WAV；网关按需通过系统 ffmpeg 转码为 `wav | mp3 | opus`。`ffmpeg`/`ffprobe` 依次通过 `VG_FFMPEG`/`VG_FFPROBE`、`PATH`，再到常见的 Homebrew 目录来发现。
- **文件后端的音色**（[`gateway/voice_store.py`](gateway/voice_store.py)）—— `voices.yaml`（原子写入 + `.bak`）和 `voices/<id>/ref.wav`。音色 ID 匹配 `^[A-Za-z0-9_-]{2,64}$`。

## Testing

后端测试需要一个运行中的 MySQL，位于 `root@127.0.0.1:3306`（空密码）；`tests/conftest.py` 强制使用 `voice_generation_test` 数据库，因此测试运行绝不会触碰你真实的历史。

```bash
conda run -n vg-gateway python -m pytest -q   # backend
cd web && npm test                            # frontend (vitest)
```

> 测试使用 `create_all`（不会向已存在的表添加新列）。在更改 ORM 列之后，请删除受影响的 `voice_generation_test` 表以便它们被重建。

## Repository layout

```
gateway/            FastAPI gateway: main.py (all routes), supervisor.py, cluster.py,
                    agent.py, config.py, database.py, cache.py, audio.py, voice_store.py
worker_runtime/     worker server + TTSBackend ABC (base.py)
workers/<model>/    per-model backends (system, cosyvoice3, f5_tts)
web/                React + TS + Vite workbench (built to web/dist)
scripts/            setup/run/build scripts + tray.py + make_icons.py + agent.sh/ps1
packaging/          Info.plist, icons, .app launcher
                    (packaging/windows/ — PyInstaller spec — is a delegated Windows
                     deliverable, not built on the Mac side; see WINDOWS_AGENT_TASK.md)
alembic/            DB migrations
examples/           history_audio_router.py, batch_pregenerate.py (歴史 use case)
tests/              backend test suite
models.example.yaml model registry template (copy to models.yaml)
```

## Documentation

- [`docs/CLUSTER.md`](docs/CLUSTER.md) —— 多机集群的搭建与运维
- [`docs/DESIGN.md`](docs/DESIGN.md) —— 架构与设计说明
- [`docs/WINDOWS_AGENT_TASK.md`](docs/WINDOWS_AGENT_TASK.md) —— Windows 副节点/托盘打包约定
