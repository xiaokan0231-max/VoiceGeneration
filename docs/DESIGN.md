# VoiceGeneration 设计说明

## 背景与目标

`歴史` 是一个中日双语的抗战编年史平台（FastAPI + React + MySQL），有大量旁白
文本（story 的 synopsis/epilogue、event 的 description、perspectives 的双方叙述、
人物 bio 等），需要把它们转成语音播放。本服务先满足这个需求，长期作为本机的
**通用语音生成服务**：一套 API、多模型可插拔、可随时增删。

约束：
- 机器为 Apple M5 Pro / 24G 内存 / macOS（arm64），已装 conda + ffmpeg。
- CosyVoice 与 F5-TTS 依赖互相冲突，且各自较重；24G 内存不宜同时常驻多个大模型。

## 架构：网关 + 独立 worker 进程

```
客户端 ──HTTP──> gateway(FastAPI) ──HTTP──> worker(system)      [gateway 环境]
                     │  ├─ registry (读 models.yaml)
                     │  ├─ cache    (内容哈希 + LRU)
                     │  └─ supervisor ──spawn──> worker(cosyvoice3)  [vg-cosyvoice 环境]
                     │                ──spawn──> worker(f5_tts)       [vg-f5 环境]
```

- **gateway**：唯一对外入口。负责鉴权、参数校验、音色解析、缓存、转码、把请求转发
  给对应 worker，并管理 worker 生命周期。
- **worker**：每个模型一个独立子进程，跑在各自的 conda 环境里，依赖完全隔离。
  统一用 `worker_runtime/server.py` 承载，模型差异只在 `workers/<model>/backend.py`。
- 进程间用本机 HTTP（各 worker 监听不同端口，见 models.yaml）。

### 为什么不用单一环境 / 不用 Docker
- 单环境会撞依赖（torch 版本、numpy、transformers 等）。
- macOS 上 Docker 无法透传 MPS（GPU），容器内只能 CPU，太慢。故用原生进程 + 多 conda 环境。

## 可插拔机制

“插口”就是 `models.yaml`：

- **增**：加一段配置（id / python(conda 环境的解释器路径) / backend(模块:类) /
  端口 / 语言 / 是否支持克隆 / options），再写一个继承 `TTSBackend` 的 `backend.py`。
- **删**：删掉这段配置（可选地删 `workers/<model>/` 与 conda 环境）。
- **停**：`enabled: false`。

`TTSBackend` 接口（`worker_runtime/base.py`）只要求实现：
- `synthesize(SynthRequest) -> wav bytes`（权重首次调用惰性加载）
- 可选 `list_voices() -> [...]`（模型内置音色）

worker 返回统一 16-bit PCM WAV，转码（mp3/opus）集中在 gateway 用 ffmpeg 完成。

## 生命周期与内存

- `supervisor.ensure_running(model)`：加锁，未运行则 spawn 子进程并轮询 `/health`
  直到就绪；并发请求只会拉起一次。
- 后台 reaper 每 15s 扫描，关闭空闲超过 `worker_idle_timeout`(默认 300s) 的 worker。
- 效果：同一时刻通常只有一个大模型常驻，符合 24G 内存约束。

## 声音克隆

- 克隆音色在 `voices.yaml` 登记：`ref_audio`(参考音频) + `ref_text`(对应文字) +
  `models`(可用于哪些模型)。
- gateway 解析请求里的 `voice`：是克隆音色就把参考音频路径与文字传给 worker；
  否则当作模型内置音色 id。
- CosyVoice：`inference_zero_shot(text, ref_text, ref_16k)`；F5-TTS：`F5TTS.infer(...)`。

## 缓存

- key = `sha256(规范化 JSON{model,voice,language,text,speed,format,ref,ref_text})`。
- 路径：`cache/<key[:2]>/<key>.<ext>`，原子写（tmp→rename）。
- 超过 `cache_max_gb` 按访问时间 LRU 淘汰。命中返回头带 `X-Cache: HIT`。

## 接入「歴史」（两种方式都支持）

1. **实时代理 + 缓存**：把 `examples/history_audio_router.py` 装到 `歴史/backend`，
   前端 `POST /api/audio` 第一次触发合成、落盘、之后复用（与其 images.py 一致）。
2. **批量预生成**：`examples/batch_pregenerate.py` 遍历种子文本，提前合成、预热缓存。

联调阶段把 `VG_MODEL=system`，上线切到 `cosyvoice3` 或 `f5_tts`。

## 安全 / 运维

- 默认仅监听 `127.0.0.1`，无鉴权；如需暴露设 `VG_API_TOKEN`，客户端带
  `Authorization: Bearer <token>`。
- worker 日志在 `cache/_logs/worker-<id>.log`，排查模型加载失败看这里。

## 后续可扩展

- 流式合成（worker 分块返回 + gateway SSE/chunked）。
- 单 worker 内并发/排队（大模型串行更稳，可加请求队列）。
- 兼容 OpenAI `/v1/audio/speech` 协议，方便现成客户端直接接。
- 更多模型：IndexTTS、Spark-TTS、ChatTTS 等，按同样的 backend 模式接入。
