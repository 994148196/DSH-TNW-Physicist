# agent.md

面向 AI Coding Agent（Claude Code / Codex / DSH 自身等）的项目工作说明。在本仓库内工作前必读。

## 必读文档（按优先级）

1. **`基于DSH的量子多体自主科研系统开发计划_v2.md`** —— 权威开发计划（完整修订版，与旧文档冲突处以它为准）
2. **`PROGRESS.md`** —— 开发进度（每完成阶段或里程碑必须更新它）
3. `docs/phase0-recon.md` —— DSH 侦察细节
4. `基于DSH的量子多体自主科研系统开发计划_精炼版.md` —— 原始计划（存档）

## 项目是什么

**DSH-TNW-Physicist**：基于 DeepSeek Harness（DSH）的量子多体自主科研系统（内部代号 `qresearch`）。

它不是预先写死的 ED / DMRG / MPS / iPEPS 算法库，也不是"物理大模型 + 若干工具"。核心目标是：

> 用户提出科研问题后，Agent 能理解问题、提出假设、制定研究计划、动态发现或开发工具、运行数值实验、三重验证、分析数据，并根据结果更新假设与计划。

定位校准（计划 v2 §0）：交付的是**可审计、可复现、半自动的科研流水线**；"自主发现新物理"不作验收标准。

## 架构分工（Phase 0 已核实）

| 层 | 职责 |
|---|---|
| DSH（执行内核） | TypeScript/Cordis 运行时：模型适配（`deepseek-official`）、步内 agent loop、shell/文件/Git 工具、JSONL 会话（fork/resume）、沙箱、审批策略 |
| qresearch（科研智能层，本项目全部代码） | **Python 主导的确定性科研主循环（状态机）**：账本（状态层）+ 站点执行器（流程层）+ Action 注册表；Goal/Hypothesis/Planner/Critic/Experiment/Verification/Analyst/Memory |
| 工具层 | NumPy/SciPy/Matplotlib/PyTorch + ED/DMRG 适配器，统一 `ResearchTool` 接口 |

## DSH 集成方式（重要）

1. **Python SDK 驱动**：qresearch 经官方 `deepseek-harness-sdk`（stdio JSON-RPC，隔离 `DSH_HOME`）把 DSH 当"单步执行引擎"——每个需要智能的站点 = 一次 `harness.run()` 轮次。
2. **实验执行不经 LLM**：Experiment Manager 直接 subprocess 跑 Python 工具并记录元数据。
3. **MCP / TS 插件靠后**：MCP 留给 Phase 6（Tool Builder）；TypeScript cordis 插件是最后手段。
4. SDK 不在 PyPI，需从源码安装（计划 v2 §2.3 三路径）；SDK 与 runtime 锁版本；`qresearch/dsh_client.py` 是唯一封装点（可替换运行时）。

## 开发铁律（必须遵守）

1. **不改 DSH 核心**：不写 TS 插件、不改 DSH 源码；定制走 SDK 参数、profile patch 或外围 Python 包。
2. **科研状态与执行状态分离**：研究对象全部落在 SQLite+JSON/YAML 账本，绝不只存对话历史或 DSH Session。
3. **LLM 只提议，代码记账**：LLM 输出永远是提案（生成器/工具型 agent/批评者三种形态），经 Pydantic 校验、由执行器落账才成为状态。
4. **Spec 先行，出题人≠答题人**：新工具先有含基准清单与容差的 Tool Spec；golden fixtures 版本化，编码 agent 无权修改。
5. **实验必须可复现**：代码版本、参数、环境、种子、日志全落账；失败也落账。
6. **结论必须挂证据**：不过验证的实验取消证据资格；禁止把未验证趋势表述为物理结论；"新物理"判断无条件人工。
7. **自主性按决策类别挣得**：Decision 落账（checklist+证据引用+requires_human），分歧率决定放开节奏。
8. **上下文=状态的投影**：站点 prompt 从账本现算并带尺寸预算；原始数据不进 prompt，按需用工具读；跨轮次开新 session。
9. **高风险操作需人类审批**：审批门在 qresearch 循环层；第一版 Manual 模式。
10. **第一版保持简单**：不做完整算法库、不做分布式、不做复杂多 Agent。

## 技术栈

- Python ≥3.10（Pydantic v2 + 类型标注）、SQLite、YAML/JSON、Markdown、Git
- DSH 接入：`deepseek-harness-sdk`
- 科研工具：NumPy、SciPy、Matplotlib、PyTorch；后续 TeNPy / quimb
- 数值验证协议：差分测试、golden fixtures、解析解校验（1D Heisenberg：E/L = 1/4 − ln 2 ≈ −0.4431471806）
- 分支开发：改动走独立分支 → 测试 → 基准 → 变更报告 → 人工批准合并

## 目录约定

```text
PROGRESS.md          # 进度（每次完成后更新）
docs/                # 侦察与设计文档
qresearch/           # 科研智能层：core / dsh_client / stations / execution / verification / tools / memory / reports
tools/               # simple_ed、dmrg_adapter、benchmarks（golden fixtures）
examples/            # 验收演示脚本
tests/               # 所有核心模块必须有测试
research_data/       # 运行时产生（大产物 git 忽略，见 .gitignore）
```

## 开发阶段与当前状态

Phase 0 → 1 Research State → 2 Planning → 3 Experiment Manager → 4 Verification → 5 Loop 闭环 → 6 Tool Builder → 7 Memory → 8 HPC

**当前状态：Phase 0 侦察完成，SDK smoke test 待做；Phase 1 计划就绪。实时进度看 `PROGRESS.md`，下一步指令看计划 v2 §12。**

## 每阶段完成的汇报要求

修改了哪些文件；为什么这样设计；如何运行；如何测试；已知限制；下一步建议。**并更新 `PROGRESS.md`。**

## 命令

```bash
# Phase 0 收尾（SDK 安装三路径见计划 v2 §2.3）
uv venv && uv pip install -e D:/AI/Agent/Try/deepseek-harness/python/sdk

# qresearch（Phase 1 起）
pip install -e .
pytest tests/
```
