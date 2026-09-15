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
import uuid
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
    reuse_attempt: int | None = None,
) -> BuildResult:
    """client_factory：live 编码用——每轮 attempt 以 cwd=workspace 新建 runtime，
    保证 agent 的工作目录就是交付目录（对单 client 依赖 prompt 里的绝对路径不可靠：
    Windows 反斜杠路径易被误读为相对路径）。离线脚本测试用注入 client 即可。

    reuse_attempt：断点续构。设为 N 时，若 ``attempt_N/tool_module.py`` 已存在则
    **跳过该轮的编码站调用**，直接复用交付物进入 golden → 验证 → 批评者 → 人工审批。
    用于「编码站已写好代码、但站点调用未干净返回导致 build_tool 中断」的情形
    （见本模块顶部与本参数的实现注释）。为 None 时行为与原先完全一致。
    """
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
    # 运行期随机前缀：DSH 的 session 是**持久化**的，同一个 id 第二次创建会直接抛
    # JsonRpcError("session ... already exists")。原先 run_agent 的 session 名只含
    # {project_id}:build:a{n}，于是**第二次构建同一个工具必然在 2 秒内失败**
    # （实测 2026-09-15：重跑 hubbard_ed 时 a0 冲突，编码站调用当场报错）。
    # call_station 早就有实例级 nonce（见 dsh_client 的 _session_nonce），
    # run_agent 这条路径漏了——补上，使每次构建都是全新会话。
    run_nonce = uuid.uuid4().hex[:8]
    for attempt_no in range(1, max_repair_rounds + 1):
        workspace = builds / spec.tool / f"attempt_{attempt_no}"
        workspace.mkdir(parents=True, exist_ok=True)
        prompt = base_prompt
        if failures:
            prompt += TOOL_BUILD_REPAIR.format(
                failures="\n".join(f"  - {f}" for f in failures)
            )
        prompt = prompt.replace("（每轮尝试的独立目录，见会话分配）", workspace.as_posix())
        station_error: str | None = None
        if reuse_attempt is not None and attempt_no == reuse_attempt and (
            workspace / "tool_module.py"
        ).exists():
            # 断点续构：编码站已经产出交付物，但**站点调用没有干净返回**（本平台已记录
            # 的 runtime 断链：站点看门狗耗尽后抛错，异常穿透 build_tool，于是交付物
            # 留在 attempt_N/ 而 tool_build_attempt 事件从未落账）。此时重跑编码站既
            # 费时又可能再撞同一缺陷，而交付物已存在——直接复用它，后续所有环节
            # （加载 → golden → 三层验证 → 批评者 → 人工审批 → 注册 → 安装 fixtures）
            # 走的是**完全相同**的代码路径，因此注册结果与一次跑通无差别。
            event_log.append(Event(
                actor=Actor.SYSTEM, action="tool_build_reuse_attempt", project_id=project_id,
                object_type="ToolCandidate", object_id=candidate_name,
                detail={"attempt": attempt_no,
                        "module": str(workspace / "tool_module.py"),
                        "reason": "复用编码站已产出的交付物，跳过编码站调用"},
            ))
            print(f"[续构] attempt_{attempt_no} 已有 tool_module.py，跳过编码站，"
                  f"直接进入 golden/验证/批评者/审批")
        else:
            # 编码站调用。**站点故障不得吞掉已产出的交付物**：本平台已记录 runtime
            # 断链（看门狗耗尽后 StationTimeout/StationRuntimeError），而异常穿透
            # build_tool 会让整次构建崩掉——实测两次 hubbard 构建就是这么丢的：
            # 编码站其实已经把 tool_module.py 写进 workspace，但事件流里连
            # tool_build_attempt 都没有，交付物从此无人问津、白跑一遍。
            # 这里降级为"记一笔、继续走下游"：交付物存在就照常评测（坏的地方由
            # golden 抓出来回喂下一轮），只有连交付物都没有才算这一轮真的失败。
            try:
                if client_factory is not None:
                    build_client = client_factory(workspace)
                    try:
                        build_client.run_agent(
                            prompt,
                            session_id=f"{project_id}:build:{run_nonce}:a{attempt_no - 1}")
                    finally:
                        build_client.close()
                else:
                    client.run_agent(
                        prompt,
                        session_id=f"{project_id}:build:{run_nonce}:a{attempt_no - 1}")
            except Exception as exc:  # noqa: BLE001 —— 站点故障按可恢复处理
                station_error = f"{type(exc).__name__}: {exc}"
                module_exists = (workspace / "tool_module.py").exists()
                event_log.append(Event(
                    actor=Actor.SYSTEM, action="tool_build_station_failed",
                    project_id=project_id, object_type="ToolCandidate",
                    object_id=candidate_name,
                    detail={"attempt": attempt_no, "phase": "coding_station",
                            "error": station_error, "module_exists": module_exists},
                ))
                print(f"[警告] attempt_{attempt_no} 编码站调用失败：{station_error}")
                print(f"        交付物是否存在：{module_exists}"
                      f"{' —— 继续评测已产出的交付物' if module_exists else ' —— 本轮真的失败'}")

        module_path = workspace / "tool_module.py"
        if station_error is not None and not module_path.exists():
            failures = [f"编码站调用失败且未产出 tool_module.py：{station_error}"]
            golden_report = None
            continue
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
    # 批评者是注册的**必要条件**（计划 v2 §8 的三道关之一：流程 + 批评者 + 人工批准），
    # 所以它失败时既不能注册、也不该弹审批。但同样不该让站点故障吞掉整次构建：
    # golden 与三层验证的成果要保住，人可带 --resume 只补跑这一关。
    # 实测该站点在本机冷 runtime 上第一次调用必挂 900s 超时，重试才成。
    critic = None
    try:
        critic = client.call_station(
            "tool_critic", project_id, CritiqueOutput,
            TOOL_CRITIC.format(
                spec_digest=spec.digest(), code=module_path.read_text(encoding="utf-8")
            ),
            event_log=event_log,
        )
    except Exception as exc:  # noqa: BLE001 —— 站点故障按"批评者缺席"处理
        event_log.append(Event(
            actor=Actor.SYSTEM, action="tool_critic_failed", project_id=project_id,
            object_type="ToolCandidate", object_id=candidate_name,
            detail={"phase": "critic_station", "error": f"{type(exc).__name__}: {exc}"},
        ))
        print(f"[警告] 批评者站点调用失败：{type(exc).__name__}: {exc}")

    if critic is not None:
        event_log.append(Event(
            actor=Actor.MODEL, action="tool_critic", project_id=project_id,
            object_type="ToolCandidate", object_id=candidate_name,
            detail={"verdict": critic.verdict,
                    "issues": [i.model_dump() for i in critic.issues]},
        ))

    # ---- 构建报告 + 审批（人工触点：注册是受控词汇变更）
    # 批评者缺席 → 三道关缺一不可 → 一律不注册，也不进入审批（避免产生一条
    # 语义含糊的 approve/reject 事件）。
    registered = (
        overall is VerificationStatus.PASSED
        and critic is not None
        and not any(i.severity == "blocker" for i in critic.issues)
    )
    if critic is None:
        approved = False
        event_log.append(Event(
            actor=Actor.SYSTEM, action="tool_build_aborted_no_critic",
            project_id=project_id, object_type="ToolCandidate", object_id=candidate_name,
            detail={"reason": "批评者站点故障；三道关缺一不可，不予注册。"
                              "可带 reuse_attempt 重跑以只补这一关。"},
        ))
        print("[中止] 批评者未给出裁决（站点故障）→ 不予注册；"
              "交付物与验证结果已保留，可带 --resume 重跑只补这一关。")
    else:
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
        # 未注册即清理候选名：候选名是构建期的临时词汇，残留会污染工具词汇表
        # （后续会话看到 candidate_* 会以为是可用工具）。原实现只在"修复耗尽"
        # 分支清理，凡是走到批评者/审批之后被拒的路径都会漏掉这一条。
        unregister(candidate_name)

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
                "verification_overall": overall.value,
                "critic": critic.verdict if critic is not None else None,
                "critic_absent": critic is None},
    ))
    return BuildResult(
        tool=spec.tool, candidate_name=candidate_name, status=status,
        attempts=attempt_no, workspace=builds / spec.tool, report_path=report_path,
        golden_passed=True, verification_overall=overall.value,
        critic_verdict=critic.verdict if critic is not None else None,
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
        if critic is not None
        else ("未进行（批评者站点故障；golden 与三层验证已通过，因三道关缺一不予注册）"
              if verification_items is not None
              else "未进行（golden 未通过）")
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
