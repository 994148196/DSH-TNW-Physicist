"""端到端恢复测试：保存 → 关闭 → 重启 → 恢复（Phase 1 验收）。"""
from qresearch.core.events import Event, EventLog
from qresearch.core.io import export_bundle, import_bundle
from qresearch.core.models import ProjectBundle
from qresearch.core.status import Actor
from qresearch.core.storage import Storage
from qresearch.core.testing import make_demo_bundle


def test_restart_restores_full_state(tmp_path):
    db_path = tmp_path / "state.sqlite"
    s1 = Storage(db_path)
    bundle = make_demo_bundle("proj_resume")
    s1.save_bundle(bundle)
    s1.close()  # 关闭程序

    s2 = Storage(db_path)  # 重新启动
    restored = s2.load_bundle("proj_resume")
    s2.close()
    assert isinstance(restored, ProjectBundle)
    assert restored.model_dump() == bundle.model_dump()


def test_export_import_roundtrip_yaml_and_json(db, tmp_path):
    bundle = make_demo_bundle("proj_io")
    db.save_bundle(bundle)

    for name in ("state.yaml", "state.json"):
        out = export_bundle(db, "proj_io", tmp_path / name)
        other = Storage(tmp_path / f"other_{name.replace('.', '_')}.sqlite")
        imported = import_bundle(other, out)
        assert imported.model_dump() == bundle.model_dump()
        other.close()


def test_events_persist_across_restart(tmp_path):
    log_path = tmp_path / "events.jsonl"
    log1 = EventLog(log_path)
    log1.append(Event(actor=Actor.HUMAN, action="approve",
                      project_id="p1", object_type="ResearchPlan", object_id="plan_1"))
    log1.append(Event(actor=Actor.MODEL, action="save_bundle", project_id="p2"))

    log2 = EventLog(log_path)  # 重启
    assert len(log2.events()) == 2
    assert [e.action for e in log2.events(project_id="p1")] == ["approve"]
    assert log2.events(project_id="p1")[0].actor == Actor.HUMAN
