"""ResearchEngine：唯一科研入口（计划 v3 M1）。

所有方法确定性记账；LLM 只在站点执行器内部被"提议"（铁律：LLM 只提议代码记账）。
谁在驱动变得可替换：壳 B（run_research_loop / run_projects）、MCP 层（M2）、未来的
壳都驱动同一内核。

边界（评审修订 A3）：全部方法**同步**——异步 job 协议是 MCP 层薄封装
（experiments_run_async / job_* 委托 qresearch/jobs.py），壳 B 不经过它，
事件时序与本文件重构前严格一致（tests/baselines/ 归一化回归门）。

唯一真相源：一切状态在 Storage(SQLite) + EventLog(JSONL)；引擎实例的 _LoopState
只是跨轮累计的进程内载体（fresh/resume 共用），可随时从台账重建（resume_state）。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from qresearch import __version__
from qresearch.core.budget import Budget
from qresearch.core.events import Event, EventLog
from qresearch.core.models import (
    Decision, Evidence, Experiment, Goal, Hypothesis, Project, ResearchPlan, ToolRecord,
    VerificationReport,
)
from qresearch.core.status import (Actor, ApprovalChannel, ExperimentStatus,
                                    PlanStatus, VerificationStatus)
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient
from .jobs import JobManager
from .stations.executors import analyze as _analyze_station
from .stations.executors import decide as _decide_station
from .stations.executors import hypothesize as _hypothesize_station
from .stations.executors import plan_with_critic, understand as _understand_station

# 台账对象类型 → ledger() 的 kind 词汇（tool 为跨项目全局，其余按 project_id）
_LEDGER_KINDS: dict[str, type] = {
    "project": Project, "goal": Goal, "hypothesis": Hypothesis,
    "plan": ResearchPlan, "experiment": Experiment, "evidence": Evidence,
    "decision": Decision, "tool": ToolRecord, "verification": VerificationReport,
}


# ================================================================ 跨轮状态
class _LoopState:
    """跨轮累计状态（fresh 与 resume 共用的可变载体）。真相在台账；本对象可重建。"""

    def __init__(self) -> None:
        self.goal: Goal | None = None
        self.hypotheses: list = []
        self.previous_plan: ResearchPlan | None = None
        self.last_decision_id: str | None = None
        self.all_decisions: list = []
        self.all_analyses: list = []
        self.all_rows: list[dict] = []
        self.all_evidence: list = []
        self.tool_by_step: dict[str, str] = {}
        self.user_notes: str = ""  # 轮末回调收集的用户修改意见（下一轮计划注入）


# ================================================================ 报告生成
def _hypothesis_fate_line(h) -> str:
    fate = ("支持" if h.supporting_evidence
            else ("反驳" if h.contradicting_evidence else "未判定"))
    return f"- {h.statement} → **{fate}**\n  - 证伪试验：{'；'.join(h.falsification_tests)}"


#: 各审批通道的保真度说明（写进报告，供读的人折价）。
_CHANNEL_FIDELITY = {
    "tty": "进程持有控制终端——agent 起的子进程结构性拿不到，保真度最高",
    "webui-local": "同机 localhost 浏览器点击——凭据落在 agent 同用户可读的信任域内，"
                   "本地进程理论上可自行完成同一请求，**保真度较低**",
    "system-auto": "system 代行（auto_approve 演示模式）——**非人工**",
    "未记录": "本字段引入前的历史事件，通道未记录——**不得反推为 tty**",
}


def _conclude_channel_line(channel: str) -> str:
    note = _CHANNEL_FIDELITY.get(channel, "未知通道")
    return f"- **结论确认通道**：`{channel}`（{note}）"


def _summary_section(project: Project, status: str, hypotheses: list,
                     decisions: list, experiment_rows: list[dict],
                     all_evidence: list,
                     conclude_channel: str | None = None) -> list[str]:
    """研究总结（置顶）：最终状态 / 结论 / 关键证据 / 假设命运 / 规模统计。

    全部内容确定性来自台账（铁律：LLM 只提议，代码记账）——结论正文取自
    收束决策的 rationale 原文，证据逐条挂 id 可回放。

    conclude_channel：结论确认的取得通道（None = 尚未确认）。渲染成独立一行，
    使"这次批准经由较低保真度通道"对读报告的人可见，而不是被 actor=HUMAN 抹平。
    """
    rec_chain = " → ".join(d.recommendation.value for d in decisions) or "（无决策）"
    lines = ["## 研究总结", "", f"- **最终状态**：`{status}`（{len(decisions)} 轮；"
             f"决策链 {rec_chain}）"]
    if conclude_channel is not None:
        lines.append(_conclude_channel_line(conclude_channel))
    final = decisions[-1] if decisions else None
    concluded = final is not None and (
        final.type.value == "declare_result"
        or final.recommendation.value in ("terminate", "accept"))
    if concluded and final is not None:
        lines.append(f"- **结论**（收束决策 rationale 原文，"
                     f"{final.decision_id}）：{final.rationale or '（未填写）'}")
        passed = [c for c in final.checklist if c.status == "passed" and c.evidence]
        if passed:
            lines.append("- **支撑结论的关键证据**：")
            lines += [f"  - {c.claim}（evidence: `{c.evidence}`）" for c in passed]
        lines.append("- **提示**：收束决策 requires_human=是——结论确认权在人。")
    else:
        lines.append("- **结论**：（本轮运行未给出最终结论——"
                     "以 iterate/replan 收尾或中途转出；见决策记录与局限）")
    if hypotheses:
        lines.append("- **假设命运**：")
        lines += [f"  { _hypothesis_fate_line(h) }" for h in hypotheses]
    n_done = sum(1 for r in experiment_rows if r["status"] == "completed")
    n_fail = sum(1 for r in experiment_rows if r["status"] == "failed")
    tools = sorted({r["tool"] for r in experiment_rows}) or ["—"]
    lines.append(
        f"- **规模**：实验 {len(experiment_rows)} 个（完成 {n_done} / 失败 {n_fail}，"
        f"工具：{'、'.join(tools)}），证据 {len(all_evidence)} 条")
    lines.append("")
    return lines


def _plan_history_section(plans: list, plan_notes: dict | None) -> list[str]:
    """计划版本历史：每版的审查结论、阻塞项、步骤与修订说明（从台账/事件重建）。"""
    lines = ["## 计划版本历史"]
    for p in plans:
        notes = (plan_notes or {}).get(p.plan_id, {})
        verdict = notes.get("verdict", "—")
        issues = notes.get("issues", [])
        lines.append("")
        lines.append(f"### 计划 v{p.version}（{p.status.value}，{len(p.steps)} 步）")
        if p.based_on_decision:
            lines.append(f"- 依据决策：`{p.based_on_decision}`（replan/修订）")
        if p.diff_summary:
            lines.append(f"- 与上一版差异：{p.diff_summary}")
        blockers = [i for i in issues if isinstance(i, dict) and i.get("severity") == "blocker"]
        lines.append(f"- critic 审查：{verdict}"
                     + (f"（blocker {len(blockers)} 项）" if blockers else ""))
        for i in issues:
            if isinstance(i, dict):
                sev = i.get("severity", "?")
                mark = "⛔" if sev == "blocker" else "·"
                lines.append(f"  - {mark} [{sev}] {i.get('step_id') or '整体'}："
                             f"{i.get('description', '')[:200]}")
        for s in p.steps:
            flag = " [需审批]" if getattr(s, "requires_approval", False) else ""
            lines.append(f"- `{s.step_id}` [{s.action}]{flag} {s.purpose}"
                         f"（工具：{'、'.join(s.tools) or '—'}）")
    lines.append("")
    return lines


def generate_report(
    project: Project, goal, hypotheses: list, plans: list,
    experiment_rows: list[dict], decisions: list, analyses: list,
    *,
    status: str = "terminated",
    plan_notes: dict | None = None,
    all_evidence: list | None = None,
    conclude_channel: str | None = None,
) -> str:
    """生成 markdown 研究报告：置顶研究总结 + 计划版本历史；结论全部可追溯。"""
    all_evidence = all_evidence or []
    lines = [
        f"# 研究报告：{project.title}",
        "",
        f"- 项目：`{project.project_id}`",
        f"- 研究问题：{goal.question if goal else project.question}",
        f"- 目标量：{'、'.join(goal.quantities) if goal else '—'}",
        "",
    ]
    lines += _summary_section(project, status, hypotheses, decisions,
                              experiment_rows, all_evidence,
                              conclude_channel=conclude_channel)
    lines += [
        "## 假设",
        *[f"- {h.statement}\n"
          f"  - 证伪：{'；'.join(h.falsification_tests)}" for h in hypotheses],
        "",
    ]
    lines += _plan_history_section(plans, plan_notes)
    lines += [
        "## 实验（三层验证后）",
        "| 实验 | 步骤 | 工具 | 状态 | 关键结果 |",
        "|---|---|---|---|---|",
        *[
            f"| {r['experiment_id']} | {r['step_id']} | {r['tool']} | {r['status']} | "
            f"{'; '.join(f'{k}={v}' for k, v in r['key_results'].items())} |"
            for r in experiment_rows
        ],
        "",
        "## 分析结论（每条挂实验 id）",
    ]
    for a in analyses:
        for c in a.observations:
            lines.append(f"- 观察：{c.claim}（{', '.join(c.experiment_ids)}）")
        for c in a.interpretations:
            lines.append(f"- 解读：{c.claim}（{', '.join(c.experiment_ids)}）")
        if a.uncertainties:
            lines.append(f"- 不确定：{'；'.join(a.uncertainties)}")
    lines += ["", "## 决策记录"]
    for d in decisions:
        lines.append(
            f"- [{d.type.value}] 建议 {d.recommendation.value}"
            f"（checklist {len(d.checklist)} 项，人工确认={'是' if d.requires_human else '否'}）："
            f"{d.rationale or ''}"
        )
    lines += [
        "",
        "## 局限与不确定性",
        "- 见各轮分析 uncertainties；本报告由确定性骨架自动生成，"
        "对外发布前需人工复核（declare_result 决策无条件人工确认）。",
    ]
    return "\n".join(lines)


def _experiment_rows(experiments: list, eligible_ids: set[str],
                     tool_for) -> tuple[list[dict], list[dict]]:
    """实验分为"可引用（验证通过）"与"全部"（计划 v2 §7.6 证据资格门）。

    tool_for：step_id → 工具名（含 parameter_scan 子实验的父步骤回溯）。
    """
    from qresearch.experiments.manager import _load_result

    eligible, all_rows = [], []
    for exp in experiments:
        result = _load_result(exp) or {}
        row = {
            "experiment_id": exp.experiment_id, "step_id": exp.step_id,
            "tool": tool_for(exp.step_id) or "?", "inputs": exp.parameters,
            "key_results": {
                k: result.get(k)
                for k in ("E0", "e0", "gap", "Sz2", "method") if k in result
            },
            "status": exp.status.value,
        }
        all_rows.append(row)
        if exp.experiment_id in eligible_ids and exp.status.value == "completed":
            eligible.append(row)
    return eligible, all_rows


def _default_experiments_root(storage: Storage) -> Path:
    return Path(storage.path).parent / "experiments"


# ================================================================ 引擎
class ResearchEngine:
    """唯一科研入口。所有方法确定性记账；LLM 只在这些方法内部被"提议"。"""

    def __init__(
        self,
        storage: Storage,
        event_log: EventLog,
        client: DSHClient,
        *,
        experiments_root: str | Path | None = None,
        memory_store=None,
        budget: Budget | None = None,
        retries: int = 1,
        runner=None,
    ) -> None:
        from qresearch.experiments.manager import ExperimentManager
        from qresearch.verification.manager import VerificationManager

        self.storage = storage
        self.event_log = event_log
        self.client = client
        self.memory_store = memory_store
        self.budget = budget
        self.retries = retries
        self.manager = ExperimentManager(
            storage, event_log, runner=runner,
            experiments_root=experiments_root or _default_experiments_root(storage))
        self.verifier = VerificationManager(storage, event_log)
        self.jobs = JobManager()  # 仅 MCP 层用（A3）；壳 B 不经过
        self._states: dict[str, _LoopState] = {}

    # -------------------------------------------------- 状态载体
    def state(self, project_id: str) -> _LoopState:
        st = self._states.get(project_id)
        if st is None:
            st = self._states[project_id] = _LoopState()
        return st

    def _question(self, project_id: str) -> str:
        project = self.storage.get(Project, project_id)
        if project is None:
            raise KeyError(f"项目不存在: {project_id}")
        return project.question

    # -------------------------------------------------- 项目与规划
    def open_project(self, project_id: str, question: str) -> Project:
        project = Project(project_id=project_id, title=question[:40], question=question)
        self.storage.save(project)
        self.event_log.append(Event(actor=Actor.SYSTEM, action="create_project",
                                    project_id=project_id, object_type="Project",
                                    object_id=project_id, detail={}))
        return project

    def understand(self, project_id: str) -> Goal:
        st = self.state(project_id)
        st.goal = _understand_station(self.client, project_id, self._question(project_id),
                                      event_log=self.event_log, retries=self.retries)
        self.storage.save(st.goal)
        self.event_log.append(Event(actor=Actor.SYSTEM, action="save",
                                    project_id=project_id, object_type="Goal",
                                    object_id=st.goal.goal_id, detail={}))
        return st.goal

    def hypothesize(self, project_id: str, n: int = 3) -> list:
        st = self.state(project_id)
        if st.goal is None:
            goals = self.storage.list(Goal, project_id=project_id)
            st.goal = goals[0] if goals else None
        st.hypotheses = _hypothesize_station(
            self.client, project_id, st.goal, n=n,
            event_log=self.event_log, retries=self.retries)
        for h in st.hypotheses:
            self.storage.save(h)
        return st.hypotheses

    def plan_create(
        self, project_id: str, *, user_notes: str | None = None,
        previous_plan_id: str | None = None, decision_id: str | None = None,
        round_no: int | None = None, ready_detail_extra: dict | None = None,
    ) -> tuple[ResearchPlan, object]:
        """制定计划并过 critic（站点调用）；落账 AWAITING_APPROVAL + plan_ready。

        user_notes：研究者修改意见（消费后清空——意见只注入一次，与轮末回调语义一致）。
        previous_plan_id/decision_id：缺省用状态载体的 previous_plan/last_decision_id
        （轮内驱动路径）；显式传 None 且 previous_plan_id=None 时为全新计划（MCP 首版）。
        round_no：轮内驱动传当前轮号（plan_ready 事件带 round）；MCP 路径传 None。
        """
        st = self.state(project_id)
        if user_notes is None:
            user_notes = st.user_notes
        if previous_plan_id is not None:
            previous = self.storage.get(ResearchPlan, previous_plan_id)
            if previous is None:
                raise KeyError(f"基准计划不存在: {previous_plan_id}")
        else:
            previous = st.previous_plan
        plan, critique = plan_with_critic(
            self.client, project_id, st.goal, st.hypotheses,
            previous=previous, decision_id=decision_id,
            memory_text=self._memory_digest_text(project_id, st),
            user_notes=user_notes, event_log=self.event_log, retries=self.retries)
        st.user_notes = ""  # 意见只注入一次
        plan.status = PlanStatus.AWAITING_APPROVAL
        self.storage.save(plan)
        detail: dict = {"version": plan.version, "verdict": critique.verdict}
        if round_no is not None:
            detail["round"] = round_no
        detail["issues"] = [i.model_dump() for i in critique.issues]
        if ready_detail_extra:
            detail.update(ready_detail_extra)
        self.event_log.append(Event(actor=Actor.SYSTEM, action="plan_ready",
                                    project_id=project_id, object_type="ResearchPlan",
                                    object_id=plan.plan_id, detail=detail))
        return plan, critique

    def plan_approve(self, plan_id: str, *, actor: Actor, note: str = "",
                     channel: ApprovalChannel = ApprovalChannel.UNSPECIFIED,
                     ) -> ResearchPlan:
        """批准计划（写 approve 事件）。actor 由调用方负责——人工审批的强制入口
        在 CLI 审批台（A1，M2 起）；MCP 层只查台账不写 actor=HUMAN。
        channel 记录审批取得通道（保真度审计）：人工通道必须由 approvals/approvals-web
        显式传入，MCP 侧不得声明 TTY。"""
        from .loop import approve_plan  # 延迟导入：loop.py 反向依赖引擎入口

        plan = self._require(plan_id, ResearchPlan)
        approve_plan(self.storage, self.event_log, plan, actor=actor, note=note,
                     channel=channel)
        return self.storage.get(ResearchPlan, plan_id)

    def plan_reject(self, plan_id: str, *, actor: Actor, note: str = "",
                    channel: ApprovalChannel = ApprovalChannel.UNSPECIFIED,
                    ) -> ResearchPlan:
        from .loop import reject_plan

        plan = self._require(plan_id, ResearchPlan)
        reject_plan(self.storage, self.event_log, plan, actor=actor, note=note,
                    channel=channel)
        return self.storage.get(ResearchPlan, plan_id)

    # -------------------------------------------------- 执行与验证
    def experiments_run(self, plan_id: str, *, max_workers: int = 1,
                        cancel_check: Callable[[], bool] | None = None) -> dict:
        """执行已批准计划的全部可执行步骤（同步；实验不经 LLM）。

        返回 {plan_id, project_id, experiment_ids, n_completed, n_failed, experiments}。
        cancel_check：协作式取消检查点（MCP 层 job_cancel 用——取消后不再新建
        实验账目，已建账的照常完成）。缺省 None，行为与 M1 严格一致。
        MCP 层经 experiments_run_async 包装为 job_id 协议（A2/A3）。
        """
        plan = self._require(plan_id, ResearchPlan)
        st = self.state(plan.project_id)
        for s in plan.steps:
            for t in s.tools:
                st.tool_by_step.setdefault(s.step_id, t)
        experiments = self.manager.execute_plan(
            plan, max_workers=max_workers, cancel_check=cancel_check)
        return {
            "plan_id": plan_id, "project_id": plan.project_id,
            "experiment_ids": [e.experiment_id for e in experiments],
            "n_completed": sum(1 for e in experiments
                               if e.status == ExperimentStatus.COMPLETED),
            "n_failed": sum(1 for e in experiments if e.status == ExperimentStatus.FAILED),
            "experiments": experiments,
        }

    def verify(self, experiment_ids: list[str]) -> list:
        """三层验证（证据资格门在 VerificationManager）：无工具映射的实验跳过，
        与轮内驱动行为一致（参数扫描子实验经父步骤回溯）。"""
        reports = []
        for eid in experiment_ids:
            exp = self._require(eid, Experiment)
            tool = self._tool_for(exp.project_id, exp.step_id)
            if tool is None:
                continue
            reports.append(self.verifier.verify_experiment(exp, tool))
        return reports

    def analyze(self, project_id: str, *, experiment_ids: list[str],
                eligible_ids: set[str] | None = None) -> tuple:
        """ANALYZE 站点（只允许引用通过验证的实验——资格门在代码层）。

        eligible_ids 缺省时按 verification_status==PASSED 过滤（MCP 路径）；
        轮内驱动显式传入本轮验证结果（行为与重构前逐字一致）。
        返回 (AnalyzeOutput, evidence 列表)；Evidence 已落账。
        """
        st = self.state(project_id)
        experiments = [self._require(eid, Experiment) for eid in experiment_ids]
        if eligible_ids is None:
            eligible_ids = {e.experiment_id for e in experiments
                            if e.verification_status == VerificationStatus.PASSED}
        eligible, rows = _experiment_rows(experiments, eligible_ids,
                                          lambda sid: self._tool_for(project_id, sid))
        st.all_rows.extend(rows)
        analysis, evidence_list = _analyze_station(
            self.client, project_id, st.goal, st.hypotheses, eligible,
            event_log=self.event_log, retries=self.retries)
        for ev in evidence_list:
            self.storage.save(ev)
        st.all_evidence.extend(evidence_list)
        st.all_analyses.append(analysis)
        return analysis, evidence_list

    def decide(self, project_id: str, *, round_no: int, max_rounds: int) -> Decision:
        """DECIDE 站点 + 预算闸（末轮 iterate 不被自动执行，转人工）。"""
        st = self.state(project_id)
        if not st.all_analyses:
            raise ValueError(f"项目 {project_id} 尚无分析结果，不能决策")
        decision = _decide_station(
            self.client, project_id, st.goal, st.hypotheses, st.all_analyses[-1],
            st.all_evidence, round_no=round_no, max_rounds=max_rounds,
            memory_text=self._memory_digest_text(project_id, st),
            event_log=self.event_log, retries=self.retries)
        if round_no == max_rounds and decision.recommendation.value == "iterate":
            decision.rationale = (
                f"[预算闸] 已达 {max_rounds} 轮上限，iterate 不被自动执行，转人工。"
                f"原 rationale：{decision.rationale}"
            )
            decision.requires_human = True
        self.storage.save(decision)
        st.all_decisions.append(decision)
        st.last_decision_id = decision.decision_id
        return decision

    # -------------------------------------------------- adhoc（D3 / A6）
    def adhoc_record(self, project_id: str, *, kind: str, summary: str,
                     tool: str | None = None, parameters: dict | None = None,
                     artifacts: list[str] | None = None) -> Experiment:
        """登记一个"计划外已发生"的动作（bash 现算、临时查表等）。

        诚实边界（D3/A6）：登记即标记未验证（verification_status=NOT_RUN），
        不产生任何证据资格；report/decide 的资格门拒绝引用它。只有 tool 为
        **注册工具**且结果可复跑、golden 过关（adhoc_verify）才能升级为证据——
        bash 现算数值永远无法验证，这是特性不是缺陷。
        kind 会成为 step_id 前缀（adhoc_<kind>）；summary 记入 parameters._adhoc。
        """
        from qresearch.tools.registry import get_tool

        tool_id = ""
        if tool:
            try:
                spec = get_tool(tool)
            except KeyError:
                tool_id = tool  # 未注册工具：如实保留原始名，验证必然失败
            else:
                tool_id = self.manager._ensure_tool_record(spec).tool_id
        params = {**(parameters or {}), "_adhoc": {"kind": kind, "summary": summary}}
        experiment = Experiment(
            project_id=project_id, plan_id="", step_id=f"adhoc_{kind}",
            tool_id=tool_id, parameters=params,
            status=ExperimentStatus.COMPLETED, seed=0,
            code_version=__version__,
            environment=self.manager._environment(),
            artifacts=list(artifacts or []), finished_at=_utcnow())
        self.storage.save(experiment)
        self.event_log.append(Event(
            actor=Actor.SYSTEM, action="adhoc_recorded", project_id=project_id,
            object_type="Experiment", object_id=experiment.experiment_id,
            detail={"kind": kind, "tool": tool or "", "summary": summary,
                    "verified": False}))
        return experiment

    def adhoc_verify(self, experiment_id: str) -> object:
        """让 adhoc 记录走同一套三层 golden。只对"注册工具"路径可能通过（A6）；
        与引擎判定解耦的结论：未通过 = 不得引用。"""
        exp = self._require(experiment_id, Experiment)
        if not exp.step_id.startswith("adhoc_"):
            raise ValueError(f"{experiment_id} 不是 adhoc 记录（step_id={exp.step_id}）")
        tool_name = self._tool_name(exp.tool_id)
        if not tool_name:
            raise ValueError(
                "adhoc 记录未关联注册工具——bash 现算数值无法验证（D3/A6："
                "要可验证的临时计算，用注册工具跑并 adhoc_record(tool=...)）")
        return self.verifier.verify_experiment(exp, tool_name)

    # -------------------------------------------------- 异步 job 协议（MCP 层，A2/A3）
    def experiments_run_async(self, plan_id: str, *, max_workers: int = 1) -> str:
        """提交实验 job；worker 内按检查点轮询自身 cancel_requested（M3 协作式取消）。"""
        key = f"experiments:{plan_id}"

        def _run() -> dict:
            job_id = self.jobs.find_active(key)
            return self.experiments_run(
                plan_id, max_workers=max_workers,
                cancel_check=(lambda: self.jobs.cancel_requested(job_id))
                if job_id else None)

        return self.jobs.submit(_run, key=key)

    def job_status(self, job_id: str) -> dict:
        return self.jobs.status(job_id)

    def job_result(self, job_id: str) -> dict:
        return self.jobs.result(job_id)

    def job_cancel(self, job_id: str) -> bool:
        return self.jobs.cancel(job_id)

    # -------------------------------------------------- 只读与产出
    def status(self, project_id: str) -> dict:
        """项目状态投影（只读）：MCP 会话开始时先调它对齐（D1）。

        每个决策同时给出 ``requires_human``（是否必须人工）与
        ``confirmed_by_human`` + ``channel``（人工是否**真的**确认了、经由哪条通道）。
        两者不是一回事：``requires_human=True`` 而 ``confirmed_by_human=False``
        表示结论尚未被确认，agent 不得据此宣布成立。
        """
        st = self.state(project_id)
        self._sync_state_from_ledger(project_id, st)
        experiments = self.storage.list(Experiment, project_id=project_id)
        decisions = st.all_decisions
        plans = self._plans(project_id)
        latest = plans[-1] if plans else None
        concluded = {e.object_id: (e.detail.get("channel") or "未记录")
                     for e in self.event_log.events(project_id=project_id)
                     if e.action == "conclude"}
        return {
            "project_id": project_id,
            "question": self._question(project_id),
            "goal": ({"question": st.goal.question,
                      "quantities": st.goal.quantities,
                      "success_criteria": st.goal.success_criteria}
                     if st.goal else None),
            "n_hypotheses": len(st.hypotheses),
            "rounds_used": len(decisions),
            "plans": [{"plan_id": p.plan_id, "version": p.version,
                       "status": p.status.value, "n_steps": len(p.steps),
                       "awaiting_approval": p.status == PlanStatus.AWAITING_APPROVAL}
                      for p in plans],
            "latest_plan": ({"plan_id": latest.plan_id, "version": latest.version,
                             "status": latest.status.value}
                            if latest else None),
            "experiments": {
                "total": len(experiments),
                "completed": sum(1 for e in experiments
                                 if e.status == ExperimentStatus.COMPLETED),
                "failed": sum(1 for e in experiments
                              if e.status == ExperimentStatus.FAILED),
            },
            "eligible_experiments": sum(1 for e in experiments
                                        if e.verification_status
                                        == VerificationStatus.PASSED),
            "evidence": len(st.all_evidence),
            "conclusion_channel": self.conclude_channel(project_id),
            "decisions": [{"decision_id": d.decision_id, "type": d.type.value,
                           "recommendation": d.recommendation.value,
                           "requires_human": d.requires_human,
                           "confirmed_by_human": d.decision_id in concluded,
                           "channel": concluded.get(d.decision_id)}
                          for d in decisions],
        }

    def events(self, project_id: str, *, since: int = 0, limit: int = 200) -> list[dict]:
        """事件流（只读审计入口，A5 的 events_tail 底座）。"""
        all_events = self.event_log.events(project_id=project_id)
        window = all_events[since:since + limit]
        return [{"seq": since + i, "timestamp": e.timestamp, "actor": e.actor.value,
                 "action": e.action, "object_type": e.object_type,
                 "object_id": e.object_id, "detail": e.detail}
                for i, e in enumerate(window)]

    def ledger(self, project_id: str, kind: str, **filters) -> list[dict]:
        """台账查询（只读）。kind ∈ project/goal/hypothesis/plan/experiment/
        evidence/decision/tool（tool 为跨项目全局）。filters 为字段等值过滤。"""
        cls = _LEDGER_KINDS.get(kind)
        if cls is None:
            raise ValueError(f"未知台账类型 {kind!r}；可用：{sorted(_LEDGER_KINDS)}")
        pid = None if kind == "tool" else project_id
        rows = [obj.model_dump() for obj in self.storage.list(cls, project_id=pid)]
        return [r for r in rows
                if all(r.get(k) == v for k, v in filters.items())]

    def conclude_channel(self, project_id: str) -> str | None:
        """该项目 conclude 事件的取得通道；None = 尚无 conclude 事件。

        保真度审计（A1 补充）：通道是台账的一等事实，报告与状态投影都要显示它，
        使"经由较低保真度通道批准"这件事对读报告的人可见，而不是被 actor=HUMAN
        三个字抹平。本字段引入前的历史事件没有 channel，返回 "未记录"——
        明确不等于 tty，不得反推。
        """
        for e in self.event_log.events(project_id=project_id):
            if e.action == "conclude":
                return e.detail.get("channel") or "未记录"
        return None

    def report_generate(self, project_id: str, *, state: _LoopState | None = None,
                        status: str | None = None) -> Path:
        """生成/刷新报告（确定性拼装，置顶总结 + 计划版本历史）并落账事件。

        state：轮内驱动的进程内累计（含本轮 analyses）；缺省从台账重建
        （analyses 不落账——与 resume 行为一致，报告如实缺该节）。
        status：缺省从台账推导——存在 actor=HUMAN 的 conclude 事件为
        "concluded"（D6 人工确认结论），否则 "terminated"。取得该确认的**通道**
        另经 conclude_channel 渲染在总结里（保真度审计：TTY 与较低保真度的
        webui-local 必须可区分，见 ApprovalChannel）。
        """
        st = state or self.state(project_id)
        if st.goal is None or not st.all_decisions:
            self._sync_state_from_ledger(project_id, st)
        project = self._require(project_id, Project)
        channel = self.conclude_channel(project_id)
        if status is None:
            status = "concluded" if channel is not None else "terminated"
        plans = self._plans(project_id)
        plan_notes: dict[str, dict] = {}
        for e in self.event_log.events(project_id=project_id):
            if e.action == "plan_ready":
                plan_notes.setdefault(e.object_id, {}).update({
                    "verdict": e.detail.get("verdict", "—"),
                    "issues": e.detail.get("issues", []),
                })
            elif e.action == "approve":
                plan_notes.setdefault(e.object_id, {})["approved_by"] = e.actor.value
        report_md = generate_report(
            project, st.goal, st.hypotheses, plans, st.all_rows,
            st.all_decisions, st.all_analyses, status=status,
            plan_notes=plan_notes, all_evidence=st.all_evidence,
            conclude_channel=channel)
        report_path = Path(self.storage.path).parent / "report.md"
        report_path.write_text(report_md, encoding="utf-8")
        self.event_log.append(Event(actor=Actor.SYSTEM, action="report_generated",
                                    project_id=project_id, object_type="Report",
                                    object_id=str(report_path),
                                    detail={"rounds_used": len(st.all_decisions)}))
        return report_path

    def plot(self, project_id: str, out_dir: str | Path) -> list[Path]:
        """三张结果图（确定性绘制，失败也画，不做美化）。"""
        from qresearch.viz import plot_project

        return plot_project(self.storage, project_id, out_dir)

    # -------------------------------------------------- 记忆
    def distill_to_memory(self, project_id: str) -> None:
        """项目结束蒸馏入库（转人工的部分经验同样有价值）。"""
        if self.memory_store is None:
            return
        from qresearch.memory import distill_project

        entries = distill_project(self.storage, project_id)
        for entry in entries:
            self.memory_store.add(entry)
        self.event_log.append(Event(
            actor=Actor.SYSTEM, action="memory_written", project_id=project_id,
            object_type="Memory", object_id=project_id,
            detail={"n_entries": len(entries),
                    "layers": sorted({e.layer.value for e in entries})}))

    # -------------------------------------------------- 恢复
    def resume_state(self, project_id: str) -> _LoopState:
        """从台账重建跨轮状态并落账 resumed 事件（真相在库里，计划 v2 §7.8）。"""
        st = self._states[project_id] = _LoopState()
        goals = self.storage.list(Goal, project_id=project_id)
        st.goal = goals[0] if goals else None
        st.hypotheses = self.storage.list(Hypothesis, project_id=project_id)
        st.previous_plan = self._plans(project_id)[-1] if self._plans(project_id) else None
        st.all_decisions = sorted(self.storage.list(Decision, project_id=project_id),
                                  key=lambda d: d.created_at)
        if st.all_decisions:
            st.last_decision_id = st.all_decisions[-1].decision_id
        st.all_evidence = self.storage.list(Evidence, project_id=project_id)

        # 工具映射：experiment.step_id → 工具名（tool_id 经 ToolRecord 解析）
        tool_name = {t.tool_id: t.name for t in self.storage.list(ToolRecord)}
        for exp in self.storage.list(Experiment, project_id=project_id):
            name = tool_name.get(exp.tool_id, exp.tool_id)
            if name:
                st.tool_by_step.setdefault(exp.step_id, name)
        self._tool_names = tool_name

        start_round = len(st.all_decisions) + 1
        self.event_log.append(Event(
            actor=Actor.SYSTEM, action="resumed", project_id=project_id,
            object_type="Loop", object_id=project_id,
            detail={"start_round": start_round,
                    "plans": len(self._plans(project_id)),
                    "decisions": len(st.all_decisions),
                    "evidence": len(st.all_evidence)}))
        return st

    # -------------------------------------------------- internals
    def _require(self, object_id: str, cls: type):
        obj = self.storage.get(cls, object_id)
        if obj is None:
            raise KeyError(f"{cls.__name__} 不存在: {object_id}")
        return obj

    def _plans(self, project_id: str) -> list:
        return sorted(self.storage.list(ResearchPlan, project_id=project_id),
                      key=lambda p: p.version)

    def _tool_name(self, tool_id: str) -> str | None:
        """tool_id（ToolRecord 哈希）→ 工具名；裸注册名也接受。"""
        names = getattr(self, "_tool_names", None)
        if names is None:
            names = {t.tool_id: t.name for t in self.storage.list(ToolRecord)}
            self._tool_names = names
        if tool_id in names:
            return names[tool_id]
        from qresearch.tools.registry import get_tool

        try:
            get_tool(tool_id)
        except KeyError:
            return None
        return tool_id

    def _tool_for(self, project_id: str, step_id: str) -> str | None:
        st = self.state(project_id)
        if step_id in st.tool_by_step:
            return st.tool_by_step[step_id]
        # parameter_scan 子实验的 step_id 为 "<step_id>_<idx>"：回溯父步骤
        base = step_id
        while "_" in base:
            base = base.rsplit("_", 1)[0]
            if base in st.tool_by_step:
                return st.tool_by_step[base]
        return None

    def _sync_state_from_ledger(self, project_id: str, st: _LoopState) -> None:
        """把台账内容补进状态载体（status/report_generate 的只读对齐；不落事件）。"""
        if st.goal is None:
            goals = self.storage.list(Goal, project_id=project_id)
            st.goal = goals[0] if goals else None
        if not st.hypotheses:
            st.hypotheses = self.storage.list(Hypothesis, project_id=project_id)
        if not st.all_decisions:
            st.all_decisions = sorted(
                self.storage.list(Decision, project_id=project_id),
                key=lambda d: d.created_at)
            if st.all_decisions:
                st.last_decision_id = st.all_decisions[-1].decision_id
        if not st.all_evidence:
            st.all_evidence = self.storage.list(Evidence, project_id=project_id)
        if not st.all_rows:
            experiments = self.storage.list(Experiment, project_id=project_id)
            tool_name = {t.tool_id: t.name for t in self.storage.list(ToolRecord)}
            for exp in experiments:
                name = tool_name.get(exp.tool_id, exp.tool_id)
                if name:
                    st.tool_by_step.setdefault(exp.step_id, name)
            # 报告的实验表按台账全量重建（eligible 无关紧要：仅用于 prompt 裁剪）
            _, rows = _experiment_rows(experiments, {e.experiment_id for e in experiments},
                                       lambda sid: self._tool_for(project_id, sid))
            st.all_rows = rows

    def _memory_digest_text(self, project_id: str, st: _LoopState) -> str:
        if self.memory_store is None:
            return ""
        from qresearch.memory import memory_digest

        goal = st.goal
        query = (f"{goal.question} {' '.join(goal.quantities)}"
                 if goal else self._question(project_id))
        digest, n_hits = memory_digest(self.memory_store, query)
        self.event_log.append(Event(
            actor=Actor.SYSTEM, action="memory_retrieved", project_id=project_id,
            object_type="Memory", object_id=project_id,
            detail={"query": query[:200], "n_hits": n_hits}))
        return digest


def _utcnow():
    from qresearch.core.models import utcnow

    return utcnow()
