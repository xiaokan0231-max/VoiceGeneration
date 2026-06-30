# VoiceGeneration

A local, pluggable text-to-speech service — one unified REST API in front of multiple TTS models, with voice cloning, projects, generation history, a React web workbench, a multi-machine cluster, and a cross-platform tray app.

**English** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

VoiceGeneration runs on your own machine (Apple Silicon primary, optional CUDA worker nodes). It exposes a single REST API that fronts several pluggable TTS engines (CosyVoice3, F5-TTS, macOS `say`), supports zero-shot voice cloning, organizes generations into projects, persists history in MySQL, and ships a same-origin React/TypeScript/Vite workbench served straight from the gateway. It was built first for the 歴史 (history-documentary) use case, then generalized.

## Features

- **Cross-platform tray app** — one codebase runs in the macOS menu bar and the Windows system tray. Pick the node role (coordinator / agent) at launch, supervise the backend, and have it auto-restart if it dies.
- **Multi-machine cluster** — one Mac coordinator plus zero or more worker agents (e.g. a Windows RTX 4060). All audio, history, and voices live centrally on the coordinator; nodes lease jobs and post results back.
- **Pluggable models** — each model runs as an isolated worker subprocess in its own conda env. Add or remove a model by editing `models.yaml`; the gateway code is never touched.
- **Zero-shot voice cloning** — clone a voice from a short reference clip; clone voices are file-backed (not in the DB).
- **Web workbench** — generate, manage clone voices, organize projects, browse history, and tune service settings — all served same-origin by the gateway.
- **Content-addressed cache + history** — identical requests return instantly from an LRU disk cache; every generation is recorded in MySQL.
- **Replica pools** — run multiple worker processes per model for real parallelism (a single worker is internally serial).

## Architecture

The **FastAPI gateway** (conda env `vg-gateway`, Python 3.11) is the only external entry point. It serves the REST API, the built web workbench from `web/dist`, the content-addressed audio cache, generation history + projects in MySQL (`voice_generation` DB), and a file-backed voice library.

Each model runs as a **separate worker subprocess** in its **own conda env** for dependency isolation (e.g. different torch versions). The supervisor spawns `<python> -m worker_runtime.server` per worker, which loads `workers/<model>/backend.py`. Workers expose `/health`, `/info`, and `/synthesize`, and return 16-bit PCM WAV bytes; the gateway transcodes (via ffmpeg) and stores the result.

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

- **Replica pools** — a single worker process is serial (GIL + single Metal/CUDA queue), so real parallelism comes from running multiple processes for the same model (`replicas=N`, each on its own port `base..base+N-1`). Jobs are dispatched to free replicas via `acquire()/release()`.
- **Lazy + idle** — workers spawn on first request and are reaped when idle longer than `worker_idle_timeout`. Health checks use `httpx` with `trust_env=False` so VPN/TUN proxies (Clash/V2Ray) cannot intercept `127.0.0.1`.
- **Cluster** — one coordinator holds the queue; agents lease jobs, run inference, and post results back. The coordinator can also run an embedded local agent (`coordinator_runs_jobs`).

See [`gateway/main.py`](gateway/main.py) (all routes), [`gateway/supervisor.py`](gateway/supervisor.py), [`gateway/cluster.py`](gateway/cluster.py), and [`gateway/agent.py`](gateway/agent.py).

## Quick start

### Prerequisites

- macOS on Apple Silicon (M-series), with [conda](https://docs.conda.io) (scripts assume `~/miniconda3`)
- **MySQL** (Homebrew) — the gateway connects to `mysql+pymysql://root@127.0.0.1:3306/voice_generation`
- **ffmpeg / ffprobe** (Homebrew) — for audio transcoding and duration probing
- **Node.js + npm** — to build the web UI

```bash
brew install mysql ffmpeg node
brew services start mysql
```

### Install

One-shot gateway setup: creates the `vg-gateway` env (Python 3.11), copies `models.example.yaml` → `models.yaml`, installs deps, builds the web UI, runs DB migrations, and builds the macOS tray app.

```bash
bash scripts/setup_gateway.sh
```

Set up the model workers you want (each gets its own conda env), then download weights as needed:

```bash
bash scripts/setup_worker.sh cosyvoice3      # creates env vg-cosyvoice, clones the official repo
bash scripts/download_weights.sh cosyvoice3  # downloads weights to models/Fun-CosyVoice3-0.5B-2512
bash scripts/setup_worker.sh f5_tts          # creates env vg-f5 (F5-TTS weights auto-download on first synth)
```

After running `setup_worker.sh`, set that model's `enabled: true` in `models.yaml` and restart the gateway.

### Run — recommended: the tray app

Build the macOS `.app`, then launch it and choose the node role from the menu:

```bash
bash scripts/build_macos_app.sh
open VoiceGeneration.app
```

The tray app supervises the backend (auto-restart on crash), shows live status, toggles start/stop/restart, sets autostart, and quits.

### Run — alternatives

```bash
bash scripts/start.sh            # foreground: alembic upgrade head, then uvicorn at http://127.0.0.1:8080
bash scripts/install_service.sh  # run the gateway as a background launchd service (no tray)
pkill -f "uvicorn gateway.main:app"   # stop a foreground/CLI gateway
```

Open the workbench at <http://127.0.0.1:8080>. Quick synth from the CLI or API:

```bash
bash scripts/tts.sh "你好" cosyvoice3 narrator_zh out.wav

curl -s -X POST http://127.0.0.1:8080/v1/tts \
  -H 'content-type: application/json' \
  -d '{"text":"九一八事变后，东北局势急剧变化。","model":"cosyvoice3","voice":"narrator_zh","format":"wav"}' \
  -o out.wav
```

> After any **backend** change, restart the gateway. After **frontend** changes, run `cd web && npm run build` and refresh — the gateway serves `web/dist` live, no restart needed.

## Models

Three models ship in [`models.example.yaml`](models.example.yaml):

| Model | Engine | Env | Device | Cloning | Modes | Notes |
|---|---|---|---|---|---|---|
| `system` | macOS `say` | gateway env | CPU | No | clone | No weights; built-in voices (Tingting / Kyoko / Samantha). Enabled by default for link testing. Set `enabled: false` on non-macOS nodes. |
| `cosyvoice3` | Fun-CosyVoice3-0.5B-2512 | `vg-cosyvoice` (py3.10) | auto/mps (Mac), cuda (Win) | Yes | clone, instruct, cross_lingual | ~2.6 GB memory per replica. `options.repo_dir`, `options.model_dir`. Base port 8110, suggested `replicas: 2`. |
| `f5_tts` | F5TTS_v1_Base | `vg-f5` (py3.10) | auto (Mac), cuda (Win) | Yes | clone | Weights auto-download from HuggingFace on first synth. Base port 8120, `replicas: 1`. |

**Generation modes** (`mode`, default `clone`):

- `clone` — zero-shot voice cloning (any cloning model).
- `instruct` — natural-language style control. **Only `cosyvoice3`**; requires non-empty `instruct_text`.
- `cross_lingual` — cross-lingual cloning. **Only `cosyvoice3`**.

Passing any mode other than `clone` to a non-`cosyvoice3` model returns HTTP 400; `instruct` with empty `instruct_text` returns HTTP 400.

> The first (cold) call to a real model is slow (~25 s incl. worker spawn + weight load) and both real models run slower than realtime (RTF ~1.5–2.2). Use pre-generation + caching for batch workloads. On macOS do **not** run models in Docker (MPS can't be passed through) — use native processes across conda envs.

## Adding a model

The gateway is never modified to add a model. There are two plug points.

1. **`models.yaml` entry** — add a block under `models:` with `id`, `python` (interpreter path; empty = gateway's own), `backend` (`module.path:ClassName`), `host`, a unique `port`, `languages`, `supports_cloning`, `replicas`, and an `options` dict.
2. **A `TTSBackend` subclass** — write `workers/<name>/backend.py` subclassing the ABC in [`worker_runtime/base.py`](worker_runtime/base.py) and implement:

   ```python
   def synthesize(self, req: SynthRequest) -> bytes:  # MUST return 16-bit PCM WAV bytes
       ...
   ```

   Load weights lazily on first call. `SynthRequest` carries `text, voice, language, speed, mode, instruct_text, ref_audio_path, ref_text`. Use the helper `pcm_to_wav_bytes(samples, sample_rate)` to encode a float `[-1,1]` or int16 1-D array into mono 16-bit WAV. Optionally override `list_voices() -> list[dict]` to expose built-in voices.

Then: create the conda env + deps (see [`scripts/setup_worker.sh`](scripts/setup_worker.sh)), add the YAML entry, and restart the gateway. Removing a model = delete its YAML block.

> `replicas=N` occupies ports `port..port+N-1`, so leave gaps between models' base ports. Each worker serves only its configured model.

## Voice cloning

Clone voices live on disk in `voices.yaml` + `voices/<id>/ref.wav` (not in the DB). Each voice has `id`, `name`, `language`, `ref_audio`, `ref_text` (the exact transcript of the reference, word-for-word), and `models` (empty = all cloning-capable models).

- A cloning model **requires** a clone voice; the voice must be permitted for that model and its `ref_audio` file must exist (else HTTP 400).
- Manage voices from the **音色库 / Voice Library** page: upload (WAV/MP3/MP4/WebM, max 20 MB, 3–30 s) or record directly with the Mac mic.
- Uploads are normalized to **16 kHz mono WAV**; only **leading** silence is trimmed (trailing silence is preserved because F5-TTS needs it).

> Reference-audio quality matters: the transcript must match the audio word-for-word. Recommended a clean, single-speaker 6–10 s clip with ~1 s trailing silence. CosyVoice caps ~30 s; F5-TTS uses only the first ~12 s.

## Projects

A project is an organizational tag stored in the MySQL `projects` table. It does **not** affect audio output and is **excluded from the cache key**, so identical text/model/voice is shared across projects. Deleting a project sets its generations to unassigned (it does not delete audio). Filter history with `/v1/history?project=<id>`, or `project=__none__` for unassigned.

## Web workbench

A React 18 + TypeScript + Vite SPA in [`web/`](web/), built to `web/dist` and served by the gateway on the same origin (default <http://127.0.0.1:8080>). All API calls use same-origin `/v1/*` paths, so no CORS or separate server is needed. Five pages:

- **生成工作台 / Workbench** (`/`) — text → speech. Editor + result player + recent list on the left; a CONTROL panel (project, model, voice, mode, language, format, speed, style instruction) on the right. Generation is asynchronous and polled client-side; in-flight jobs persist in `localStorage` and resume after reload.
- **音色库 / Voice Library** (`/voices`) — create, edit, delete, and preview clone voices; upload or record reference audio.
- **项目 / Projects** (`/projects`) — group generations by project (name, description, color).
- **生成历史 / History** (`/history`) — paginated, filterable history (search, model, status, project); filters sync to the URL. Reuse a generation in the workbench, move it to a project, download, or delete.
- **服务设置 / Settings** (`/settings`) — health cards (MySQL, cache, Apple MPS, gateway), global settings, cluster runtime + performance metrics, sub-node connect info, and per-model service controls.

## Multi-machine cluster

One **coordinator** (default the Mac) holds the job queue, audio cache, history, and voices in MySQL. **Worker nodes** (the coordinator's embedded agent + a Windows RTX 4060 agent + …) lease jobs, run local inference, and post results back. Worker nodes need neither MySQL nor ffmpeg — transcode and storage happen on the coordinator.

- **Job lifecycle** — each job is a `generation_history` row moving `queued → leased → completed | failed` (also `cancelled`). The queue is implemented purely with MySQL row locks — no extra broker.
- **Atomic leasing** — nodes claim work via `SELECT ... FOR UPDATE SKIP LOCKED`, so no two nodes grab the same job. Leasing is **per-model** (`{model_id: capacity}`), so an idle `system`/F5 slot never over-claims a busy CosyVoice job.
- **Fault tolerance** — leases have a TTL (`lease_ttl`, default 120 s). Expired leases requeue (until `max_attempts`, default 3) or fail; on coordinator restart all leftover leased rows reset to `queued`. Slow jobs send heartbeats so they aren't re-dispatched as duplicates.
- **Dedup** — queued rows with the same cache key reuse already-produced audio.
- **No node preference** — all online nodes drain the queue in parallel; throughput is the sum of nodes. Real parallelism per model = the sum of that model's replicas across all nodes.
- **Provenance** — the HTTP response carries an `X-Node` header naming the machine that produced the audio (for cache hits this is the coordinator's `node_id`); history shows "generated by &lt;node name&gt;".
- **Remote-agent console** at `:8090` — the sub-node's own control panel (connect / disconnect / refresh, capacity, running jobs, the coordinator's node list). The agent connects **only** after the operator presses Connect (`cluster.enabled` defaults to false; the state is persisted to `models.yaml`).
- **Coordinator-only mode** — set `cluster.coordinator_runs_jobs: false` so the Mac coordinates only and all jobs go to other nodes.

### Networking

Nodes connect over **Tailscale** (stable `100.x` IPs) or the same **LAN** (coordinator's LAN IP or `<hostname>.local`). The coordinator binds `127.0.0.1` by default — to let other machines connect, start with `--host 0.0.0.0` (or set `settings.host: 0.0.0.0`) **and** set `cluster.token` (identical on both ends). The agent's `httpx` client uses `trust_env=False` to bypass a system proxy/VPN; verify `curl http://<host>:8080/health` returns 200.

```bash
# expose the coordinator to other machines
conda run -n vg-gateway uvicorn gateway.main:app --host 0.0.0.0 --port 8080

# start a worker node agent (console at http://127.0.0.1:8090)
bash scripts/agent.sh                                   # macOS / Linux
./scripts/agent.ps1 -CoordinatorUrl http://<host>:8080 -ClusterToken <token> -NodeId win-4060 -NodeName 'Windows 4060'   # Windows
```

See [`docs/CLUSTER.md`](docs/CLUSTER.md).

## Tray app

One codebase ([`scripts/tray.py`](scripts/tray.py), `pystray` + Pillow) runs in the **macOS menu bar** and the **Windows system tray**.

- **Role at launch** — read from `models.yaml` `cluster.role`. If empty, the menu offers *Start as coordinator / Start as agent* and only persists + starts once chosen. Coordinator spawns the uvicorn gateway (`:8080`); agent spawns `python -m gateway.agent` (`:8090` console + lease loop).
- **Supervision** — a thread keeps the backend alive: if it exited and should be running, it auto-restarts with exponential backoff (up to 30 s).
- **Status** — polled every 3 s. Coordinator shows `running · nodes N · queue D`; agent shows `connected/connecting/not-connected · running K/total slots`. The icon switches online (copper) vs offline (gray).
- **Menu** — status · open UI · stop/start · restart · switch role · autostart · quit. Quitting the tray stops **that machine's** backend.
- **Autostart** — macOS uses a LaunchAgent plist; Windows uses the registry `Run` key.
- **Coordinator startup** — on macOS the tray runs `brew services start mysql` then `alembic upgrade head` before launching uvicorn.

**Windows packaging is delegated** to Codex-on-Windows via [`docs/WINDOWS_AGENT_TASK.md`](docs/WINDOWS_AGENT_TASK.md). The contract: **reuse `scripts/tray.py` as-is** (it already handles the Windows branches — `CREATE_NEW_PROCESS_GROUP`, `winreg` autostart, tray icon) and do **not** change the cluster protocol. Windows deliverables include `scripts/start_tray.bat`, `install_autostart.ps1` / `uninstall_autostart.ps1`, and an optional PyInstaller `VoiceGeneration.exe`.

> PyInstaller note: the frozen exe must run with the **repo root as its working directory** (or it can't find `models.yaml` and the model conda envs). A `pythonw.exe scripts/tray.py` shortcut is a documented, freeze-free fallback. The repo must stay on disk on every worker regardless — workers launch from the repo dir using their conda envs.

## REST API

Most user-facing routes require `Authorization: Bearer <token>` only when `settings.api_token` (or `VG_API_TOKEN`) is set. Cluster routes (`/v1/cluster/*`) use a separate `cluster.token`.

> **Security default:** the service binds `127.0.0.1` with **no auth** — intended for local use only. Expose it to other machines only by setting `settings.host: 0.0.0.0` **and** setting both `api_token` and `cluster.token`.

| Method & path | Description |
|---|---|
| `GET /health` | Health check `{ok, version}` |
| `POST /v1/tts` | Synthesize; returns audio + `X-Generation-Id`, `X-Cache` (HIT\|MISS), `X-Node` |
| `POST /v1/generations` | Submit async generation (200 cache hit, 202 queued) |
| `GET /v1/generations/{id}` | Poll a generation's status |
| `DELETE /v1/generations/{id}` | Cancel a queued generation (409 if leased) |
| `GET /v1/models` | List enabled models |
| `GET /v1/voices?model=<id>` | List voices (clone + built-in); config-only, never wakes a worker |
| `GET /v1/voice-library` | List all clone voices |
| `POST/PUT/DELETE /v1/voices[/{id}]` | Create / update / delete a clone voice (multipart) |
| `GET /v1/voices/{id}/audio` | Download a clone voice's reference WAV |
| `GET /v1/history` | Paginated/filtered history (`page, page_size, model, status, q, project`) |
| `PATCH /v1/history/{id}` | Reassign a generation's project |
| `GET /v1/history/{id}/audio` | Fetch audio (410 if evicted) |
| `DELETE /v1/history/{id}` | Delete a record (keeps disk audio) |
| `GET/POST/PUT/DELETE /v1/projects[/{id}]` | Manage projects |
| `GET /v1/settings` · `PUT /v1/settings` | Global settings + per-model runtime state; update writes `models.yaml` and hot-reloads |
| `PUT /v1/models/{id}/config` | Update one model's config (validates ports/paths/device) |
| `POST /v1/models/{id}/start\|stop\|restart` | Warm / stop / restart a model's replica pool |
| `GET /v1/system` | Service/version/platform, MPS flags, MySQL state, cache usage, models |
| `POST /v1/service/shutdown` | Gracefully stop the service |
| `POST /v1/cluster/register` | Agent registers (cluster-token auth) |
| `POST /v1/cluster/lease` | Agent long-polls to lease jobs by per-model capacity |
| `GET /v1/cluster/asset/{voice_id}` | Agent downloads a clone reference WAV |
| `POST /v1/cluster/jobs/{id}/result\|fail\|heartbeat` | Agent uploads result / reports failure / extends lease |
| `GET /v1/cluster/nodes` | Cluster overview: self, nodes, `queue_depth` |
| `GET /v1/cluster/connect-info` | Sub-node connect info (candidate URLs Tailscale-first, token) |
| `GET /v1/jobs/{id}` | Fetch a single job/history row |
| `GET /` · `GET /{path:path}` | Serve the web SPA (catch-all must remain last) |

> Any new `GET` route **must** be registered before the catch-all `@app.get('/{path:path}')` SPA fallback at the bottom of [`gateway/main.py`](gateway/main.py), or the fallback will swallow it.

## Configuration

`models.yaml` is **git-ignored** and per-machine. Start from [`models.example.yaml`](models.example.yaml) (`cp models.example.yaml models.yaml`). `PUT /v1/settings` and `PUT /v1/models/{id}/config` rewrite it atomically and keep a `models.yaml.bak`.

### Key settings

| Key | Default | Description |
|---|---|---|
| `settings.host` | `127.0.0.1` | Bind address; set `0.0.0.0` to accept other machines |
| `settings.port` | `8080` | Gateway HTTP port (also serves the web UI) |
| `settings.api_token` | `''` | If non-empty, REST requires `Bearer` (env `VG_API_TOKEN`) |
| `settings.cache_dir` / `cache_max_gb` | `cache` / `3.0` | Disk cache dir + size cap (GB); LRU-evicted. Config default is `3.0`; `models.example.yaml` uses `30.0`. |
| `settings.worker_idle_timeout` / `worker_start_timeout` | `300` · `180` | Idle reclaim · start wait (seconds). Config default idle is `300`; `models.example.yaml` uses `3600`. |
| `settings.default_model` / `default_format` | `cosyvoice3` / `wav` | Defaults when the request omits them |
| `settings.voices_file` | `voices.yaml` | Clone-voice manifest path |

### Cluster keys

| Key | Default | Description |
|---|---|---|
| `cluster.role` | `''` | `''` (pick at tray launch) \| `coordinator` \| `agent` |
| `cluster.node_id` / `node_name` | `local` | Unique node id / display name |
| `cluster.coordinator_url` | `''` | Agent: the coordinator URL (leave empty on coordinator) |
| `cluster.token` | `''` | Shared cluster secret; required when exposing the coordinator |
| `cluster.coordinator_runs_jobs` | `true` | Coordinator also runs inference via its embedded agent |
| `cluster.max_concurrency` | `1` | Node concurrency hint (this node's own inference parallelism) |
| `cluster.poll_interval` | `1.0` | Agent long-poll / idle sleep interval (seconds) |
| `cluster.lease_ttl` / `node_timeout` / `max_attempts` | `120` / `60` / `3` | Lease TTL · offline timeout · max attempts |
| `cluster.agent_host` / `agent_port` | `127.0.0.1` / `8090` | Sub-node web console bind / port |
| `cluster.enabled` | `false` | Whether the agent actively connects (toggled by the `:8090` console) |

### Per-model keys

`id`, `enabled`, `description`, `python` (interpreter; empty = gateway's), `backend` (`module:Class`), `host`, `port` (replica base), `languages`, `supports_cloning`, `replicas` (parallel processes, 1–8), `options` (e.g. `device: auto|mps|cpu|cuda`, `repo_dir`, `model_dir`, `model`), and `placement.allow` (list of node_ids permitted to run the model; empty = all).

### Environment overrides

`VG_API_TOKEN`, `VG_CLUSTER_ROLE`, `VG_NODE_ID`, `VG_NODE_NAME`, `VG_COORDINATOR_URL`, `VG_CLUSTER_TOKEN`, `VG_DATABASE_URL`, `VG_FFMPEG`, `VG_FFPROBE`.

> On a Windows worker: point each model's `python` at that env's `python.exe`, set `options.device: cuda` (verify `torch.cuda.is_available()`), point `model_dir`/`repo_dir` at local paths, and set the `system` model `enabled: false`.

## Data & cache

- **MySQL** (`voice_generation`; override with `VG_DATABASE_URL`, default `mysql+pymysql://root@127.0.0.1:3306/voice_generation`). Three tables: `generation_history`, `projects`, `cluster_nodes`. Timestamps are stored as naive UTC and serialized with a `Z` suffix.
- **Alembic** — `alembic upgrade head` auto-creates the DB then applies migrations (a linear chain of 4 revisions). `init_database()` also runs `create_all`, so tables can appear from the ORM; keep Alembic the source of truth in production.
- **Content-addressed disk cache** ([`gateway/cache.py`](gateway/cache.py)) — audio stored under `cache/<key[:2]>/<key>.<ext>`, keyed by SHA-256 of the output-determining params (model, voice, language, text, speed, format, mode, instruct_text, options, ref, ref_text). It **excludes** `project_id` and `assigned_node`, so identical content is shared and deduplicated. LRU eviction over `cache_max_gb` never touches `_logs`.
- **Transcode** ([`gateway/audio.py`](gateway/audio.py)) — workers return WAV; the gateway transcodes on demand to `wav | mp3 | opus` via system ffmpeg. `ffmpeg`/`ffprobe` are discovered via `VG_FFMPEG`/`VG_FFPROBE`, `PATH`, then common Homebrew dirs.
- **File-backed voices** ([`gateway/voice_store.py`](gateway/voice_store.py)) — `voices.yaml` (atomic write + `.bak`) and `voices/<id>/ref.wav`. Voice IDs match `^[A-Za-z0-9_-]{2,64}$`.

## Testing

Backend tests need a live MySQL at `root@127.0.0.1:3306` (empty password); `tests/conftest.py` forces the `voice_generation_test` DB so runs never touch your real history.

```bash
conda run -n vg-gateway python -m pytest -q   # backend
cd web && npm test                            # frontend (vitest)
```

> Tests use `create_all` (won't add new columns to existing tables). After changing ORM columns, drop the affected `voice_generation_test` tables so they're rebuilt.

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

- [`docs/CLUSTER.md`](docs/CLUSTER.md) — multi-machine cluster setup and operation
- [`docs/DESIGN.md`](docs/DESIGN.md) — architecture and design notes
- [`docs/WINDOWS_AGENT_TASK.md`](docs/WINDOWS_AGENT_TASK.md) — Windows agent/tray packaging contract
