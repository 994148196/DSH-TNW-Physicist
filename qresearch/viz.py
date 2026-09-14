"""研究结果可视化（可选依赖 matplotlib）：从台账确定性绘图，LLM 不参与。

plot_project(storage, project_id, out_dir) 产出三张 PNG（结果变化一目了然）：
  1. experiment_tools.png  —— 工具 × 状态堆叠柱状图（实验概览：完成/失败分布）
  2. results_vs_param.png  —— 数值结果随参数的变化，按计划版本着色（哪一轮
     产出了哪些数、不同版本计划的结果如何演变）
  3. evidence_timeline.png —— 证据累计曲线 + 决策时间线（信息积累节奏与
     每轮决策的位置）

诚实边界：只画台账里真实发生的事（含失败），不做任何美化推断；
数值点以验证状态区分（实心=passed，空心=未通过/未跑）。
安装：pip install -e ".[viz]"
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from qresearch.core.models import Decision, Experiment, ResearchPlan
from qresearch.core.status import ExperimentStatus, VerificationStatus
from qresearch.core.storage import Storage


def _plt():
    """延迟导入 matplotlib（Agg 后端 + 中文字体），缺失时给出可操作的报错。"""
    try:
        import matplotlib
    except ImportError as exc:  # pragma: no cover
        raise ImportError('可视化需要 matplotlib：pip install -e ".[viz]"') from exc
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _plan_versions(storage: Storage, experiments: list[Experiment]) -> dict[str, int]:
    """plan_id → version（用于按计划版本给结果点上色）。"""
    versions: dict[str, int] = {}
    for e in experiments:
        if e.plan_id not in versions:
            plan = storage.get(ResearchPlan, e.plan_id)
            versions[e.plan_id] = plan.version if plan else 0
    return versions


def _plot_tools(plt, experiments: list[Experiment], path: Path) -> None:
    """图 1：工具 × 状态堆叠柱状图。"""
    by_tool: dict[str, Counter] = defaultdict(Counter)
    for e in experiments:
        by_tool[e.tool_id][e.status.value] += 1
    plt.figure(figsize=(max(6, 1.6 * max(1, len(by_tool))), 4.5))
    if not by_tool:
        plt.text(0.5, 0.5, "（无实验记录）", ha="center", va="center")
    else:
        tools = sorted(by_tool)
        statuses = sorted({st for c in by_tool.values() for st in c})
        colors = {"completed": "#2a9d8f", "failed": "#e76f51",
                  "running": "#e9c46a", "created": "#8d99ae", "cancelled": "#b0b8c4"}
        bottom = [0.0] * len(tools)
        for st in statuses:
            vals = [by_tool[t].get(st, 0) for t in tools]
            plt.bar(tools, vals, bottom=bottom, label=st,
                    color=colors.get(st, "#6c757d"))
            bottom = [b + v for b, v in zip(bottom, vals)]
        plt.ylabel("实验数")
        plt.title("实验概览：工具 × 状态")
        plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()


def _numeric_scalar(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _plot_results(plt, storage: Storage, experiments: list[Experiment],
                  versions: dict[str, int], path: Path) -> None:
    """图 2：数值结果 vs 参数（按计划版本着色；实心=验证通过，空心=否则）。"""
    from qresearch.experiments.manager import _load_result

    # 数据点：(参数名, 参数值, 结果名, 结果值, 版本, 验证通过)
    # 每个数值型标量参数各出一组候选点（排除 seed/method 等非物理量）
    points: list[tuple[str, float, str, float, int, bool]] = []
    for e in experiments:
        if e.status != ExperimentStatus.COMPLETED:
            continue
        result = _load_result(e) or {}
        passed = e.verification_status == VerificationStatus.PASSED
        for pk in sorted(e.parameters):
            if pk in ("seed", "method") or pk.startswith("_"):
                continue  # 内部簿记参数（_elapsed_s 等）不是物理参数
            x = _numeric_scalar(e.parameters[pk])
            if x is None:
                continue
            for rk, rv in sorted(result.items()):
                if rk in e.parameters or rk.startswith("_"):
                    continue  # 结果里原样回显的输入值（如 N、J）不重复作 y 轴
                y = _numeric_scalar(rv)
                if y is not None:
                    points.append((pk, x, rk, y, versions.get(e.plan_id, 0), passed))
    # 优先画"有变化的参数"（≥2 个不同取值）——常数参数（如固定 J=1）没有标度信息
    distinct: dict[str, set] = defaultdict(set)
    for name, x, *_ in points:
        distinct[name].add(x)
    varied = sorted(name for name, xs in distinct.items() if len(xs) >= 2)
    if varied:
        points = [pt for pt in points if pt[0] in set(varied)]
    if not points:
        plt.figure(figsize=(7, 4))
        plt.text(0.5, 0.5, "（没有可绘制的数值结果——实验未完成或结果非数值）",
                 ha="center", va="center")
        plt.savefig(path, dpi=150, bbox_inches="tight")
        plt.close()
        return

    params = sorted({p[0] for p in points})
    results = sorted({p[2] for p in points})
    n_plots = len(params) * len(results)
    fig, axes = plt.subplots(n_plots, 1, figsize=(7, 3.2 * n_plots), squeeze=False)
    versions_all = sorted({p[4] for p in points})
    cmap = plt.get_cmap("viridis")
    colors = {v: cmap(i / max(1, len(versions_all) - 1))
              for i, v in enumerate(versions_all)}
    ax_i = 0
    for pname in params:
        for rname in results:
            ax = axes[ax_i][0]
            ax_i += 1
            sel = [p for p in points if p[0] == pname and p[2] == rname]
            # 同参数多值时以浅灰线连出趋势（按参数值排序）
            if len(sel) > 2 and len({p[1] for p in sel}) == len(sel):
                line = sorted((p[1], p[3]) for p in sel)
                ax.plot([a for a, _ in line], [b for _, b in line],
                        color="#adb5bd", linewidth=0.8, zorder=1)
            for v in versions_all:
                pts = sorted((p[1], p[3], p[5]) for p in sel if p[4] == v)
                if not pts:
                    continue
                label = f"计划 v{v}"
                for xx, yy, ok in pts:
                    # 实心=验证通过；空心=未通过/未验证（诚实展示，不隐瞒）
                    ax.scatter(xx, yy, s=42, label=label,
                               facecolor=colors[v] if ok else "none",
                               edgecolor=colors[v], linewidth=1.4, zorder=3)
                    label = None  # 图例只记一次
            ax.set_xlabel(pname)
            ax.set_ylabel(rname)
            ax.set_title(f"{rname} vs {pname}", fontsize=10)
            ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _plot_timeline(plt, evidence_created: list[datetime],
                   decisions: list[Decision], path: Path) -> None:
    """图 3：证据累计曲线 + 决策时间线（虚线=决策）。"""
    plt.figure(figsize=(9, 4.5))
    if evidence_created:
        times = sorted(evidence_created)
        plt.plot(times, range(1, len(times) + 1), color="#2a9d8f",
                 linewidth=1.8, label="证据累计")
    rec_colors = {"iterate": "#457b9d", "terminate": "#2a9d8f",
                  "replan": "#e9c46a", "propose": "#8d99ae",
                  "accept": "#264653", "escalate": "#e76f51"}
    n_ev = max(1, len(evidence_created))
    for d in decisions:
        x = d.created_at
        if evidence_created:
            lo, hi = min(evidence_created), max(evidence_created)
            span = (hi - lo).total_seconds() or 1.0
            y = 1 + (x - lo).total_seconds() / span * n_ev
        else:
            y = 1
        plt.axvline(x, color=rec_colors.get(d.recommendation.value, "#6c757d"),
                    linestyle="--", linewidth=1.2, alpha=0.8)
        plt.annotate(
            f"决策：{d.recommendation.value}\n{x:%m-%d %H:%M}",
            xy=(x, y), fontsize=8, va="bottom",
            color=rec_colors.get(d.recommendation.value, "#6c757d"))
    plt.ylabel("证据条数")
    plt.title("证据累计与决策时间线（虚线=决策）")
    if evidence_created:
        plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()


def plot_project(storage: Storage, project_id: str,
                 out_dir: str | Path) -> list[Path]:
    """为项目生成三张结果变化图，返回写入的 PNG 路径列表（确定性、可重放）。"""
    plt = _plt()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    experiments = storage.list(Experiment, project_id=project_id)
    decisions = storage.list(Decision, project_id=project_id)
    versions = _plan_versions(storage, experiments)

    from qresearch.core.models import Evidence

    paths = []
    p1 = out / "experiment_tools.png"
    _plot_tools(plt, experiments, p1)
    paths.append(p1)

    p2 = out / "results_vs_param.png"
    _plot_results(plt, storage, experiments, versions, p2)
    paths.append(p2)

    evidence_created = [ev.created_at for ev in
                        storage.list(Evidence, project_id=project_id)]
    p3 = out / "evidence_timeline.png"
    _plot_timeline(plt, evidence_created, decisions, p3)
    paths.append(p3)
    return paths
