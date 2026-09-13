# 基于 DSH 的量子多体自主科研系统开发计划 v2

> 本版是《基于DSH的量子多体自主科研系统开发计划_精炼版》的**完整修订**，吸收 2026-09-13 对 DSH 源码的 Phase 0 侦察结论（`docs/phase0-recon.md`）与后续架构评审共识。与精炼版冲突处以本文为准。

**相对精炼版的主要修订**：
① 环境方式重写——DSH 实为 TypeScript/Cordis 项目 + 官方 Python SDK，精炼版 4.1 的 `pip install -e deepseek-harness` 不成立；
② 接入拓扑反转——qresearch（Python）主导确定性科研主循环，经 SDK 把 DSH 当"单步执行引擎"，而非"qresearch 做成 DSH 插件"；
③ 新增决策问责机制——Decision 升为核心数据对象，自主性按决策类别逐步挣得；
④ 验证协议具体化——Spec 先行、出题人≠答题人、程序/算法/算符物理三层基准；
⑤ 新增上下文管理设计——上下文是状态的投影；
⑥ Phase 3 补种子工具，消除"Phase 3 要工具、Phase 6 才造工具"的鸡生蛋问题；
⑦ 目标三档校准，明确承诺边界。

---

## 0. 目标校准（先说清楚能做到什么）

| 档位 | 内容 | 判断 |
|---|---|---|
| 一 | 可审计、可复现、半自动的科研流水线（Phase 0–5 闭环） | **确定**，工程问题，本计划的验收范围 |
| 二 | 对已有成熟工具的问题做多轮半自主研究（扫描、标度、复现、对照） | 大概率，依赖工具覆盖与基准质量 |
| 三 | 自主发现新物理、自创新算法 | **不承诺**，作长期探索方向，不作为任何阶段验收标准 |

系统的定位：**让机器可靠承担科研中可验证的部分，人保留判断权**。系统价值不依赖第三档成立。

---

## 1. 项目简介

本项目开发一个面向量子多体物理、量子模拟和量子计算的 **Quantum Research Agent**。

用户输入科研问题后，系统能够：

1. 理解研究目标；
2. 提出可验证的物理假设；
3. 制定和批评研究计划；
4. 动态发现或开发所需工具；
5. 调用 Python 程序、已有算法和 HPC 资源；
6. 执行数值实验；
7. 进行软件、数值和物理三层验证；
8. 分析结果并更新假设；
9. 生成下一步研究计划和研究报告。

系统不是预先写死的 ED、DMRG、MPS 或 iPEPS 算法库，而是一个围绕科研问题动态组织工具和实验的研究 Agent。**它不是固定算法库，也不是"物理大模型+若干工具"——它是带账本、带门卫的科研状态机。**

---

## 2. 总体技术路线

### 2.1 DSH 实际形态（Phase 0 核实）

- DeepSeek Harness（DSH）是 **TypeScript / Cordis 插件式 Agent 运行时**："不存在需要打补丁的特权内核"，模型适配器、工具注册表、agent loop、会话日志皆为插件；
- 本机安装：npm 包 `@deepseek-ai/dsh@0.1.0-rc.6`；源码：`D:\AI\Agent\Try\deepseek-harness`（0.1.5 sync，commit `c291e79`）。两者不同步，**以 SDK 配套 runtime 为准、锁版本**；
- 体系：profile = 有序 bundles + `cordis.patch.yml` patch 层；随附 profile：`web` / `headless` / `sdk` / `sdk-minimal` / `acp`；`dsh-base` 含沙箱与审批策略；
- **官方 Python SDK**：`deepseek-harness-sdk` + `deepseek-harness-runtime-bin`（wheel 内含 dsh CLI，运行不需要系统 Node.js）。API：`DeepSeekHarness(dsh_home=必须显式, cwd, provider="deepseek-official", model)` → `.run(prompt, session_id)` → `RunResult(final_response, finish_reason, events, notifications)`。

### 2.2 接入拓扑（反转，本版核心决策）

```text
qresearch（Python）主导确定性科研主循环
        │  deepseek-harness-sdk（stdio JSON-RPC，隔离 DSH_HOME）
        ▼
DSH runtime（dsh --profile sdk）＝ 单步执行引擎
        │
        ▼
DeepSeek 模型 + 沙箱执行
```

理由：

1. 研究主循环本质是确定性状态机（跨天、可中断、可恢复、需审批门），应由代码而非模型即兴驱动；
2. 科研状态与执行状态分离原则要求状态机与 LLM 循环解耦；
3. 直接规避"把 Code Agent 当 Research Agent"风险——模型自己开循环时，状态完整性、审批门、错误恢复都退化为模型的口头承诺；
4. **实验执行不经 LLM**：Experiment Manager 直接 subprocess 跑 Python 工具并记录元数据；DSH 只承担需要智能的步骤（理解、假设、计划、批评、分析、写码）。

分工原则一句话：**自主性下沉到"步内"，确定性守住"步间"。**

MCP server 留给 Phase 6（让 DSH 的 agent 在轮次内自主查询/注册科研工具）；TypeScript cordis 插件是最后手段。

### 2.3 环境方式（替换精炼版 4.1）

DSH 源码 clone 仅作阅读参考，不参与安装。Python SDK 不在 PyPI 发布（源码内 `0.0.0.dev0`，runtime-bin 为本地 path 依赖），三条安装路径按序尝试：

```bash
# A（首选）：uv 解析 path 依赖
uv venv && uv pip install -e D:/AI/Agent/Try/deepseek-harness/python/sdk

# B：pip 装 SDK 纯 Python 部分 + dsh_bin 指向 npm 已装的 dsh
pip install -e D:/AI/Agent/Try/deepseek-harness/python/sdk
#   DeepSeekHarness(dsh_bin=".../dsh", ...)

# C：按 python/development.md 构建运行时产物后安装（最重）
```

其余要求：

- **隔离 DSH_HOME**（如 `research_data/dsh_home/`），科研数据不进用户全局 DSH home（SDK 本身强制显式 dsh_home）；
- `DEEPSEEK_API_KEY` 经环境变量或 SDK `api_key` 参数传入，不入库；
- Windows 平台可用性是 Phase 0 收尾的硬验收。

### 2.4 版本与可替换性

- qresearch 与 DSH 之间只有**一个薄封装**（`qresearch/dsh_client.py`），锁 SDK+runtime 版本；
- 该封装是可替换点：DSH 装不通或 rc 接口漂移时，可换运行时甚至裸 API+最小工具循环，科研层不动。**harness 不构成壁垒，也不构成单点依赖。**

---

## 3. 系统架构

```text
用户 / 研究者（审批、修改、终审判断）
    ↕
┌─ qresearch（Python 包，本项目全部代码）──────────────┐
│ ① 账本（状态层）                                    │
│    Project/Goal/Hypothesis/Plan/Experiment/         │
│    Evidence/Decision/Tool/Report → SQLite+YAML      │
│ ② 流程层（状态机）                                   │
│    UNDERSTAND→HYPOTHESIZE→PLAN→APPROVE(人)→         │
│    EXECUTE→VERIFY→ANALYZE→DECIDE→(下一轮|REPORT)    │
│ ③ 步骤层（站点执行器 + Action 执行器注册表）          │
│    每个执行器 = prompt投影 + 输出schema + 校验 + 落账 │
│   Experiment Manager 直接 subprocess 跑工具（不经LLM）│
│    Verification Manager 跑基准套件（纯 Python）       │
└─────────────────────────────────────────────────────┘
    ↕ deepseek-harness-sdk（薄封装 dsh_client.py）
DSH runtime ── DeepSeek 模型 + 沙箱 + 会话日志(fork/resume)
```

### 3.1 DSH 职责映射

| 精炼版中 DSH 的职责 | 实际机制 | 复用方式 |
|---|---|---|
| 模型调用 | `ctx.llm` + `deepseek-official` 适配器 | SDK `provider`/`model` |
| Agent Loop | agent-loop 插件 | 每站点一次 `harness.run()` 轮次；步内多步工具循环由它管 |
| Shell/文件/Git | `dsh-tool-bash`/`dsh-tool-fs` 等 | profile 默认带 |
| Session | JSONL 会话日志，fork/resume | `session_id` 按项目:站点:尝试命名 |
| Sandbox | `ctx.sandbox`（`dsh-pwsh-sandbox`） | profile patch 配置 |
| 审批 | dsh-base 审批策略 | 步间审批门在 qresearch 层；DSH 内策略 Phase 3 验证 |
| 插件机制 | cordis + profile/patch | 不写 TS；MCP 留 Phase 6 |
| UI | `dsh web` | 可选交互审阅 |

---

## 4. 项目组织与环境

本仓库（`DSH-TNW-Physicist`）即 qresearch 仓库：

```text
├── agent.md                         # Coding Agent 工作说明
├── docs/                            # phase0-recon.md、设计文档
├── 基于DSH的量子多体自主科研系统开发计划_精炼版.md   # 原始计划（存档）
├── 基于DSH的量子多体自主科研系统开发计划_v2.md      # 本文件（权威）
├── pyproject.toml
├── qresearch/
│   ├── core/        # models.py status.py storage.py events.py
│   ├── dsh_client.py            # 对 SDK 的唯一封装点
│   ├── stations/    # 各站点执行器（understand/hypothesize/plan/analyze/decide…）
│   ├── execution/   # experiment_manager.py local_executor.py artifact_manager.py
│   ├── verification/# verifier.py benchmarks/ 差分与守恒检查
│   ├── tools/       # registry.py specification.py adapters/
│   ├── memory/      # method_memory.py tool_memory.py failure_cases.py
│   └── reports/     # report_generator.py
├── tools/           # simple_ed/ dmrg_adapter/ benchmarks/(golden fixtures)
├── examples/        # resume_demo.py research_loop_demo/
├── tests/
└── research_data/   # projects/ experiments/ artifacts/ reports/ dsh_home/（运行时产生，大产物 git 忽略）
```

配置：YAML（`configs/default.yaml` + `local.yaml`）；Git 分支开发；所有 LLM 结构化输出经 Pydantic v2 强校验。

---

## 5. 核心数据模型

科研状态独立持久化（SQLite + YAML/JSON + Markdown + 事件日志），绝不只存 Prompt 或会话记录。

### 5.1 对象清单

```text
Project ── Goal（success_criteria、constraints、预算）
        ── Hypothesis（statement、status、supporting/contradicting evidence、falsification tests）
        ── ResearchPlan（version 链，steps: list[Action]，每步含目的/输入/工具/预期输出/成本/是否需审批）
        ── Experiment（tool_id、参数、代码版本、环境、随机种子、artifacts、verification_status）
        ── Evidence（type、source_experiment、claim、confidence、artifacts）
        ── Decision（新增，一等公民，见 5.2）
        ── Tool（ToolRecord：接口、版本、基准清单、已知限制）
        ── VerificationReport（逐项基准的 pass/fail/不确定 + 原因）
        ── Report
```

假设状态：`proposed → under_test → supported / weakened / rejected / accepted_provisionally`。
计划状态：`draft → awaiting_approval → approved → executing → completed/superseded`。

### 5.2 Decision（决策问责对象）

流程中"没有绝对标准、需要模型判断"的场合（是否迭代/终止、是否改计划、新方案与评价），一律产出结构化 Decision：

```yaml
decision_id: dec_007
type: iterate_or_terminate        # 或 replan / new_proposal / proposal_review / declare_result
recommendation: iterate
checklist:                        # 能挂证据的 claim 必须挂
  - claim: 能量在 bond dimension 上收敛
    status: passed
    evidence: exp_014
  - claim: hyp_002 已被检验
    status: untested              # 模型被迫暴露缺口
    reason: 需要 L=32
info_gain_estimate: moderate
alternatives_considered: [...]
made_by: model                    # model / human / rule
requires_human: true              # 初期一律 true，按决策类别逐步放开
```

状态机只做机械事：校验结构、核对 evidence 引用真实存在、发现 checklist 有 `untested` 项即拒绝"终止"、预算超限硬停。**模型负责判断，状态机负责让判断可问责并机械执行。** 判断错误的现实目标是"可见"，不是"杜绝"。

### 5.3 事件日志

追加式 JSONL：谁（model/human/rule）、何时、把哪个对象从什么状态改到什么状态、依据哪个 Decision/Evidence。恢复与审计都从它走。

---

## 6. Research Agent 工作流

### 6.1 状态机（固定骨架）

```text
科研问题
   ↓
UNDERSTAND      产出 Goal（成功标准、约束、预算）
   ↓
HYPOTHESIZE     产出/更新假设，每条必须自带证伪试验（无证伪方式不入库）
   ↓
PLAN            生成 plan vN（步骤列表），critic 攻击
   ↓
APPROVE（人）    展示 diff，批/改/拒
   ↓
EXECUTE         按计划逐步执行 Action（实验直接 subprocess，可批量扫描）
   ↓
VERIFY          三层基准（程序/算法/算符物理），不过关的实验取消证据资格
   ↓
ANALYZE         结果解读，逐条挂 evidence
   ↓
DECIDE          迭代/终止/改计划/新方案 → Decision 对象
   ├─ 终止且 checklist 全绿 + 人确认 → REPORT
   └─ 否则 → 回到 PLAN（携带 Decision 与失败方向清单）
```

### 6.2 固定吗？三层回答

| 层 | 是否固定 | 说明 |
|---|---|---|
| 顶层骨架（站点顺序） | **固定** | 科研方法论的形状；改它=改代码+评审，因为恢复与审计依赖它 |
| 每轮做什么 | **不固定** | 计划是数据：模型生成 steps，critic 攻击，人批准，执行器按数据驱动 |
| 动作词汇表 | **开放** | 新能力=注册新 Action 执行器；Phase 6 本质是给词汇表加动作 |

### 6.3 LLM 的三种合法形态与一条铁律

1. **生成器**：text → 结构化对象（Goal/假设/计划/解读），必须过 schema；
2. **工具型 agent**：步内自主多步（跑绘图脚本、翻 CSV、写码迭代）——自由度在 DSH 的 loop 里；
3. **批评者**：对抗性检查他者产出（plan critic、hypothesis attacker、diff vs Spec 审查）。

铁律：**LLM 只提议，代码记账。** 模型输出永远是提案，经校验器、由执行器落账后才成为状态。模型是证人，代码是书记员和门卫。

### 6.4 自主性按决策类别挣得

Manual → Autonomous 不是全局开关，是**每类决策一个开关**：

| 判断 | 可靠机制 | 初期拍板人 |
|---|---|---|
| 迭代/终止 | Goal 成功标准 + Decision checklist + 预算硬闸（轮数/算力/信息增益衰减） | 人（终止低频高险） |
| 是否改计划 | 计划版本 diff，必须引用触发 evidence | 人批 diff |
| 新方案 | Generator–Critic 分离（不同会话/模型/温度） | 人选方向 |
| 方案评价 | 量纲评分：信息增益/成本/可证伪性/风险 | 人终审 |
| 宣称"新物理" | 永远人工：证据链+收敛+替代解释齐全才进报告，措辞"一致于"而非"证明" | **人，无条件** |

初期 Manual 模式下模型只出建议、人拍板，同时积累"模型建议 vs 人决定"的分歧记录；分歧率低的类别先放开（如"继续迭代"）。**某类判断若设计不出 schema，显式标 `requires_human: always`，不硬造。**

---

## 7. 主要模块

### 7.0 dsh_client（对 SDK 唯一封装）

```python
def call_station(station, state, schema, prompt_builder, retries=1):
    prompt = prompt_builder(state)                 # 从状态库投影上下文
    for attempt in range(retries + 1):
        out = harness.run(prompt + SCHEMA_INSTRUCTIONS[schema],
                          session_id=f"{state.project_id}:{station}:a{attempt}")
        try:
            return schema.model_validate_json(out.final_response)
        except ValidationError as e:
            prompt += f"\n上次输出不合法：{e}，请修正。"
    raise NeedsHuman(station, out)
```

错误处理三分类：LLM 输出不合法 → 重试一次 → `needs_human`；实验进程失败 → `experiment.status=failed` + 重试策略（计划指定）；运行时/API 错误 → DSH 层自处理，异常透传。

### 7.1–7.4 站点执行器

UNDERSTAND / HYPOTHESIZE / PLAN(+CRITIC) / APPROVE：职责同精炼版 7.1–7.3，补充：PLAN 输出必须是与上一版本的 **diff**（改了哪步、为什么、触发证据）；critic 检查表沿用精炼版（缺基准、缺收敛、有限尺寸、逻辑跳跃、更便宜实验、数值误判物理）。

### 7.5 Experiment Manager

- Action 执行器注册表（`run_experiment` / `parameter_scan` / `compare_benchmark` / `plot` / …）；
- **批量执行**：一个 `parameter_scan` 派多个 subprocess，结果落带索引的表；
- 每个实验落账：代码版本、参数、环境、种子、时间、硬件、输出、日志、验证状态；
- 失败也落账；支持重试与复现（同参数同种子必复现）。

### 7.6 Verification Manager（三层协议）

| 层 | 检查 | 具体手段 |
|---|---|---|
| 程序层 | 代码可靠 | 单测、类型检查、边界（L=2、g=0）、异常路径、同种子可重复 |
| 算法层 | 数值收敛、不变量 | bond dimension/步长外推差值；厄米性、能量方差、变分上界 E_var≥E₀；误差标度行为合理 |
| 算符/物理层 | 物理正确 | [H,S_tot]=0 数值验证；g→0、g→∞ 极限对已知解；小系统与 scipy `eigsh`/TeNPy **独立实现差分**；特殊点对 Bethe ansatz（E/L=¼−ln2≈−0.4431472）；量纲量级 sanity |

输出 `VerificationReport`：逐项 pass/fail/不确定 + 原因与建议；**不过关的实验取消证据资格**。

### 7.7 Result Analyst

输出五段：observations / interpretations / uncertainties / alternative_explanations / recommended_next_steps；每条解读挂 evidence id；禁止把未验证趋势表述为物理结论。

### 7.8 Context Manager（新增）

原则：**上下文是状态的投影，不是历史的堆积。**

- 每个站点 prompt 由 qresearch 从 SQLite 现算：Goal + 活跃假设（含证据计数）+ 当前计划摘要 + 上轮 Decision + 失败方向清单，各带尺寸预算；
- 原始数据文件永不进 prompt，只给路径+摘要；模型需要细节用工具现读（按需付费）；
- 会话策略：跨轮次开新 session（真相在库里，session 可随时扔）；仅"编码-修复"循环内复用 session；
- Research Memory（Phase 7）按需检索注入。

### 7.9 Research Memory

四层（项目/方法/工具/失败案例），先 SQLite+Markdown，后续向量检索。失败案例库显式记录"哪些实验没有信息增益"——是 DECIDE 与下一轮 PLAN 的固定输入。

---

## 8. 工具接口与验证协议

### 8.1 ResearchTool 接口

沿用精炼版第 8 节（`ToolInput`/`ToolOutput`/`ResearchTool.validate_input/run/verify/describe`）。

### 8.2 Spec 先行（写码之前先定考题）

任何新工具，先有 Tool Spec：接口契约 + 输入输出 schema + **机器可判定的基准清单（含容差与 golden 数值）**。"物理正确性"由基准清单操作化定义，不靠感觉。**出题人≠答题人**：golden fixtures 来自文献与可信第三方库，预先入库、版本化，编码 agent 无权修改——模型不能给自己出卷，也不能改答案。

### 8.3 种子工具（Phase 3 交付，消除鸡生蛋）

- `simple_ed`：scipy 稀疏 Lanczos（`eigsh`），L≤16，对称性可选；golden：L=4–16 精确值 + Heisenberg 热力学极限对照；
- `dmrg_adapter`：接入 quimb 或 TeNPy；基准：与 simple_ed 在小系统差分；
- 注册即带：版本、适用范围、基准清单、已知限制。

---

## 9. 分阶段开发计划

### Phase 0：DSH 可行性（侦察已完成，收尾中）

已完成：DSH 形态核实、扩展机制确认（见 `docs/phase0-recon.md`）。
残留：SDK 安装（2.3 三路径）→ smoke test `harness.run("Say hi.")` 返回 `final_response`。
**验收：Windows 上 SDK 跑通一次真实轮次。**

### Phase 1：Research State（纯 Python，不依赖 SDK）

- `qresearch/core/`：models.py（Pydantic 全对象）、status.py（状态机枚举）、storage.py（SQLite）、events.py（JSONL 事件日志）；
- 导入导出 YAML/JSON；
- **验收：创建项目→保存→关闭→重启→恢复全部状态**（`examples/resume_demo.py`）+ pytest 全绿。

### Phase 2：Planning 站点

- UNDERSTAND / HYPOTHESIZE / PLAN / CRITIC 执行器 + `call_station` 封装 + Decision 模型；
- 计划版本 diff 与审批接口（CLI 起步）；
- **验收：科研问题 → 结构化 Goal → 候选假设（各带证伪试验）→ plan v1 → 人批 → Decision 链可追溯。**

### Phase 3：Experiment Manager + 种子工具

- Action 注册表、批量扫描、artifact 管理、复现元数据；
- `simple_ed` + golden fixtures 入库；
- **验收：批准的计划 → 自动建实验 → 批量跑 → 落账 → 实验摘要。**

### Phase 4：Verification Manager

- 三层基准套件（7.6 表落地）+ VerificationReport + 证据资格门；
- **验收：注入一个故意写错的工具能被抓住并出具报告。**

### Phase 5：Research Loop 闭环

- ANALYZE / DECIDE 站点 + Decision checklist + 预算闸 + 报告生成；
- 按类自动化第一步：放开"迭代/继续"类（checklist+预算自动走）；
- **验收：MVP 问题全自动走完 3 轮（人工只批计划与终止），全程可回放。**

### Phase 6：Tool Builder

流程：缺口 → Tool Spec（人审核）→ DSH 编码（分支上：实现+单测）→ 基准套件自动跑（失败回喂修复，上限 N）→ 批评者 session 审查 diff vs Spec → diff 报告 → 人工批准 → 注册。
铁律：出题人≠答题人；golden fixtures 与容差变更走独立人工评审。
**验收：一个真实工具缺口被自动补齐并通过三层验证。**

### Phase 7：Research Memory

四层记忆 + 失败案例库 + 检索注入。
**验收：新项目能检索复用旧项目经验（含"哪些实验没信息增益"）。**

### Phase 8：HPC 与多项目

Slurm/远程、预算、队列、多项目、暂停恢复、并行实验。

### 每阶段完成的汇报要求

修改了哪些文件；为什么这样设计；如何运行；如何测试；已知限制；下一步建议。

---

## 10. MVP

```text
qresearch（Phase 1–5 交付物）
+ DSH runtime（SDK 驱动）
+ simple_ed + dmrg_adapter
+ 人类审批（Manual 模式）
```

**验证问题**：一维 Heisenberg 模型基态能量、关联函数与收敛性分析。

流程：输入问题 → UNDERSTAND → 假设 → 计划 → 人批 → `parameter_scan`（L 与 bond dimension 网格）→ 三层验证（含 Bethe ansatz 对照）→ 收敛分析 → Decision → 报告（全证据链）。

重点不是这个问题本身，而是验证闭环与**判断问责机制**在真实数据上可运转。

---

## 11. 开发分工

| 角色 | 职责 |
|---|---|
| Claude Code | 主开发：架构、qresearch 全模块、长流程调试 |
| Codex | 工程协作：单模块、单测、类型检查、Bug 修复、评审 |
| DSH（runtime） | 执行内核：模型适配、步内 agent loop、工具、沙箱、会话 |
| 人 | 计划审批、终止确认、新物理终审、golden fixtures 评审 |

---

## 12. 给 Coding Agent 的当前指令

```text
Phase 0 已完成侦察（见 docs/phase0-recon.md），当前两件事：

1. Phase 0 收尾：
   - 按 2.3 路径 A 安装 deepseek-harness-sdk（失败依次试 B、C）；
   - smoke test：隔离 DSH_HOME + harness.run("Say hi.") 返回 final_response；
   - 记录 DEEPSEEK_API_KEY 配置方式与实际可用模型 ID。

2. Phase 1 编码（不依赖 SDK，可与 1 并行）：
   - pyproject.toml（Python ≥3.10，pydantic≥2）；
   - qresearch/core/{models,status,storage,events}.py 按第 5 节；
   - examples/resume_demo.py + tests/；
   - 完成后按 9 节汇报要求输出。

约束：不写 TypeScript；不改 DSH 源码；结构化输出必须过 Pydantic；
不伪造任何实验数值；research_data 大产物不入库。
```

---

## 13. 主要风险与控制（修订）

| 风险 | 控制方式 |
|---|---|
| 过早追求完全自主 | Manual→Autonomous **按决策类别**逐级放开（6.4） |
| 把 Code Agent 当 Research Agent | 拓扑反转：确定性状态机守步间，模型自主性限步内（2.2） |
| 过早开发完整算法库 | 适配器优先；种子工具最小化；缺口驱动 |
| 科研结论幻觉 | 证据链强制 + 三层验证 + 证据资格门 + "新物理"无条件人工 |
| LLM 判断不可靠 | Decision 问责（checklist+证据引用+批评者分离）；分歧率数据决定放开节奏（5.2/6.4） |
| SDK 安装失败（Windows） | 三条备选路径（2.3）；dsh_bin 兜底；封装层可换运行时（2.4） |
| DSH rc 版接口漂移 | 锁版本 + 唯一封装点 + 冒烟测试纳入回归 |
| 上下文失控 | 状态投影 + 尺寸预算 + session 可丢弃（7.8） |
| 范围失控 | MVP 纪律：每阶段先问"能不能砍" |

---

## 14. 最终路线

```text
阶段一：DSH(SDK 驱动) + Research State + SQLite + 种子工具 + 人类审批
阶段二：Planner + Hypothesis Manager + Experiment/Verification Manager + Analyst
阶段三：Tool Discovery/Builder + Research Memory
阶段四：HPC + 多项目 + 任务调度 + 长期运行（预算闸内）
阶段五：多 Agent 协作 + 自动研究方向（仅作探索，不作为验收）
```

---

## 15. 设计原则速查

1. **确定性骨架 + 数据驱动计划 + 开放动作词汇**；
2. **LLM 只提议，代码记账**（生成器/工具型 agent/批评者三种合法形态）；
3. **Spec 先行，出题人≠答题人**（正确性=基准清单，编码者无权改考卷）；
4. **上下文=状态的投影**（prompt 从账本现算，session 可丢弃）；
5. **自主性按决策类别挣得**（Decision 落账，分歧率决定放开）；
6. **实验不经 LLM**（subprocess 直跑，元数据落账）；
7. **结论必须挂证据，未验证趋势不得表述为物理结论**。

## 核心判断

本项目的核心壁垒不是模型、不是算法库、不是 harness，而是：

```text
科研状态建模 + 假设与证据管理 + 决策问责机制
+ Spec 先行的三层验证协议 + 上下文投影 + 人机协作界面
```

最终目标：

> 让 DSH 做执行内核，让 qresearch 做科研智能层——机器可靠承担可验证的部分，人保留判断权，逐步形成能真正参与量子多体物理研究的科研系统。
