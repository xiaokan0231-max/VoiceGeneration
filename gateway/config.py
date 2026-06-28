"""加载 models.yaml / voices.yaml，提供类型化的配置对象。"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 仓库根目录（gateway/ 的上一级）
ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ClusterConfig:
    role: str = "coordinator"          # coordinator(含本地 agent) | agent
    node_id: str = "local"
    node_name: str = ""
    coordinator_url: str = ""           # agent 必填；coordinator 留空(走本地)
    token: str = ""                     # 集群内鉴权
    max_concurrency: int = 1
    lease_ttl: int = 120
    node_timeout: int = 60
    poll_interval: float = 1.0
    coordinator_runs_jobs: bool = True  # 协调端是否也跑推理
    max_attempts: int = 3

    @property
    def is_coordinator(self) -> bool:
        return self.role == "coordinator"


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8080
    api_token: str = ""
    cache_dir: str = "cache"
    cache_max_gb: float = 3.0
    worker_idle_timeout: int = 300
    worker_start_timeout: int = 180
    default_model: str = "cosyvoice3"
    default_format: str = "wav"
    voices_file: str = "voices.yaml"
    cluster: ClusterConfig = field(default_factory=ClusterConfig)

    @property
    def cache_path(self) -> Path:
        return (ROOT / self.cache_dir).resolve()


@dataclass
class ModelConfig:
    id: str
    enabled: bool = True
    description: str = ""
    python: str = ""                       # 空 => 用 gateway 解释器（sys.executable）
    backend: str = ""                      # "module.path:ClassName"
    host: str = "127.0.0.1"
    port: int = 0
    languages: list[str] = field(default_factory=list)
    supports_cloning: bool = False
    replicas: int = 1                                        # 同模型并行 worker 进程数
    options: dict[str, Any] = field(default_factory=dict)
    placement: dict[str, Any] = field(default_factory=dict)  # {allow: [node_id,...]}

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def allows(self, node_id: str) -> bool:
        allow = self.placement.get("allow") or []
        return not allow or node_id in allow

    @property
    def python_exe(self) -> str:
        return self.python or sys.executable


@dataclass
class Voice:
    id: str
    name: str
    language: str
    ref_audio: str
    ref_text: str
    models: list[str] = field(default_factory=list)  # 空 => 所有支持克隆的模型

    @property
    def ref_audio_path(self) -> Path:
        return (ROOT / self.ref_audio).resolve()

    def usable_by(self, model_id: str) -> bool:
        return not self.models or model_id in self.models


@dataclass
class AppConfig:
    settings: Settings
    models: list[ModelConfig]
    voices: list[Voice]

    def model(self, model_id: str) -> ModelConfig | None:
        return next((m for m in self.models if m.id == model_id), None)

    def enabled_models(self) -> list[ModelConfig]:
        return [m for m in self.models if m.enabled]

    def clone_voice(self, voice_id: str) -> Voice | None:
        return next((v for v in self.voices if v.id == voice_id), None)


def load_config(models_file: str | None = None) -> AppConfig:
    models_path = Path(models_file) if models_file else ROOT / "models.yaml"
    raw = yaml.safe_load(models_path.read_text(encoding="utf-8")) or {}

    raw_settings = dict(raw.get("settings") or {})
    cluster_raw = dict(raw_settings.pop("cluster", None) or {})
    settings = Settings(**raw_settings)
    settings.cluster = ClusterConfig(**cluster_raw)
    if not settings.cluster.node_name:
        settings.cluster.node_name = settings.cluster.node_id
    # 环境变量覆盖（方便 Windows 节点/测试免改 yaml）
    c = settings.cluster
    c.role = os.environ.get("VG_CLUSTER_ROLE", c.role)
    c.node_id = os.environ.get("VG_NODE_ID", c.node_id)
    c.node_name = os.environ.get("VG_NODE_NAME", c.node_name)
    c.coordinator_url = os.environ.get("VG_COORDINATOR_URL", c.coordinator_url)
    c.token = os.environ.get("VG_CLUSTER_TOKEN", c.token)
    models = [ModelConfig(**m) for m in (raw.get("models") or [])]

    voices: list[Voice] = []
    voices_path = ROOT / settings.voices_file
    if voices_path.exists():
        vraw = yaml.safe_load(voices_path.read_text(encoding="utf-8")) or {}
        voices = [Voice(**v) for v in (vraw.get("voices") or [])]

    # 环境变量可覆盖鉴权 token（部署时用）
    settings.api_token = os.environ.get("VG_API_TOKEN", settings.api_token)
    return AppConfig(settings=settings, models=models, voices=voices)


def load_raw_models() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "models.yaml").read_text(encoding="utf-8")) or {}


def save_raw_models(raw: dict[str, Any]) -> None:
    """原子保存模型配置，同时保留一份最近备份。"""
    path = ROOT / "models.yaml"
    backup = ROOT / "models.yaml.bak"
    if path.exists():
        backup.write_bytes(path.read_bytes())
    fd, tmp = tempfile.mkstemp(dir=str(ROOT), prefix="models-", suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
