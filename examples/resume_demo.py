"""Phase 1 验收演示：创建 → 保存 → 关闭 → 重启 → 恢复全部状态（计划 v2 §9 Phase 1）。

用法：.venv/Scripts/python.exe examples/resume_demo.py
"""
import shutil
import sys
from pathlib import Path

from qresearch.core.events import Event, EventLog
from qresearch.core.io import export_bundle
from qresearch.core.models import utcnow
from qresearch.core.status import Actor
from qresearch.core.storage import Storage
from qresearch.core.testing import make_demo_bundle

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "research_data" / "demo"


def main() -> int:
    if DEMO_DIR.exists():
        shutil.rmtree(DEMO_DIR)
    DEMO_DIR.mkdir(parents=True)

    # ---- 阶段一：创建并保存（"第一次会话"） -------------------------------
    db_path = DEMO_DIR / "state.sqlite"
    log_path = DEMO_DIR / "events.jsonl"
    storage = Storage(db_path)
    log = EventLog(log_path)

    bundle = make_demo_bundle("proj_heisenberg_demo")
    storage.save_bundle(bundle)
    log.append(Event(actor=Actor.SYSTEM, action="save_bundle",
                     project_id=bundle.project.project_id,
                     object_type="ProjectBundle",
                     object_id=bundle.project.project_id))
    log.append(Event(actor=Actor.MODEL, action="status_change",
                     project_id=bundle.project.project_id,
                     object_type="Decision", object_id="dec_001",
                     detail={"recommendation": "iterate"}))
    print(f"[session 1] 已保存 {bundle.project.project_id} -> {db_path}")

    # 导出 YAML 供人工查看
    yaml_path = export_bundle(storage, bundle.project.project_id,
                              DEMO_DIR / "heisenberg_state.yaml")
    print(f"[session 1] 已导出 YAML -> {yaml_path}")
    storage.close()  # 关闭程序

    # ---- 阶段二：重启并恢复（"第二次会话"） -------------------------------
    storage2 = Storage(db_path)  # 全新实例 = 进程重启
    restored = storage2.load_bundle("proj_heisenberg_demo")
    log2 = EventLog(log_path)

    assert restored.project.title == bundle.project.title
    assert len(restored.goals) == 1 and len(restored.plans) == 1
    assert len(restored.hypotheses) == 1 and len(restored.decisions) == 1
    assert len(log2.events(project_id="proj_heisenberg_demo")) == 2
    storage2.require_valid_references(restored)

    print("[session 2] 状态恢复成功：")
    print(f"  project    : {restored.project.title} ({restored.project.status})")
    print(f"  goal       : {restored.goals[0].question}")
    print(f"  hypotheses : {len(restored.hypotheses)} 条（含证伪试验 "
          f"{restored.hypotheses[0].falsification_tests[0]}）")
    print(f"  plan       : v{restored.plans[0].version}，{len(restored.plans[0].steps)} 步")
    print(f"  decision   : {restored.decisions[0].recommendation} "
          f"(requires_human={restored.decisions[0].requires_human})")
    print(f"  events     : {len(log2.events())} 条已恢复")
    print("RESUME DEMO: PASS")
    storage2.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
