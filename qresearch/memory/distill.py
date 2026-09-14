"""从项目台账蒸馏经验（计划 v2 §7.9）。

铁律的延续：蒸馏是确定性代码提取（全部事实来自 storage 里的对象），
不经 LLM——记忆库里的每一条都能回放到源实验/决策/报告。
"""
from __future__ import annotations

from qresearch.core.models import (
    Decision, Experiment, Evidence, Goal, Hypothesis, ResearchPlan, ToolRecord,
    VerificationReport,
)
from qresearch.core.status import ExperimentStatus, VerificationStatus
from qresearch.core.storage import Storage
from .store import MemoryEntry, MemoryLayer

_METHOD_LIMIT = 6  # 方法层每项目最多条数（防蒸馏膨胀）


def _claim_tags(*texts: str, extra: list[str] | None = None) -> list[str]:
    """条目标签：输入文本中的拉丁词（≥3 字符，小写去重）+ 显式 extra。"""
    import re

    tags: list[str] = list(extra or [])
    for t in texts:
        tags.extend(w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", t or ""))
    seen: set[str] = set()
    return [t for t in tags if not (t in seen or seen.add(t))][:12]


def distill_project(storage: Storage, project_id: str) -> list[MemoryEntry]:
    """从已完成（或转人工）的项目台账提取四层经验条目。"""
    goals = storage.list(Goal, project_id=project_id)
    goal = goals[0] if goals else None
    hypotheses = storage.list(Hypothesis, project_id=project_id)
    experiments = storage.list(Experiment, project_id=project_id)
    evidence = storage.list(Evidence, project_id=project_id)
    decisions = storage.list(Decision, project_id=project_id)
    plans = storage.list(ResearchPlan, project_id=project_id)
    reports = storage.list(VerificationReport, project_id=project_id)
    report_by_exp = {r.experiment_id: r for r in reports}
    # tool_id 是 ToolRecord id：解析为工具名（人类可读、检索友好）
    tool_name = {t.tool_id: t.name for t in storage.list(ToolRecord)}
    question = goal.question if goal else project_id
    # 问题域标签：让本项目全部经验（尤其失败案例）可被相似问题检索到
    area_tags = _claim_tags(question, extra=[*goal.quantities] if goal else [])[:6]

    entries: list[MemoryEntry] = []
    ref = {"project_id": project_id}

    # ---- 项目层：问题 + 假设命运 + 决策轨迹（一条）------------------------
    fates = []
    for h in hypotheses:
        fate = "支持" if h.supporting_evidence else ("反驳" if h.contradicting_evidence else "未判定")
        fates.append(f"{h.statement[:60]}…→{fate}" if len(h.statement) > 60
                     else f"{h.statement}→{fate}")
    decision_path = " → ".join(d.recommendation.value for d in decisions) or "（无决策）"
    versions = sorted(p.version for p in plans)
    entries.append(MemoryEntry(
        layer=MemoryLayer.project,
        title=f"项目经验：{question[:60]}",
        content=(
            f"研究问题：{question}\n"
            f"假设与命运：{'；'.join(fates) or '（无）'}\n"
            f"决策轨迹：{decision_path}；计划版本 {versions or '无'}"
        ),
        tags=_claim_tags(question, extra=[*area_tags, "project"]),
        refs=ref,
        source_project=project_id,
    ))

    # ---- 方法层：被验证支撑的证据主张（可复用的分析/对照手段）-------------
    for ev in evidence[:_METHOD_LIMIT]:
        exp = next((e for e in experiments if e.experiment_id == ev.source_experiment), None)
        tool = tool_name.get(exp.tool_id, exp.tool_id) if exp else "?"
        entries.append(MemoryEntry(
            layer=MemoryLayer.method,
            title=f"方法经验：{ev.claim[:60]}",
            content=f"{ev.claim}（支撑实验 {ev.source_experiment or '—'}，工具 {tool}）",
            tags=_claim_tags(ev.claim, extra=[tool, *area_tags] if tool and tool != "?" else area_tags),
            refs={**ref, "evidence_id": ev.evidence_id},
            source_project=project_id,
        ))

    # ---- 工具层：按工具聚合实战表现 ---------------------------------------
    by_tool: dict[str, list[Experiment]] = {}
    for e in experiments:
        by_tool.setdefault(tool_name.get(e.tool_id, e.tool_id) or "?", []).append(e)
    for tool, exps in sorted(by_tool.items()):
        failed = [e for e in exps if e.status is ExperimentStatus.FAILED]
        verified_bad = [
            e for e in exps
            if report_by_exp.get(e.experiment_id)
            and report_by_exp[e.experiment_id].overall is not VerificationStatus.PASSED
        ]
        entries.append(MemoryEntry(
            layer=MemoryLayer.tool,
            title=f"工具经验：{tool}（{len(exps)} 次使用）",
            content=(
                f"使用 {len(exps)} 次，失败 {len(failed)} 次，"
                f"验证未通过 {len(verified_bad)} 次。"
                + (f"典型失败：{failed[0].error or failed[0].parameters}" if failed else "")
            ),
            tags=[tool, *area_tags],
            refs=ref,
            source_project=project_id,
        ))

    # ---- 失败案例层：失败实验 + 验证未通过（DECIDE/PLAN 的固定输入）--------
    for e in experiments:
        tname = tool_name.get(e.tool_id, e.tool_id)
        if e.status is ExperimentStatus.FAILED:
            entries.append(MemoryEntry(
                layer=MemoryLayer.failure,
                title=f"失败案例：{e.step_id}（工具 {tname or '未注册'}）",
                content=(
                    f"输入 {e.parameters}；错误：{e.error or '未知'}。"
                    "教训：该配置/工具选择无信息增益，新计划应避开或说明差异。"
                ),
                tags=_claim_tags(str(e.parameters), str(e.error),
                                 extra=[tname, *area_tags] if tname else area_tags),
                refs={**ref, "experiment_id": e.experiment_id},
                source_project=project_id,
            ))
        elif e.experiment_id in report_by_exp:
            vr = report_by_exp[e.experiment_id]
            if vr.overall is not VerificationStatus.PASSED:
                reasons = "；".join(
                    f"[{i.layer}] {i.claim}" for i in vr.items if i.status is not VerificationStatus.PASSED
                ) or vr.overall.value
                entries.append(MemoryEntry(
                    layer=MemoryLayer.failure,
                    title=f"无信息增益：{e.step_id}（验证 {vr.overall.value}）",
                    content=(
                        f"实验完成但验证 {vr.overall.value}，不得引用为证据。原因：{reasons}。"
                        "教训：该产出不能支撑结论，新计划应修正方法后重做。"
                    ),
                    tags=_claim_tags(reasons, extra=[tname, *area_tags] if tname else area_tags),
                    refs={**ref, "experiment_id": e.experiment_id, "report_id": vr.report_id},
                    source_project=project_id,
                ))
    return entries
