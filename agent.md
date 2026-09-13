# agent.md

面向 AI Coding Agent（Claude Code / Codex / DSH 自身等）的项目工作说明。在本仓库内工作前必读。

## 项目是什么

**DSH-TNW-Physicist**：基于 DeepSeek Harness（DSH）的量子多体自主科研系统（内部代号 `qresearch`）。

它不是预先写死的 ED / DMRG / MPS / iPEPS 算法库，也不是"物理大模型 + 若干工具"。核心目标是：

> 用户提出科研问题后，Agent 能理解问题、提出假设、制定研究计划、动态发现或开发工具、运行数值实验、三重验证、分析数据，并根据结果更新假设与计划。

权威设计文档：`基于DSH的量子多体自主科研系统开发计划_精炼版.md`；DSH 实际形态与集成架构核实结果：`docs/phase0-recon.md`（优先级更高，与精炼版冲突处以它为准）。

## 架构分工（Phase 0 已核实）

| 层 | 职责 |
|---|---|
| DSH（运行内核） | TypeScript/Cordis 插件式运行时：模型适配（`deepseek-official`）、agent loop、shell/文件/Git 工具、JSONL 会话（fork/resume）、沙箱、审批策略 |
| qresearch（科研智能层，本项目开发内容） | **Python 主导的确定性科研主循环** + Goal Understanding、Hypothesis Manager、Planner、Plan Critic、Tool Discovery/Builder、Experiment Manager、Verification Manager、Result Analyst、Research Memory |
| 工具层 | NumPy/SciPy/Matplotlib/PyTorch + ED/DMRG 适配器，统一 `ResearchTool` 接口 |

## DSH 集成方式（重要）

1. **Python SDK 驱动**：qresearch 通过官方 `deepseek-harness-sdk`（stdio JSON-RPC，隔离 `DSH_HOME`）把 DSH 当作"单步执行引擎"——每个需要智能的科研步骤 = 一次 `harness.run()` 轮次。
2. **实验执行不经 LLM**：Experiment Manager 直接 subprocess 跑 Python 工具并记录元数据。
3. **MCP / TS 插件靠后**：MCP server 留给 Phase 6（Tool Builder 自主调科研工具）；TypeScript cordis 插件是最后手段。
4. SDK 不在 PyPI 上，需从源码安装（路径见 `docs/phase0-recon.md` 第 2 节）；版本锁定，当前源码 0.1.5 / npm 0.1.0-rc.6 不同步，以 SDK 配套 runtime 为准。

## 开发铁律（必须遵守）

1. **不改 DSH 核心**：不写 TS 插件、不改 DSH 源码；一切定制经 SDK 参数、profile patch 或外围 Python 包。
2. **科研状态与执行状态分离**：研究问题、假设、计划、实验、证据保存在独立持久层（SQLite + JSON/YAML + Markdown），绝不只存在对话历史或 DSH Session 里。
3. **实验必须可复现**：记录代码版本、参数、环境、依赖、随机种子、日志。
4. **结论必须关联证据**：支持证据、反对证据、置信度、替代解释缺一不可；Agent 必须主动提出反证方式。
5. **禁止伪造与夸大**：不伪造实验结果；不把未经验证的数值趋势表述为"已证明新物理"。
6. **高风险操作需人类审批**：审批门在 qresearch 循环层；交互模式按 Manual → Assisted → Semi-autonomous → Autonomous 逐级放开，第一版半自动（用户逐步确认）。
7. **工具动态发现优先**：先接入已有代码（适配器），只有确实缺能力时才开发新工具，且必须通过软件 + 物理基准验证后才注册。种子工具（scipy Lanczos ED）在 Phase 3 手工开发。
8. **第一版保持简单**：不做完整算法库、不做分布式调度、不做复杂多 Agent。
9. **LLM 结构化输出必须校验**：所有经 SDK 的步骤输出用 Pydantic schema 强校验，不信任自由文本。

## 技术栈

- Python ≥3.10（主语言，Pydantic v2 + 类型标注）、SQLite、YAML/JSON、Markdown、Git
- DSH 接入：`deepseek-harness-sdk`
- 科研工具：NumPy、SciPy、Matplotlib、PyTorch；后续 TeNPy / quimb
- 数值验证协议：差分测试（ED vs DMRG 对照）、golden fixtures、解析解校验（1D Heisenberg：E/L = 1/4 − ln 2 ≈ −0.4431471806）
- 分支开发：代码修改走独立 Git 分支 → 测试 → 物理基准 → 变更报告 → 人工批准合并

## 目录约定

```text
docs/                 # phase0-recon.md 等（侦察与设计文档）
qresearch/            # 科研智能层 Python 包：core / planning / execution / verification / tools / memory / analysis
tools/                # simple_ed、dmrg_adapter、benchmarks
examples/             # 验收演示脚本
tests/                # 所有核心模块必须有测试
research_data/        # projects / experiments / artifacts / reports（运行时产生，git 忽略大文件）
```

## 开发阶段与当前状态

Phase 0 环境与可行性 → 1 Research State → 2 Research Plan → 3 Experiment Manager → 4 Verification Manager → 5 Research Loop → 6 Tool Builder → 7 Research Memory → 8 HPC 与多项目

**当前状态：Phase 0 架构侦察完成（见 docs/phase0-recon.md）。残留：SDK 安装与 smoke test。Phase 1（Research State，纯 Python）计划已就绪，待确认后编码。**

## 每阶段完成的汇报要求

- 修改了哪些文件；为什么这样设计；如何运行；如何测试；已知限制；下一步建议。

## 命令

```bash
# Phase 0 收尾（SDK 安装三候选路径见 docs/phase0-recon.md §2）
uv pip install -e D:/AI/Agent/Try/deepseek-harness/python/sdk

# qresearch（Phase 1 起）
pip install -e .
pytest tests/
```
