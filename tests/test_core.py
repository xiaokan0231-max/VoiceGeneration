from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from gateway import cluster
from gateway.cache import AudioCache
from gateway.audio import convert
from gateway.media_tools import media_binary
from gateway.database import (
    ClusterNode, GenerationHistory, Project, create_project, db_session,
    delete_generation, delete_project, finish_generation, init_database,
    list_generations, list_projects, new_generation, set_generation_project,
)
from gateway.schemas import TTSRequest
from gateway.config import ModelConfig, Settings
from gateway.supervisor import Supervisor


@pytest.fixture(autouse=True)
def clean_history():
    init_database()
    with db_session() as db:
        db.execute(delete(GenerationHistory))
        db.execute(delete(Project))
        db.execute(delete(ClusterNode))
        db.commit()
    yield


def test_tts_schema_accepts_three_modes_and_validates_speed():
    for mode in ("clone", "instruct", "cross_lingual"):
        value = TTSRequest(text="测试", model="cosyvoice3", voice="narrator_zh", mode=mode)
        assert value.mode == mode
    with pytest.raises(Exception):
        TTSRequest(text="测试", model="cosyvoice3", voice="narrator_zh", speed=0)


def test_cache_key_includes_mode_and_eviction_preserves_logs(tmp_path: Path):
    cache = AudioCache(tmp_path, max_bytes=5)
    clone = cache.key({"text": "同一句", "mode": "clone", "instruct_text": ""})
    instruct = cache.key({"text": "同一句", "mode": "instruct", "instruct_text": "沉稳"})
    assert clone != instruct
    log = tmp_path / "_logs" / "worker.log"
    log.parent.mkdir(); log.write_bytes(b"keep me")
    cache.put(clone, "wav", b"123456")
    assert log.read_bytes() == b"keep me"


def test_ffmpeg_is_found_with_macos_app_path(monkeypatch):
    """A .app gets a minimal PATH; Homebrew ffmpeg must still be discovered."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    resolved = Path(media_binary("ffmpeg"))
    assert resolved.is_file() and resolved.name == "ffmpeg"


def test_opus_conversion_with_minimal_path(monkeypatch):
    import io
    import wave

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 1600)
    opus = convert(stream.getvalue(), "opus")
    assert opus.startswith(b"OggS")


def test_mysql_history_pagination_filter_and_delete():
    ids = []
    for index in range(25):
        row = new_generation(
            text=f"分页测试 {index}", model_id="cosyvoice3" if index % 2 else "system",
            voice_id="qa", voice_name="QA", mode="clone", language="zh",
            speed=1.0, format="wav", instruct_text=None, cache_key=f"{index:064d}",
        )
        finish_generation(row.id, status="completed", elapsed_seconds=.1)
        ids.append(row.id)
    rows, total = list_generations(page=2, page_size=10, status="completed", query="分页测试")
    assert total == 25
    assert len(rows) == 10
    assert delete_generation(ids[0]) is True
    _, remaining = list_generations(page=1, page_size=10)
    assert remaining == 24


def _gen(project_id, key):
    row = new_generation(
        text="项目测试", model_id="system", voice_id="qa", voice_name="QA",
        project_id=project_id, mode="clone", language="zh", speed=1.0,
        format="wav", instruct_text=None, cache_key=key,
    )
    finish_generation(row.id, status="completed", elapsed_seconds=.1)
    return row


def test_projects_count_filter_reassign_and_delete():
    project = create_project(name="项目X", description="d", color="#d98d52")
    g1 = _gen(project.id, "p1")
    g2 = _gen(None, "p2")

    # list_projects 带生成数
    summary = next(p for p in list_projects() if p["id"] == project.id)
    assert summary["generation_count"] == 1 and summary["name"] == "项目X"

    # 按项目 / 未归类 过滤
    rows, total = list_generations(project=project.id)
    assert total == 1 and rows[0].id == g1.id
    _, none_total = list_generations(project="__none__")
    assert none_total == 1

    # 事后指派
    assert set_generation_project(g2.id, project.id) is True
    _, after = list_generations(project=project.id)
    assert after == 2

    # 删除项目 → 关联生成置为未归类，项目消失
    assert delete_project(project.id) is True
    _, none_after = list_generations(project="__none__")
    assert none_after == 2
    assert all(p["id"] != project.id for p in list_projects())


def _queued(cache_key: str, model: str = "cosyvoice3"):
    return new_generation(
        text="集群任务", model_id=model, voice_id="narrator_zh", voice_name="旁白",
        mode="clone", language="zh", speed=1.0, format="wav", instruct_text=None,
        cache_key=cache_key, status="queued",
    )


def test_cluster_lease_is_atomic_and_respects_capacity():
    for i in range(3):
        _queued(f"k{i}")
    # 第一次认领 2 条
    first = cluster.lease_jobs("nodeA", ["cosyvoice3"], 2, 120)
    assert len(first) == 2
    # 再认领（容量 5）只剩 1 条 —— 已认领的不会被重复发出
    second = cluster.lease_jobs("nodeB", ["cosyvoice3"], 5, 120)
    assert len(second) == 1
    assert cluster.lease_jobs("nodeC", ["cosyvoice3"], 5, 120) == []
    ids = {j["id"] for j in first + second}
    assert len(ids) == 3  # 三条互不重复


def test_cancel_only_accepts_queued_jobs():
    queued = _queued("cancel-queued")
    assert cluster.cancel_queued_job(queued.id) == "cancelled"
    cancelled = db_session().get(GenerationHistory, queued.id)
    assert cancelled.status == "cancelled"
    assert cancelled.completed_at is not None
    assert cluster.lease_jobs("nodeA", ["cosyvoice3"], 1, 120) == []

    leased = _queued("cancel-leased")
    assert cluster.lease_jobs("nodeA", ["cosyvoice3"], 1, 120)
    assert cluster.cancel_queued_job(leased.id) == "conflict"
    assert cluster.cancel_queued_job("missing") == "missing"


def test_cluster_lease_respects_capacity_of_each_model():
    for i in range(3):
        _queued(f"cosy-{i}", "cosyvoice3")
        _queued(f"f5-{i}", "f5_tts")

    jobs = cluster.lease_jobs_by_model(
        "nodeA", {"cosyvoice3": 1, "f5_tts": 2}, 120,
    )

    assert Counter(job["model"] for job in jobs) == {
        "cosyvoice3": 1,
        "f5_tts": 2,
    }
    with db_session() as db:
        remaining = Counter(
            db.scalars(
                select(GenerationHistory.model_id).where(
                    GenerationHistory.status == "queued"
                )
            )
        )
    assert remaining == {"cosyvoice3": 2, "f5_tts": 1}


def test_cluster_requeue_expired_and_fail():
    row = _queued("rk")
    cluster.lease_jobs("nodeA", ["cosyvoice3"], 1, 120)
    # 把租约改到明确的过去时间（用固定旧时间，避免 DATETIME 秒级取整带来的临界抖动）
    with db_session() as db:
        obj = db.get(GenerationHistory, row.id)
        obj.lease_expires_at = datetime(2000, 1, 1)
        db.commit()
    assert cluster.requeue_expired(max_attempts=3) == 1
    after = db_session().get(GenerationHistory, row.id)
    assert after.status == "queued" and after.assigned_node is None
    # 认领后直接判失败（attempts 已达上限）
    cluster.lease_jobs("nodeA", ["cosyvoice3"], 1, 120)
    assert cluster.fail_or_requeue(row.id, "boom", max_attempts=1) == "failed"


def test_cluster_dedup_completed():
    a = _queued("same")
    b = _queued("same")
    moved = cluster.dedup_completed("same", "cache/x/same.wav", "audio/wav", 100, 1.0, keep_id=a.id)
    assert moved == 1
    other = db_session().get(GenerationHistory, b.id)
    assert other.status == "completed" and other.audio_path == "cache/x/same.wav"


def test_cluster_node_registry():
    cluster.register_node(node_id="win-4060", name="Windows 4060", role="agent",
                          models=["cosyvoice3", "f5_tts"], max_concurrency=2, version="1.0.0")
    nodes = cluster.list_nodes()
    assert any(n["node_id"] == "win-4060" and n["models"] == ["cosyvoice3", "f5_tts"] for n in nodes)
    assert cluster.node_name_map().get("win-4060") == "Windows 4060"
    cluster.update_node_runtime("win-4060", {
        "workers": [{"id": "cosyvoice3#1", "model": "cosyvoice3", "index": 1,
                     "port": 8110, "started": True, "active": True, "speed": .42},
                    {"id": "cosyvoice3#2", "model": "cosyvoice3", "index": 2,
                     "port": 8111, "started": True, "active": False, "speed": .58}],
    })
    performance = new_generation(
        text="性能样本", model_id="cosyvoice3", voice_id="qa", voice_name="QA",
        mode="clone", language="zh", speed=1.0, format="wav", instruct_text=None,
        cache_key="perf", status="leased", assigned_node="win-4060",
        worker_id="cosyvoice3#1", attempts=1,
    )
    finish_generation(
        performance.id, status="completed", duration_seconds=10,
        inference_seconds=20, elapsed_seconds=21,
    )
    node = next(item for item in cluster.list_nodes() if item["node_id"] == "win-4060")
    assert node["started_workers"] == 2
    assert node["working_workers"] == 1
    assert node["total_speed"] == pytest.approx(.42)
    assert node["latest_speed"] == pytest.approx(1.0)
    assert node["average_speed_30m"] == pytest.approx(.5)
    assert node["workers"][0]["speed_30m"] == pytest.approx(.5)


def test_supervisor_reports_worker_count_and_realtime_speed():
    supervisor = Supervisor(Settings(), [ModelConfig(id="cosyvoice3", port=8110, replicas=2)])
    worker = supervisor.pools["cosyvoice3"][0]
    worker.process = type("Process", (), {"poll": lambda self: None})()
    supervisor.begin_work(worker, "job-1", "测试文本")
    supervisor.finish_work(worker, audio_seconds=10, elapsed_seconds=20)
    supervisor.begin_work(worker, "job-2", "继续测试")
    metrics = supervisor.runtime_metrics()
    assert metrics["started_workers"] == 1
    assert metrics["working_workers"] == 1
    assert metrics["total_speed"] == pytest.approx(.5)
    assert metrics["latest_speed"] == pytest.approx(.5)
    assert metrics["workers"][0]["speed"] == pytest.approx(.5)
