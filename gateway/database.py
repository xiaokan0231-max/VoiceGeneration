"""MySQL generation-history storage.

Audio stays in the existing content-addressed disk cache; MySQL stores durable
metadata and the relative cache path so the UI can browse past work.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymysql
from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text, create_engine, func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import ROOT


DEFAULT_DATABASE_URL = (
    "mysql+pymysql://root@127.0.0.1:3306/voice_generation?charset=utf8mb4"
)


class Base(DeclarativeBase):
    pass


class GenerationHistory(Base):
    __tablename__ = "generation_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    voice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    voice_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="clone")
    language: Mapped[str | None] = mapped_column(String(24), nullable=True)
    speed: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    format: Mapped[str] = mapped_column(String(12), nullable=False, default="wav")
    instruct_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    # 集群任务字段（单机时 assigned_node 即本机 node_id）
    assigned_node: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    inference_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    audio_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ClusterNode(Base):
    __tablename__ = "cluster_nodes"

    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(24), nullable=False, default="agent")
    models: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="online")
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


_engine = None
SessionLocal: sessionmaker[Session] | None = None


def database_url() -> str:
    return os.environ.get("VG_DATABASE_URL", DEFAULT_DATABASE_URL)


def ensure_database_exists(url: str | None = None) -> None:
    target = make_url(url or database_url())
    database = target.database
    if not database:
        raise RuntimeError("VG_DATABASE_URL 必须包含数据库名")
    conn = pymysql.connect(
        host=target.host or "127.0.0.1",
        port=target.port or 3306,
        user=target.username or "root",
        password=target.password or "",
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            safe_name = database.replace("`", "``")
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{safe_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        conn.close()


def init_database() -> None:
    global _engine, SessionLocal
    ensure_database_exists()
    _engine = create_engine(database_url(), pool_pre_ping=True, pool_recycle=1800)
    Base.metadata.create_all(_engine)
    SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)


def db_session() -> Session:
    if SessionLocal is None:
        init_database()
    assert SessionLocal is not None
    return SessionLocal()


def new_generation(**values: Any) -> GenerationHistory:
    status = values.pop("status", "running")
    row = GenerationHistory(
        id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        status=status,
        **values,
    )
    with db_session() as db:
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def finish_generation(row_id: str, **values: Any) -> None:
    with db_session() as db:
        row = db.get(GenerationHistory, row_id)
        if not row:
            return
        for key, value in values.items():
            setattr(row, key, value)
        row.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()


def get_generation(row_id: str) -> GenerationHistory | None:
    with db_session() as db:
        return db.get(GenerationHistory, row_id)


def delete_generation(row_id: str) -> bool:
    with db_session() as db:
        row = db.get(GenerationHistory, row_id)
        if not row:
            return False
        path = audio_file(row)          # 解析磁盘文件（不存在/越界则为 None）
        audio_path = row.audio_path
        db.delete(row)
        db.commit()
        # 同时删除磁盘上的音频文件；但缓存按内容去重，多条历史可能指向同一文件，
        # 只有在没有其它记录再引用它时才真正删除，避免误删仍在用的缓存。
        if path is not None and audio_path:
            still_referenced = db.scalar(
                select(func.count())
                .select_from(GenerationHistory)
                .where(GenerationHistory.audio_path == audio_path)
            )
            if not still_referenced:
                path.unlink(missing_ok=True)
        return True


def list_generations(
    *, page: int = 1, page_size: int = 20, model: str | None = None,
    status: str | None = None, query: str | None = None, project: str | None = None,
) -> tuple[list[GenerationHistory], int]:
    with db_session() as db:
        stmt = select(GenerationHistory)
        count_stmt = select(func.count()).select_from(GenerationHistory)
        filters = []
        if model:
            filters.append(GenerationHistory.model_id == model)
        if status:
            filters.append(GenerationHistory.status == status)
        if query:
            filters.append(GenerationHistory.text.like(f"%{query}%"))
        if project == "__none__":
            filters.append(GenerationHistory.project_id.is_(None))
        elif project:
            filters.append(GenerationHistory.project_id == project)
        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)
        total = int(db.scalar(count_stmt) or 0)
        rows = list(
            db.scalars(
                stmt.order_by(GenerationHistory.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total


def audio_file(row: GenerationHistory) -> Path | None:
    if not row.audio_path:
        return None
    path = (ROOT / row.audio_path).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def history_dict(row: GenerationHistory, project_name: str | None = None,
                 node_name: str | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "text": row.text,
        "model": row.model_id,
        "voice": row.voice_id,
        "voice_name": row.voice_name,
        "project_id": row.project_id,
        "project_name": project_name,
        "assigned_node": row.assigned_node,
        "worker_id": row.worker_id,
        "node_name": node_name or row.assigned_node,
        "mode": row.mode,
        "language": row.language,
        "speed": row.speed,
        "format": row.format,
        "instruct_text": row.instruct_text,
        "status": row.status,
        "duration_seconds": row.duration_seconds,
        "byte_size": row.byte_size,
        "cache_hit": row.cache_hit,
        "elapsed_seconds": row.elapsed_seconds,
        "inference_seconds": row.inference_seconds,
        "error_message": row.error_message,
        "audio_available": audio_file(row) is not None,
        "created_at": row.created_at.isoformat() + "Z",
        "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
    }


# --- 项目(Project) -----------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def project_dict(row: Project, generation_count: int = 0) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "color": row.color,
        "generation_count": generation_count,
        "created_at": row.created_at.isoformat() + "Z",
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


def create_project(*, name: str, description: str | None = None,
                   color: str | None = None) -> Project:
    row = Project(id=str(uuid.uuid4()), name=name, description=description,
                  color=color, created_at=_utcnow(), updated_at=_utcnow())
    with db_session() as db:
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def get_project(project_id: str) -> Project | None:
    with db_session() as db:
        return db.get(Project, project_id)


def update_project(project_id: str, **values: Any) -> Project | None:
    with db_session() as db:
        row = db.get(Project, project_id)
        if not row:
            return None
        for key, value in values.items():
            if value is not None:
                setattr(row, key, value)
        row.updated_at = _utcnow()
        db.commit()
        db.refresh(row)
    return row


def delete_project(project_id: str) -> bool:
    """删除项目，并把其下生成记录置为未归类。"""
    with db_session() as db:
        row = db.get(Project, project_id)
        if not row:
            return False
        db.execute(
            update(GenerationHistory)
            .where(GenerationHistory.project_id == project_id)
            .values(project_id=None)
        )
        db.delete(row)
        db.commit()
        return True


def list_projects() -> list[dict[str, Any]]:
    with db_session() as db:
        counts = dict(
            db.execute(
                select(GenerationHistory.project_id, func.count())
                .group_by(GenerationHistory.project_id)
            ).all()
        )
        rows = list(db.scalars(select(Project).order_by(Project.created_at.desc())))
        return [project_dict(r, int(counts.get(r.id, 0))) for r in rows]


def project_name_map() -> dict[str, str]:
    with db_session() as db:
        return {r.id: r.name for r in db.scalars(select(Project))}


def set_generation_project(gen_id: str, project_id: str | None) -> bool:
    with db_session() as db:
        row = db.get(GenerationHistory, gen_id)
        if not row:
            return False
        row.project_id = project_id
        db.commit()
        return True
