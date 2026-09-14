"""MCP 冒烟（计划 v3 M2 检验 1 / L2 离线断言，不花 token）。

经**真实 stdio 传输**（与 DSH 的 `@deepseek-ai/dsh-mcp-client` 同契约）起
qresearch MCP server 子进程，断言：
  1. 工具清单齐全（22 个，A2 异步协议 + A1 台账仲裁面）；
  2. research_open 可创建项目，research_status 形状正确；
  3. 只读工具（research_status/research_list/ledger_query/events_tail/plan_approve
     查询/job_status）**零写入**——state.sqlite 与 events.jsonl 字节级不变（L2 核心）；
  4. 错误以 {"error": ...} 文本返回（模型可读原因），不炸通道；
  5. plan_approve 是纯查询：不产生 actor=HUMAN 事件（A1）。

用法：
    .venv/Scripts/python.exe -X utf8 examples/mcp_smoke.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "qresearch" / "mcp_server.py"

EXPECTED_TOOLS = {
    # 项目
    "research_open", "research_status", "research_list",
    # 站点（A2 异步）
    "understand", "hypothesize", "plan_create", "plan_revise",
    # 审批（A1 台账仲裁）
    "plan_show", "plan_approve",
    # 执行与验证
    "experiments_run", "verify",
    # 分析与决策
    "analyze", "decide",
    # adhoc（D3/A6）
    "adhoc_record", "adhoc_verify",
    # 产出与只读
    "report_generate", "viz_plot", "ledger_query", "events_tail",
    # job 协议
    "job_status", "job_result", "job_cancel",
}


def _snapshot(project_dir: Path) -> dict[str, bytes]:
    def rd(name: str) -> bytes:
        p = project_dir / name
        return p.read_bytes() if p.exists() else b""
    return {"db": rd("state.sqlite"), "log": rd("events.jsonl")}


async def _call(session: ClientSession, name: str, arguments: dict) -> dict:
    res = await session.call_tool(name, arguments)
    assert not res.is_error, f"{name} 返回 is_error：{res.content}"
    text = res.content[0].text
    return json.loads(text)


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qresearch_mcp_smoke_"))
    params = StdioServerParameters(
        command=sys.executable,
        args=["-X", "utf8", str(SERVER), "--project-root", str(tmp)],
    )
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" —— {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    print(f"smoke 项目根：{tmp}")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. 工具清单
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            missing = EXPECTED_TOOLS - names
            check(f"工具清单齐全（{len(names)} 个）", not missing,
                  f"缺失：{sorted(missing)}" if missing else "")

            # 2. research_open + research_status
            st = await _call(session, "research_open",
                             {"project_id": "smoke", "question": "一维 Heisenberg 模型基态能隙的尺寸标度"})
            check("research_open 返回状态投影", st.get("project_id") == "smoke"
                  and "rounds_used" in st and "experiments" in st)
            pdir = tmp / "smoke"

            # 预置一条待审批计划（父进程直写台账；A1 断言用）
            from qresearch.core.models import PlanStep, ResearchPlan
            from qresearch.core.storage import Storage

            _plan = ResearchPlan(
                project_id="smoke", goal_id="goal_smoke", version=1,
                steps=[PlanStep(step_id="s1", action="run_experiment",
                                purpose="N=4 基准", tools=["simple_ed"],
                                inputs={"N": 4, "want_gap": True})])
            _storage = Storage(pdir / "state.sqlite")
            try:
                _storage.save(_plan)
            finally:
                _storage.close()
            plan_id_saved = _plan.plan_id

            # 3. 只读工具零写入（L2 核心）
            for tool, args in [
                ("research_status", {"project_id": "smoke"}),
                ("research_list", {}),
                ("ledger_query", {"project_id": "smoke", "kind": "experiment"}),
                ("ledger_query", {"project_id": "smoke", "kind": "tool"}),
                ("events_tail", {"project_id": "smoke"}),
                ("plan_approve", {"project_id": "smoke", "plan_id": plan_id_saved}),
                ("job_status", {"job_id": "job_nope"}),
            ]:
                before = _snapshot(pdir)
                out = await _call(session, tool, args)
                after = _snapshot(pdir)
                check(f"只读零写入：{tool}", before == after,
                      "" if before == after else "台账字节发生变化！")
                if tool == "plan_approve":
                    check("A1：plan_approve 纯查询（不写 HUMAN 事件）",
                          out.get("approved_by_human") is False
                          and "how_to" in out, json.dumps(out, ensure_ascii=False)[:120])
                if tool == "job_status":
                    check("job_status 未知 id 返回 error dict", "error" in out,
                          out.get("error", ""))

            # 4. 错误形状：{"error": ...}（不炸通道）+ A2 异步协议的失败可查
            out = await _call(session, "research_status", {"project_id": "ghost"})
            check("不存在项目 → error dict（通道不炸）",
                  "error" in out and "不存在" in out["error"], out.get("error", ""))
            out = await _call(session, "analyze", {"project_id": "smoke"})
            check("analyze 无资格实验 → error dict", "error" in out,
                  out.get("error", ""))
            out = await _call(session, "adhoc_verify",
                              {"project_id": "smoke", "experiment_id": "exp_ghost"})
            check("adhoc_verify 走 job 协议（A2）", bool(out.get("job_id")),
                  json.dumps(out, ensure_ascii=False)[:100])
            job_res: dict = {}
            for _ in range(100):
                job_res = await _call(session, "job_result",
                                      {"job_id": out["job_id"]})
                if job_res.get("state") in ("done", "error", "cancelled"):
                    break
                await asyncio.sleep(0.05)
            check("job_result 捕获后台失败（实验不存在）",
                  job_res.get("state") == "error"
                  and "不存在" in (job_res.get("error") or ""),
                  job_res.get("error") or json.dumps(job_res, ensure_ascii=False)[:100])

    if failures:
        print(f"\nsmoke 失败 {len(failures)} 项：{failures}")
        shutil.rmtree(tmp, ignore_errors=True)
        return 1
    print("\nsmoke 全部通过（stdio 契约 + 只读零写入 + A1 仲裁 + 错误形状）")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
