"""对外 REST API 的请求/响应模型。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, description="要合成的文字")
    model: str = Field(..., description="模型 id，见 GET /v1/models")
    voice: str = Field(..., description="音色 id：克隆音色见 voices.yaml，内置音色见 GET /v1/voices")
    language: str | None = Field(None, description="语言代码，如 zh / ja / en；留空则由模型自行判断")
    speed: float = Field(1.0, gt=0.1, le=3.0, description="语速倍率")
    format: str | None = Field(None, description="wav | mp3 | opus；留空用全局默认")
    mode: Literal["clone", "instruct", "cross_lingual"] = "clone"
    instruct_text: str | None = Field(None, max_length=1000, description="指令控制模式的风格指令")
    project_id: str | None = Field(None, description="所属项目 id；留空=未归类")


class VoiceInfo(BaseModel):
    id: str
    name: str
    language: str | None = None
    kind: str  # "clone" | "builtin"
    model: str


class ModelInfo(BaseModel):
    id: str
    description: str
    enabled: bool
    languages: list[str]
    supports_cloning: bool
    loaded: bool  # worker 进程当前是否在运行


class SettingsUpdate(BaseModel):
    default_model: str | None = None
    default_format: Literal["wav", "mp3", "opus"] | None = None
    worker_idle_timeout: int | None = Field(None, ge=30, le=86400)
    worker_start_timeout: int | None = Field(None, ge=30, le=1800)
    cache_max_gb: float | None = Field(None, ge=0.1, le=100)


class ModelConfigUpdate(BaseModel):
    enabled: bool | None = None
    description: str | None = Field(None, max_length=500)
    python: str | None = None
    port: int | None = Field(None, ge=1024, le=65535)
    languages: list[str] | None = None
    options: dict[str, Any] | None = None


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="项目名称")
    description: str | None = Field(None, max_length=2000)
    color: str | None = Field(None, max_length=16, description="UI 标签色，如 #d98d52")


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    color: str | None = Field(None, max_length=16)


class HistoryProjectUpdate(BaseModel):
    project_id: str | None = Field(None, description="目标项目 id；null=移出到未归类")


class ClusterRegister(BaseModel):
    node_id: str
    name: str = ""
    role: str = "agent"
    models: list[str] = Field(default_factory=list)
    max_concurrency: int = 1
    version: str | None = None


class ClusterLease(BaseModel):
    node_id: str
    models: list[str] = Field(default_factory=list)
    capacity: int = 1


class JobFail(BaseModel):
    node_id: str | None = None
    error: str = ""
