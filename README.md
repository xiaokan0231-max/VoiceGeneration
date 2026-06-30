# VoiceGeneration — 本机文字转语音服务 + Web 工作台

一个跑在本机（macOS / Apple Silicon）的**文字转语音（TTS）服务**：统一一套 REST API，
背后挂多个**可插拔的模型**（CosyVoice3、F5-TTS、macOS 系统引擎），支持**零样本声音克隆**、
按**项目**归类生成、生成历史（MySQL）、以及一个同源托管的 **Web 工作台**。

> 给 AI 阅读者：这份 README 力求自包含。读完你应能：启动服务、调用 API 合成语音、
> 用 Web 工作台、新增/删除模型、管理音色与项目。所有路径相对仓库根
> `/Users/kanxiao/IdeaProjects/VoiceGeneration`。

---

## 1. 架构一览

```
浏览器 / API 客户端
        │  HTTP (默认 127.0.0.1:8080)
        ▼
┌─────────────────────────────────────────────┐
│ gateway  (FastAPI, conda 环境 vg-gateway)     │
│  · 统一 REST API + 同源托管 Web 工作台(web/dist)│
│  · 内容寻址磁盘缓存 (cache/)                    │
│  · 生成历史 → 本机 MySQL (voice_generation)    │
│  · 音色库 → 文件 (voices.yaml + voices/<id>/)  │
│  · 项目 → MySQL (projects 表)                  │
│  · supervisor: 按需拉起/空闲回收 worker 子进程  │
└───────────────┬───────────────┬───────────────┘
                │ 本机 HTTP      │
       ┌────────▼──────┐ ┌──────▼─────────┐  每个模型一个独立 conda 环境+子进程，
       │ worker:system │ │ worker:cosyvoice│  依赖(torch 版本等)互相隔离；
       │ (macOS say)   │ │ worker:f5_tts   │  首次调用惰性加载、空闲超时自动卸载。
       └───────────────┘ └────────────────┘
```

- **gateway** 是唯一对外入口；模型在**各自的 conda 环境**里以子进程运行
  （`worker_runtime/server.py` 加载 `workers/<model>/backend.py`）。
- **可插拔**：增删模型 = 改 `models.yaml` + 写一个 `backend.py`，gateway 不动。
- **缓存**：相同(文本/模型/音色/模式/语速/格式/参考音频)命中缓存直接返回；
  响应头 `X-Cache: HIT|MISS`。项目（project_id）**不参与**缓存键，跨项目共享音频。

---

## 2. 目录结构

```
gateway/            网关：main.py(全部路由) config.py cache.py supervisor.py
                    database.py(MySQL: 历史+项目) voice_store.py(文件音色) audio.py schemas.py
worker_runtime/     所有 worker 共用：server.py(通用服务) base.py(TTSBackend 接口)
workers/            模型后端：system/(macOS say) cosyvoice3/ f5_tts/  各含 backend.py
web/                React+TS+Vite 工作台；构建产物 web/dist 由 gateway 托管
alembic/            MySQL 迁移（versions/ 下按 YYYYMMDD_NN 命名）
scripts/            setup_gateway.sh setup_worker.sh download_weights.sh start.sh tts.sh
                    launcher.py build_macos_app.sh
voices.yaml         克隆音色清单（参考音频 + 逐字稿）
voices/<id>/ref.wav 参考音频（16k 单声道）
models.yaml         模型注册表（“插口”）+ 全局 settings
cache/              生成音频缓存(内容寻址) + cache/_logs/(网关与 worker 日志)
models/  third_party/  下载的权重 / 克隆的官方仓库（不入 git）
examples/           接入「歴史」项目示例：history_audio_router.py / batch_pregenerate.py
tests/              pytest（test_core.py 需本机 MySQL；test_cosyvoice_modes.py mock）
```

---

## 3. 环境前提

- macOS（Apple Silicon），已装 **miniconda**、**Homebrew**、**ffmpeg**(`brew install ffmpeg`)、
  **MySQL**(`brew install mysql && brew services start mysql`，默认 `root` 无密码)、**Node.js**。
- conda 环境：`vg-gateway`(py3.11，网关) / `vg-cosyvoice`(py3.10) / `vg-f5`(py3.10)。

---

## 4. 安装与启动

```bash
# 一次性安装：建 vg-gateway 环境、装依赖、构建 Web、跑 DB 迁移、生成 macOS .app
bash scripts/setup_gateway.sh
```

启动方式：

```bash
# A) 命令行启动网关（前台）：会先 alembic upgrade head，再起 uvicorn
bash scripts/start.sh                       # → http://127.0.0.1:8080

# B) 托盘程序（推荐）：构建并双击 VoiceGeneration.app → 菜单栏出现图标
bash scripts/build_macos_app.sh             # 解析 vg-gateway python + 生成图标 + 打包
open VoiceGeneration.app
#    启动时按 models.yaml 的 cluster.role 运行；role 为空则在菜单里选「主服务器/副节点」。
#    选「主服务器」→ 看护 uvicorn(:8080 工作台)；选「副节点」→ 看护 gateway.agent(:8090 控制台)。
#    崩溃自动重启；悬停看状态；菜单可重启/启停/切角色/开机自启/退出。日志在 logs/<role>.log。
#    Windows 用同一份 scripts/tray.py：见 docs/WINDOWS_AGENT_TASK.md（交给 Windows 上的 Codex 打包发布）。

# C) 常驻后台（无托盘，launchd）：bash scripts/install_service.sh
```

打开 **http://127.0.0.1:8080/** 即是 Web 工作台；API 文档在 **/docs**。

`system` 模型（macOS `say`）开箱即用、无需权重，适合先验证链路。

---

## 5. 模型

| id | 引擎 | 设备 | 克隆 | 模式 | 启用前提 |
|----|------|------|------|------|----------|
| `system` | macOS `say` | CPU | ✗ | 仅 clone | 无（默认可用，仅联调） |
| `cosyvoice3` | Fun-CosyVoice3-0.5B-2512 | MPS | ✓ | clone/instruct/cross_lingual | 见下 |
| `f5_tts` | F5TTS_v1_Base | MPS | ✓ | 仅 clone | 见下 |

启用真实模型（已在 `setup_worker.sh` 内置 macOS 适配，可复现）：

```bash
# CosyVoice3
bash scripts/setup_worker.sh cosyvoice3        # 建 vg-cosyvoice + 克隆官方仓库 + 装依赖
bash scripts/download_weights.sh cosyvoice3    # 下载权重到 models/Fun-CosyVoice3-0.5B-2512

# F5-TTS（权重首次合成时自动从 HuggingFace 下载）
bash scripts/setup_worker.sh f5_tts            # 建 vg-f5 + pip install f5-tts
```

模型在 `models.yaml` 里 `enabled: true` 即生效（改完重启网关，或用
`/v1/settings`、`/v1/models/{id}/config` 热更新）。

> 性能：本机上两个真实模型都**慢于实时**（RTF≈1.5–2.2），首次冷调用约 25s（含 worker
> 拉起+加载权重），之后命中缓存即时返回。批量场景建议预生成 + 缓存。

---

## 6. 生成模式（mode）

- `clone`（默认）：零样本声音克隆，所有支持克隆的模型可用。
- `instruct`：自然语言指令控制语气，**仅 `cosyvoice3`**，必须提供 `instruct_text`
  （如“沉稳克制、纪录片旁白”）。
- `cross_lingual`：跨语言克隆，**仅 `cosyvoice3`**。

`system` 与 `f5_tts` 只支持 `clone`；对它们传别的 mode 会报 400。

---

## 7. 声音克隆与参考音频

克隆音色登记在 `voices.yaml`，每个音色 = 一段参考音频 + **逐字稿**（音频里实际说的话，
**必须一字不差**）：

```yaml
voices:
  - id: narrator_zh
    name: 中文旁白
    language: zh
    ref_audio: voices/narrator_zh/ref.wav
    ref_text: "这是一段用于声音克隆测试的参考音频，语气清晰自然。"
    models: [cosyvoice3, f5_tts]    # 留空=所有支持克隆的模型
```

参考音频建议：单人、干净无噪、**6–10 秒**、结尾留约 1s 静音。约束：CosyVoice 上限 30s；
F5-TTS 只取前 ~12s。上传/录制时 `voice_store.py` 会统一转成 16k 单声道 WAV 并**自动修剪开头静音**
（不剪结尾，F5 需要）。内容可任意，但逐字稿必须对应。

支持克隆的模型在 `/v1/tts` 必须指定一个克隆音色；`system` 用内置音色（`Tingting`/`Kyoko`/`Samantha`）。

---

## 8. 项目（按项目归类生成）

- 生成时传 `project_id` 把这次生成归到某项目；留空=「未归类」。项目存于 MySQL `projects` 表。
- 用 `/v1/projects` 增删改查；`/v1/history?project=<id>`（或 `__none__` 查未归类）筛选；
  `PATCH /v1/history/{id}` 事后改归属。删项目会把其下生成置为未归类（不删音频）。
- `project_id` 是组织标签，**不影响音频、不进缓存键**。

---

## 9. Web 工作台（五个页面）

- **生成工作台**：输文本 → 选项目/模型/音色/模式/语言/语速/格式 → 生成；真实波形播放、下载、最近生成（可一键复制文本）。
- **音色库**：上传 WAV/MP3/WebM 或浏览器录音（先预热再录，避免吞首字），管理克隆音色。
- **项目**：新建/改名/删除项目、看每个项目的生成数、跳转到该项目的历史。
- **生成历史**：按文本/模型/状态/**项目**筛选、分页、播放、下载、复用、删除、行内「移到项目」。
- **服务设置**：MySQL/缓存/MPS/各模型状态；改全局设置、逐模型启停/改配置。

---

## 10. REST API 参考

基址 `http://127.0.0.1:8080`。若 `settings.api_token`（或环境变量 `VG_API_TOKEN`）非空，
需带 `Authorization: Bearer <token>`。

**合成**
```
POST /v1/tts
  body(JSON): {
    "text": "要合成的文字",          // 必填
    "model": "cosyvoice3",          // 必填，见 /v1/models
    "voice": "narrator_zh",         // 必填，克隆音色或内置音色 id
    "mode": "clone",                // clone|instruct|cross_lingual，默认 clone
    "instruct_text": "...",         // mode=instruct 必填
    "language": "zh",               // 可选 zh/ja/en/...，留空模型自判
    "speed": 1.0,                   // 0.1–3.0
    "format": "wav",                // wav|mp3|opus，留空用默认
    "project_id": "<uuid|null>"     // 可选，归属项目
  }
  → 200: 音频字节(audio/wav|mpeg|ogg)；响应头 X-Generation-Id、X-Cache:HIT|MISS
```

**模型 / 音色**
```
GET    /v1/models                         已启用模型及能力
GET    /v1/voices?model=<id>              某模型可用音色(克隆+内置)
GET    /v1/voice-library                  全部克隆音色(含 ref_text、audio_url)
POST   /v1/voices                         新建克隆音色(multipart: name,language,ref_text,models,audio[,voice_id])
PUT    /v1/voices/{id}                    更新(multipart，audio 可省=保留)
DELETE /v1/voices/{id}                    删除
GET    /v1/voices/{id}/audio              下载该音色参考音频
```

**项目**
```
GET    /v1/projects                       项目列表(含 generation_count)
POST   /v1/projects                       {name, description?, color?}
PUT    /v1/projects/{id}                  {name?, description?, color?}
DELETE /v1/projects/{id}                  删除(其下生成置为未归类)
```

**历史**
```
GET    /v1/history?page&page_size&model&status&q&project   分页/筛选(project=__none__ 查未归类)
GET    /v1/history/{id}/audio             取该次生成音频(已清理则 410)
PATCH  /v1/history/{id}                   {project_id: <id|null>}  改归属
DELETE /v1/history/{id}                   删记录(不删磁盘音频)
```

**设置 / 模型进程 / 系统**
```
GET  /v1/settings                         全局设置 + 模型运行态
PUT  /v1/settings                         {default_model?,default_format?,worker_idle_timeout?,worker_start_timeout?,cache_max_gb?}
PUT  /v1/models/{id}/config               {enabled?,description?,python?,port?,languages?,options?}
POST /v1/models/{id}/start|stop|restart   管理 worker 进程
GET  /v1/system                           MySQL/缓存/MPS/模型状态
GET  /health                              健康检查
POST /v1/service/shutdown                 优雅停止服务
```

命令行快捷合成：`bash scripts/tts.sh "你好" cosyvoice3 narrator_zh out.wav`

curl 示例：
```bash
curl -s -X POST http://127.0.0.1:8080/v1/tts -H 'content-type: application/json' \
  -d '{"text":"九一八事变后，东北局势急剧变化。","model":"cosyvoice3","voice":"narrator_zh","format":"wav"}' \
  -o out.wav
```

---

## 11. 配置文件

`models.yaml`（节选）：
```yaml
settings:
  host: 127.0.0.1
  port: 8080
  api_token: ""             # 非空则需 Bearer 鉴权
  cache_max_gb: 30          # 缓存上限，超出按 LRU 淘汰
  worker_idle_timeout: 300  # 秒；worker 空闲超时自动卸载
  default_model: cosyvoice3
  default_format: wav
models:
  - id: cosyvoice3
    enabled: true
    python: /Users/kanxiao/miniconda3/envs/vg-cosyvoice/bin/python  # 该模型的解释器
    backend: workers.cosyvoice3.backend:CosyVoice3Backend           # 模块:类
    host: 127.0.0.1
    port: 8102
    languages: [zh, en, ja, ko, ...]
    supports_cloning: true
    options: { repo_dir: third_party/CosyVoice, model_dir: models/Fun-CosyVoice3-0.5B-2512, device: auto }
```

---

## 12. 如何新增一个模型（可插拔）

1. `workers/<name>/backend.py` 写一个类，继承 `worker_runtime/base.py:TTSBackend`，实现
   `synthesize(self, req: SynthRequest) -> bytes`（返回 16-bit PCM 的 WAV 字节；权重首次调用惰性加载）。
   可选实现 `list_voices()` 暴露内置音色。
2. 为它建一个 conda 环境并装依赖（可参考 `scripts/setup_worker.sh`）。
3. 在 `models.yaml` 的 `models:` 加一段（`id` / `python`(该环境解释器) / `backend`(模块:类) /
   `host` / 唯一 `port` / `languages` / `supports_cloning` / `options`）。
4. 重启网关。删除模型=删掉这段配置（可选删环境/目录）。

gateway 通过 supervisor 用该 `python -m worker_runtime.server` 拉起子进程，环境变量
`VG_BACKEND/VG_MODEL_ID/VG_HOST/VG_PORT/VG_OPTIONS` 注入；worker 暴露 `/health` `/info` `/synthesize`。

---

## 13. 数据与缓存

- **生成音频**：内容寻址存 `cache/<key前2位>/<sha256>.<ext>`，超 `cache_max_gb` 按访问时间 LRU 淘汰。
- **生成历史**：MySQL `voice_generation.generation_history`（元数据 + 相对音频路径）。
- **项目**：MySQL `voice_generation.projects`。
- **克隆音色**：文件 `voices.yaml` + `voices/<id>/ref.wav`（非 DB）。
- DB 连接：`VG_DATABASE_URL`，默认 `mysql+pymysql://root@127.0.0.1:3306/voice_generation`。

---

## 14. 测试

```bash
# 后端(需本机 MySQL；用独立库 voice_generation_test)
conda run -n vg-gateway python -m pytest -q
# 前端
cd web && npm test          # vitest
cd web && npm run build     # tsc 类型检查 + 构建
```

> 注：测试库用 `create_all` 建表，不会给旧表加新列。**改了 ORM 列后**需先 DROP 测试库里对应旧表，
> 让其按新结构重建；生产库则靠 `alembic upgrade head`。

---

## 15. 给 AI 协作者的关键约束

- **改后端需重启网关**（`pkill -f "uvicorn gateway.main:app"` 后 `bash scripts/start.sh`）；
  **改前端需 `npm run build`**（gateway 从 `web/dist` 实时托管，刷新即可，无需重启网关）。
- 新增 GET 路由必须放在 `gateway/main.py` 末尾的 SPA 兜底 `@app.get("/{path:path}")` **之前**，否则被它吞掉。
- `conda run` 会吞掉 heredoc 的 stdout；调试时把脚本写文件、输出重定向到文件再读。
- macOS 上不要用 Docker 跑模型（无法透传 MPS）；用原生进程 + 多 conda 环境。
- 默认只监听 `127.0.0.1`、无鉴权，仅供本机使用。

详细设计见 [docs/DESIGN.md](docs/DESIGN.md)。接入「歴史」项目见 [examples/](examples/)。
