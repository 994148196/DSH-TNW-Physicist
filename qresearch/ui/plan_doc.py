"""计划文档化（Phase 9.2）：把 ResearchPlan 渲染成可读、可编辑的 markdown。

设计要点：
- 文档由台账对象确定性渲染（LLM 不参与），每轮审批前自动写入
  <项目目录>/plans/plan_vN.md——既是给人读的，也是"直接改文档"交互的载体；
- 文档末尾固定有"修改意见"节：用户可以只写意见（意见通道），也可以直接
  编辑上面的步骤（编辑通道，由 transcribe_plan 站点转录回结构化计划，
  再走语义校验 + critic + 审批——用户编辑不绕过验证管线）。
"""
from __future__ import annotations

from pathlib import Path

from qresearch.core.models import Goal, Hypothesis, ResearchPlan

_EDIT_HINT = (
    "---\n\n"
    "<!-- 两种用法（可并用）：\n"
    "  1. 意见通道：在下面「修改意见」节里写你想改什么（可多行），保存后在终端按回车；\n"
    "  2. 编辑通道：直接修改上面的步骤（工具/输入/目的……），保存后在终端按回车。\n"
    "     系统会把编辑后的文档转录回结构化计划，仍要过语义校验 + critic + 你的最终审批。\n"
    "  什么都不改直接回车 = 回到菜单。 -->\n\n"
    "## 修改意见\n\n（在此填写……）\n"
)


def _yaml_inputs(inputs: dict) -> str:
    if not inputs:
        return "（无）"
    try:
        import yaml
        return "```yaml\n" + yaml.safe_dump(inputs, allow_unicode=True,
                                            sort_keys=True).rstrip() + "\n```"
    except Exception:  # noqa: BLE001 —— 渲染失败用 repr 兜底
        return "```yaml\n" + repr(inputs) + "\n```"


def render_plan_markdown(
    plan: ResearchPlan, *,
    goal: Goal | None = None,
    hypotheses: list[Hypothesis] | None = None,
    critique_verdict: str | None = None,
    critique_issues: list[dict] | None = None,
    project_title: str | None = None,
) -> str:
    lines = [
        f"# 研究计划 v{plan.version}（{plan.status.value}）",
        "",
        f"- 项目：`{plan.project_id}`" + (f"——{project_title}" if project_title else ""),
        f"- 计划 id：`{plan.plan_id}`",
    ]
    if plan.based_on_decision:
        lines.append(f"- 依据决策：`{plan.based_on_decision}`（replan/修订）")
    if goal is not None:
        lines += [
            "",
            "## 研究目标",
            "",
            f"- 研究问题：{goal.question}",
            f"- 目标量：{'、'.join(goal.quantities) or '—'}",
            f"- 成功判据：{'；'.join(goal.success_criteria) or '—'}",
        ]
    if hypotheses:
        lines += ["", "## 假设（及证伪试验）", ""]
        lines += [f"{i}. {h.statement}\n   - 证伪：{'；'.join(h.falsification_tests)}"
                  for i, h in enumerate(hypotheses, start=1)]
    lines += ["", f"## 步骤（{len(plan.steps)} 步）", ""]
    for s in plan.steps:
        flag = " **[需审批]**" if s.requires_approval else ""
        lines += [
            f"### {s.step_id} [{s.action}] {s.purpose}{flag}",
            "",
            f"- 工具：{'、'.join(s.tools) or '—'}",
            f"- 输入：\n{_yaml_inputs(s.inputs)}",
            f"- 预期输出：{'、'.join(s.expected_outputs) or '—'}",
            "",
        ]
    if plan.risks:
        lines += ["## 风险", "", *[f"- {r}" for r in plan.risks], ""]
    if plan.diff_summary:
        lines += ["## 与上一版差异", "", plan.diff_summary, ""]
    if critique_verdict is not None:
        lines += [f"## critic 审查结论：{critique_verdict}", ""]
        for i in (critique_issues or []):
            sev = i.get("severity", "?") if isinstance(i, dict) else "?"
            sid = i.get("step_id") or "整体" if isinstance(i, dict) else "整体"
            desc = i.get("description", "") if isinstance(i, dict) else str(i)
            mark = "⛔" if sev == "blocker" else "·"
            lines.append(f"- {mark} [{sev}] {sid}：{desc}")
        lines.append("")
    lines.append(_EDIT_HINT)
    return "\n".join(lines)


def save_plan_doc(
    project_dir: Path, plan: ResearchPlan, **kwargs,
) -> Path:
    """写入 <project_dir>/plans/plan_vN.md，返回路径。"""
    out_dir = Path(project_dir) / "plans"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"plan_v{plan.version}.md"
    path.write_text(render_plan_markdown(plan, **kwargs), encoding="utf-8")
    return path


def split_user_notes(plan_md: str) -> tuple[str, str]:
    """从编辑后的计划文档里拆出（正文，修改意见）。

    修改意见 = 「## 修改意见」标题之后的内容（去掉 HTML 注释提示行）。
    """
    marker = "## 修改意见"
    if marker not in plan_md:
        return plan_md, ""
    body, notes = plan_md.split(marker, 1)
    # 去掉编辑提示注释行（<!-- ... -->）与首尾空白
    cleaned: list[str] = []
    in_comment = False
    for line in notes.splitlines():
        if "<!--" in line:
            in_comment = True
        if not in_comment:
            cleaned.append(line)
        if "-->" in line:
            in_comment = False
    notes_text = "\n".join(cleaned).strip()
    placeholder = "（在此填写……）"
    if notes_text == placeholder:
        notes_text = ""
    return body.strip(), notes_text
