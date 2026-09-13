"""Tool Builder 主流程（计划 v2 Phase 6）。

缺口 → Tool Spec（人审核，tool_specs/）→ DSH 编码（隔离 workspace：实现+自测）
→ golden 套件自动跑（失败回喂修复，上限 max_repair_rounds）→ 三层验证
→ 批评者审查 diff vs Spec → 构建报告 → 人工批准 → 注册（正式名 + fixtures
安装到 benchmarks/golden/）。

铁律：出题人≠答题人。编码 prompt 不含任何基准数值；fixtures/容差修改路径
不在编码站手上。防串通的完整保障 = 本流程 + 批评者 + 人工批准三道关。
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from qresearch.core.events import Event, EventLog
from qresearch.core.status import Actor, VerificationStatus
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient
from qresearch.stations.prompts import REPORT, TOOL_BUILD, TOOL_BUILD_REPAIR, TOOL_CRITIC
from qresearch.stations.schemas import CritiqueOutput
from qresearch.tools.registry import ToolSpec, get_tool, register, unregister
from qresearch.verification.manager import VerificationManager

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- 候选模块加载
def _load_candidate_module(module_path: Path, module_name: str):
    """从交付文件加载候选实现；要求 Inputs（pydantic 模型）与 run(dict)->dict。"""
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块文件：{module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    inputs_model = getattr(module, "Inputs", None)
    run_fn = getattr(module, "run", None)
    if not (isinstance(inputs_model, type) and issubclass(inputs_model, BaseModel)):
        raise AttributeError("交付文件缺少 Inputs（pydantic BaseModel）")
    if not callable(run_fn):
        raise AttributeError("交付文件缺少 run(inputs: dict) -> dict")
    return module


# ---------------------------------------------------------------- 构建结果
@dataclass
class BuildResult:
    tool: str
    candidate_name: str
    status: str  # registered / rejected / failed
    attempts: int
    workspace: Path
    report_path: Path
    golden_passed: bool
    verification_overall: str | None = None
    critic_verdict: str | None = None
    failures: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 主流程
def build_tool(
    client: DSHClient,
    storage: Storage,
    event_log: EventLog,
    spec_path: str | Path,
    *,
    max_repair_rounds: int = 3,
    auto_approve: bool = False,
    builds_root: str | Path | None = None,
    client_factory: "Callable[[Path], DSHClient] | None" = None,
) -> BuildResult:
    """client_factory：live 编码用——每轮 attempt 以 cwd=workspace 新建 runtime，
    保证 agent 的工作目录就是交付目录（对单 client 依赖 prompt 里的绝对路径不可靠：
    Windows 反斜杠路径易被误读为相对路径）。离线脚本测试用注入 client 即可。"""
    from typing import Callable

    from .spec import ToolBuildSpec

    spec = ToolBuildSpec.load(spec_path)
    spec_file = Path(spec_path).resolve()
    candidate_name = f"candidate_{spec.tool}"
    project_id = f"toolbuild_{spec.tool}"
    builds = Path(builds_root or ROOT / "research_data" / "tool_builds")
    builds.mkdir(parents=True, exist_ok=True)

    event_log.append(Event(
        actor=Actor.SYSTEM, action="tool_build_started", project_id=project_id,
        object_type="ToolBuildSpec", object_id=spec.tool,
        detail={"spec": str(spec_file), "max_repair_rounds": max_repair_rounds},
    ))

    base_prompt = TOOL_BUILD.format(
        tool_name=spec.tool, version=spec.version, purpose=spec.purpose,
        convention=spec.physics_convention,
        inputs_text="\n".join(f"  - {k}: {v}" for k, v in spec.inputs.items()),
        outputs_text="\n".join(f"  - {k}: {v}" for k, v in spec.outputs.items()),
        invariants="\n".join(f"  - {s}" for s in spec.invariants) or "（无）",
        limitations="\n".join(f"  - {s}" for s in spec.known_limitations) or "（无）",
        workspace="（每轮尝试的独立目录，见会话分配）",
    )

    # ---- 编码 + golden 自检循环（失败回喂修复，上限 max_repair_rounds）
    golden_report = None
    module_path: Path | None = None
    failures: list[str] = []
    attempt_no = 0
    for attempt_no in range(1, max_repair_rounds + 1):
        workspace = builds / spec.tool / f"attempt_{attempt_no}"
        workspace.mkdir(parents=True, exist_ok=True)
        prompt = base_prompt
        if failures:
            prompt += TOOL_BUILD_REPAIR.format(
                failures="\n".join(f"  - {f}" for f in failures)
            )
        prompt = prompt.replace("（每轮尝试的独立目录，见会话分配）", workspace.as_posix())
        if client_factory is not None:
            build_client = client_factory(workspace)
            try:
                build_client.run_agent(prompt, session_id=f"{project_id}:build:a{attempt_no - 1}")
            finally:
                build_client.close()
        else:
            client.run_agent(prompt, session_id=f"{project_id}:build:a{attempt_no - 1}")

        module_path = workspace / "tool_module.py"
        try:
            if not module_path.exists():
                raise FileNotFoundError("未找到交付文件 tool_module.py（必须在当前工作目录）")
            module = _load_candidate_module(
                module_path, f"qresearch_candidate_{spec.tool}_a{attempt_no}"
            )
            register(ToolSpec(
                name=candidate_name, version=spec.version, type=spec.type,
                description=f"[候选] {spec.purpose}", input_model=module.Inputs,
                run=module.run, applicable_domains=["quantum_many_body"],
                benchmark_refs=[Path(spec.fixtures_ref).name],
                known_limitations=list(spec.known_limitations),
            ), replace=True)
            from qresearch.verification.golden import run_benchmark

            golden_report = run_benchmark(spec.fixtures_ref, tool_override=candidate_name)
        except Exception as exc:  # noqa: BLE001 —— 交付缺陷回喂修复，不炸流程
            failures = [f"{type(exc).__name__}: {exc}"]
            golden_report = None
            event_log.append(Event(
                actor=Actor.SYSTEM, action="tool_build_attempt", project_id=project_id,
                object_type="ToolCandidate", object_id=candidate_name,
                detail={"attempt": attempt_no, "error": failures[0]},
            ))
            continue

        failures = [f"{r.case}: {r.detail}" for r in golden_report.results if not r.passed]
        event_log.append(Event(
            actor=Actor.SYSTEM, action="tool_build_attempt", project_id=project_id,
            object_type="ToolCandidate", object_id=candidate_name,
            detail={
                "attempt": attempt_no,
                "golden_passed": golden_report.all_passed,
                "n_checks": len(golden_report.results),
                "n_failed": len(failures),
            },
        ))
        if golden_report.all_passed:
            break

    if golden_report is None or not golden_report.all_passed:
        # 修复耗尽：不注册、不进批评环节，报告如实记录；清理候选注册避免污染词汇表
        unregister(candidate_name)
        report_path = _write_report(
            builds, spec, candidate_name, status="failed", attempts=attempt_no,
            verification_items=None, critic=None, limitations=spec.known_limitations,
        )
        return BuildResult(
            tool=spec.tool, candidate_name=candidate_name, status="failed",
            attempts=attempt_no, workspace=builds / spec.tool,
            report_path=report_path, golden_passed=False, failures=failures,
        )

    # ---- 三层验证（候选名 + 显式基准路径：fixture 尚未安装到 benchmarks/golden/）
    verifier = VerificationManager(storage, event_log)
    verification_items = verifier.verify_tool(
        candidate_name, extra_benchmarks=[ROOT / spec.fixtures_ref]
    )
    overall = _worst(verification_items)

    # ---- 批评者：diff vs Spec（读交付代码全文）
    critic = client.call_station(
        "tool_critic", project_id, CritiqueOutput,
        TOOL_CRITIC.format(
            spec_digest=spec.digest(), code=module_path.read_text(encoding="utf-8")
        ),
        event_log=event_log,
    )
    event_log.append(Event(
        actor=Actor.MODEL, action="tool_critic", project_id=project_id,
        object_type="ToolCandidate", object_id=candidate_name,
        detail={"verdict": critic.verdict,
                "issues": [i.model_dump() for i in critic.issues]},
    ))

    # ---- 构建报告 + 审批（人工触点：注册是受控词汇变更）
    registered = overall is VerificationStatus.PASSED and not any(
        i.severity == "blocker" for i in critic.issues
    )
    approved = auto_approve or _approve_interactive(spec.tool, overall, critic.verdict)
    event_log.append(Event(
        actor=Actor.SYSTEM if auto_approve else Actor.HUMAN,
        action="approve_tool" if approved else "reject_tool",
        project_id=project_id, object_type="ToolCandidate", object_id=candidate_name,
        detail={"note": "auto-approve（演示/测试用，非人工）" if auto_approve else ""},
    ))

    if approved and registered:
        final_spec = ToolSpec(
            name=spec.tool, version=spec.version, type=spec.type,
            description=spec.purpose, input_model=get_tool(candidate_name).input_model,
            run=get_tool(candidate_name).run,
            applicable_domains=["quantum_many_body"],
            benchmark_refs=[Path(spec.fixtures_ref).name],
            known_limitations=list(spec.known_limitations),
        )
        register(final_spec, replace=True)  # 允许覆盖同名 seed/旧版本（重建升级路径）
        unregister(candidate_name)
        if spec.fixtures_install_to:
            install_to = ROOT / spec.fixtures_install_to
            install_to.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / spec.fixtures_ref, install_to)
        from qresearch.core.models import ToolRecord

        storage.save(ToolRecord(**final_spec.to_tool_record_kwargs()))
        status = "registered"
    else:
        status = "rejected"

    report_path = _write_report(
        builds, spec, candidate_name, status=status, attempts=attempt_no,
        verification_items=verification_items, critic=critic,
        limitations=spec.known_limitations,
    )
    event_log.append(Event(
        actor=Actor.SYSTEM,
        action="tool_registered" if status == "registered" else "tool_build_rejected",
        project_id=project_id, object_type="Tool", object_id=spec.tool,
        detail={"status": status, "attempts": attempt_no,
                "verification_overall": overall.value, "critic": critic.verdict},
    ))
    return BuildResult(
        tool=spec.tool, candidate_name=candidate_name, status=status,
        attempts=attempt_no, workspace=builds / spec.tool, report_path=report_path,
        golden_passed=True, verification_overall=overall.value,
        critic_verdict=critic.verdict,
    )


# ---------------------------------------------------------------- 辅助
def _worst(items):
    statuses = [i.status for i in items] or [VerificationStatus.UNCERTAIN]
    if VerificationStatus.FAILED in statuses:
        return VerificationStatus.FAILED
    if VerificationStatus.UNCERTAIN in statuses:
        return VerificationStatus.UNCERTAIN
    return VerificationStatus.PASSED


def _approve_interactive(tool: str, overall, verdict: str) -> bool:
    print(f"\n[审批] 工具 {tool} 三层验证={overall.value}，批评者={verdict}。")
    return input("批准正式注册？[y/N] ").strip().lower() == "y"


def _write_report(builds, spec, candidate_name, *, status, attempts,
                  verification_items, critic, limitations) -> Path:
    golden_section = "逐轮结果见事件日志 tool_build_attempt（research_data/tool_builds/<tool>/attempt_N/）"
    if verification_items is not None:
        verification_section = "\n".join(
            f"- [{i.layer}] {i.status.value}：{i.detail}" for i in verification_items
        )
    else:
        verification_section = "- 未进入三层验证（golden 未通过）"
    critic_section = (
        f"verdict={critic.verdict}；issues："
        + "; ".join(f"[{i.severity}] {i.description}" for i in critic.issues)
        if critic is not None else "未进行（golden 未通过）"
    )
    text = REPORT.format(
        tool_name=spec.tool, purpose=spec.purpose, status=status, attempts=attempts,
        candidate_name=candidate_name,
        registered_name=spec.tool if status == "registered" else "—",
        golden_section=golden_section, verification_section=verification_section,
        critic_section=critic_section,
        limitations="\n".join(f"- {s}" for s in limitations) or "- 无",
    )
    report_path = builds / spec.tool / "build_report.md"
    report_path.write_text(text, encoding="utf-8")
    return report_path
