# qresearch —— 基于 DSH 的量子多体自主科研系统

给定一个凝聚态物理研究问题，自动完成 **理解 → 假设 → 计划（人工审批）→
数值实验 → 三层验证 → 分析 → 决策** 的闭环，产出研究报告、完整证据链与
可回放的操作日志。LLM（经 [DSH](https://github.com/deepseek-ai/dsh) /
deepseek-v4-flash）只负责"提议"；记账、执行、验证、决策落账全部是确定性代码。

**当前状态**：开发计划 v2 **Phase 0–8 全部完成**（均经真实 LLM live 验收），
**Phase 9 交互层完成**；开发计划 v3（交互式 Agent 化）**M0–M3 全部完成**：
MCP server 22 工具接入 DSH（`dsh --profile research`）、CLI 审批台、协作式取消。
125 项测试全绿。详见 [PROGRESS.md](PROGRESS.md)。

## 文档

| 文档 | 读者 |
|---|---|
| [docs/user_guide.md](docs/user_guide.md) | **使用者**：安装、快速上手、四个真实案例、读懂输出、FAQ |
| [docs/manual.md](docs/manual.md) | **开发者/维护者**：全部技术——架构、每层机制、接口、铁律、设计权衡 |
| [PROGRESS.md](PROGRESS.md) | 阶段进度、验收清单、里程碑日志 |

## 功能总览

| 子系统 | 能力 |
|---|---|
| 研究闭环 | 3–N 轮全自动：understand/hypothesize/plan/critic/transcribe/analyze/decide 七站点，计划审批与结论确认两个人工触点，全程事件落账可回放 |
| 实验执行 | 子进程数值实验（不经 LLM）、参数扫描、实验并行、超时与失败落账 |
| 数值工具 | simple_ed（Heisenberg ED）、dmrg_adapter（quimb DMRG + 回退）、tfim_ed（由 DSH 自动构建并晋升）；golden 基准锁定数值 |
| 验证 | 程序/算法/物理三层验证 + 证据资格门（验证不过不得引用为证据） |
| 工具构建 | Spec 先行 + 防串通（编码 prompt 零基准数值）+ 修复循环 + 批评者审查 + 晋升 |
| 研究记忆 | 跨项目四层经验库（项目/方法/工具/失败案例），确定性蒸馏、失败案例优先检索注入、Markdown 镜像 |
| 运维 | 预算闸（轮数/实验数/墙钟）、PAUSE 旗标暂停 + 台账恢复续跑、多项目队列 + 每项目沙箱隔离、Slurm 后端（dry-run 诚实边界） |
| MCP / Agent 接入 | `dsh --profile research` 在 DSH 壳里对话式驱动研究闭环：22 个 MCP 工具（站点/实验/分析链/job 协议/台账查询），长工具一律返回 `job_id` 轮询，同 key 幂等防重复实验；审批与结论的"写"只在真实终端（TTY 守卫 fail-closed），MCP 面只有查询（A1/D6）；job_cancel 协作式取消（诚实落账） |
| 交互与产出 | `qresearch` 命令进入交互会话（自然语言开题 + 斜杠命令，[ui]）、事件驱动实时进度条、对话式计划审批（多行口述意见 或 直接编辑 `plans/plan_vN.md` 文档 → 转录→校验→critic→再审批，编辑不绕过验证管线）、轮末回调（节点汇报/叫停/插话注入下一轮）、置顶总结 + 计划版本历史的自动报告、三张台账可视化图（[viz]） |

## 最小示例

交互式（推荐，像 Code Agent 一样）：

```bash
qresearch                 # 输入研究问题即可开跑；/help 看命令
```

```python
from qresearch.research_loop import run_research_loop   # 完整用法见 docs/user_guide.md
summary = run_research_loop(client, storage, log, "proj_1", "你的研究问题",
                            rounds=3, auto_approve=True)
```

## 开发

```bash
.venv/Scripts/python.exe -X utf8 -m pytest -q        # 104 passed
.venv/Scripts/python.exe -X utf8 examples/phase5_demo.py        # 离线 3 轮闭环
.venv/Scripts/python.exe -X utf8 examples/phase8_demo.py --live # live 双项目队列
```
