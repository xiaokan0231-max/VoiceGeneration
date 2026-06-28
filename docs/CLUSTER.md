# 多机协同生成（集群）

让多台机器一起跑 TTS：**协调端**（默认 Mac）持有任务队列、音频缓存、历史、音色；
**工作节点**（Mac 自己 + Windows 4060 + …）通过「认领」从队列取活、本地推理、回传结果。
**所有音频与历史集中存在协调端。**

## 工作原理

- 客户端调 `POST /v1/tts` → 协调端入队（`generation_history.status=queued`）→ 等任意节点完成 → 返回音频。
- 每个工作节点（含协调端自带的内置 agent）长轮询 `POST /v1/cluster/lease` 认领任务，
  用本地 worker 推理出 WAV，再 `POST /v1/cluster/jobs/{id}/result` 回传；协调端转码、入缓存、写历史。
- **两台都榨干**：所有在线节点并发认领，队列由大家并行排干，吞吐 = 各节点之和。
- **容错**：认领有租约（lease_ttl）。节点崩溃/掉线/断网 → 租约到期自动重派（最多 max_attempts 次）。
  协调端重启后，残留任务自动重入队；任务持久化在 MySQL。
- 响应头 `X-Node` 表示这次由哪台机器生成；历史每条显示「由 <节点名> 生成」。

## 组网（推荐 Tailscale）

1. 两台机器都装 [Tailscale](https://tailscale.com/) 并登录同一账号 → 各获得稳定私有 IP（如 `100.x.x.x`）。
2. 协调端（Mac）的地址即 `http://<mac-tailscale-ip>:8080`。
3. 也可用同局域网：协调端地址用 `http://<mac-局域网IP>:8080` 或 `http://mac-main.local:8080`。

> 协调端默认只监听 `127.0.0.1`。要让别的机器连，需用 `0.0.0.0` 起网关：
> `conda run -n vg-gateway uvicorn gateway.main:app --host 0.0.0.0 --port 8080`
> （或把 models.yaml 的 `settings.host` 改成 `0.0.0.0`）。**对外暴露务必设 `cluster.token`。**

## 协调端配置（Mac，models.yaml → settings.cluster）

```yaml
settings:
  cluster:
    role: coordinator
    node_id: mac-main
    node_name: Mac 主机
    coordinator_url: ''
    token: "选一串随机密钥"      # 多机时必填；两端一致
    max_concurrency: 1            # Mac 也参与生成的并行度；想让 Mac 只协调改 coordinator_runs_jobs: false
    coordinator_runs_jobs: true
```

## 加入一台 Windows 4060 工作节点

1. 装 Tailscale；装 miniconda、git。
2. clone 本仓库，建带 **CUDA** 的环境并装权重：
   ```powershell
   bash scripts/setup_worker.sh cosyvoice3   # 在 Git-Bash 里；或手动建 vg-cosyvoice 环境
   # 关键：装 CUDA 版 torch（cu121），例如：
   conda run -n vg-cosyvoice pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
   bash scripts/download_weights.sh cosyvoice3
   conda run -n vg-f5 pip install f5-tts torch torchaudio --index-url https://download.pytorch.org/whl/cu121
   ```
3. 准备并编辑本机配置（`models.yaml` 不入 git，需从模板复制）：
   ```powershell
   Copy-Item models.example.yaml models.yaml
   ```
   把 cosyvoice3 / f5_tts 的 `python` 指向 Windows 上对应 conda 环境的 `python.exe`，
   `options.device: cuda`，`model_dir/repo_dir` 指向本机路径；`system` 模型设 `enabled: false`（Windows 无 `say`）。
   cluster 字段可直接用 `scripts/agent.ps1` 里的环境变量覆盖（node_id 等）。
4. 启动 agent（编辑 `scripts/agent.ps1` 里的 `VG_COORDINATOR_URL` / `VG_CLUSTER_TOKEN`）：
   ```powershell
   conda activate vg-gateway
   ./scripts/agent.ps1
   ```
   节点会自动注册并开始认领。Windows 节点**不需要** MySQL / ffmpeg（转码与存储都在协调端）。

> 环境变量可免改 yaml 覆盖集群字段：`VG_CLUSTER_ROLE / VG_NODE_ID / VG_NODE_NAME / VG_COORDINATOR_URL / VG_CLUSTER_TOKEN`。

## 验证

- 协调端 `GET /v1/cluster/nodes` 应看到 `mac-main` 与 `win-4060` 均 `online`。
- 发一批生成（如 `examples/batch_pregenerate.py`）→ 两台同时各跑各的，队列被并行排干。
- 任一条生成的响应头 `X-Node` / 历史「由 X 生成」标明实际执行节点。
- 合成中途关掉 Windows agent → 该任务租约到期后被另一台接手完成，不丢不重复。

## 让 Mac 只协调、把活全交给 Windows

把 Mac 的 `settings.cluster.coordinator_runs_jobs` 设为 `false` 并重启网关。此时 Mac 不跑推理，
全部任务由 Windows 认领；Windows 全离线时任务排队等待。
