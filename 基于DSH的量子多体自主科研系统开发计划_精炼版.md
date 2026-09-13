# 基于 DSH 的量子多体自主科研系统开发计划

## 1. 项目简介

本项目拟开发一个面向量子多体物理、量子模拟和量子计算的 **Quantum Research Agent**。

用户输入科研问题后，系统能够：

1. 理解研究目标；
2. 提出可验证的物理假设；
3. 制定和批评研究计划；
4. 动态发现或开发所需工具；
5. 调用 Python 程序、已有算法和 HPC 资源；
6. 执行数值实验；
7. 进行软件、数值和物理验证；
8. 分析结果并更新假设；
9. 生成下一步研究计划和研究报告。

系统不是预先写死的 ED、DMRG、MPS 或 iPEPS 算法库，而是一个能够围绕科研问题动态组织工具和实验的研究 Agent。

---

## 2. 总体技术路线

采用：

```text
DeepSeek Harness（DSH）
        +
qresearch 科研扩展层
        +
Python 科研工具与实验环境
```

### 2.1 DSH 的职责

DSH 作为底层 Agent Runtime，尽量直接复用：

- 模型调用；
- Agent Loop；
- Shell 和文件操作；
- Git；
- 工具调用；
- Session；
- 日志与执行轨迹；
- 暂停、恢复和重放；
- Sandbox；
- 权限审批；
- 插件机制；
- 基础交互界面。

### 2.2 qresearch 的职责

科研逻辑放在独立的 `qresearch` Python 包中，通过插件接入 DSH。

主要负责：

- Research Project；
- Research Goal；
- Hypothesis；
- Research Plan；
- Plan Critic；
- Research Loop；
- Tool Discovery；
- Tool Builder；
- Experiment Manager；
- Verification Manager；
- Result Analyst；
- Research Memory；
- 多项目管理。

### 2.3 开发原则

- 优先开发插件和外围模块，不直接大规模修改 DSH；
- 只有在插件接口不足时，才对 DSH 做最小化修改；
- DSH Session 与科研状态分离；
- 所有实验必须可复现；
- 所有结论必须关联证据；
- 第一版不追求完全自主和复杂多 Agent；
- 先验证闭环，再扩展算法和算力。

---

## 3. 系统架构

```text
用户 / 研究者
    │
    ▼
Research Agent
    ├── Goal Understanding
    ├── Hypothesis Manager
    ├── Research Planner
    ├── Plan Critic
    ├── Research Loop
    ├── Tool Discovery / Builder
    ├── Experiment Manager
    ├── Verification Manager
    ├── Result Analyst
    └── Research Memory
    │
    ▼
qresearch Plugin / Python Backend
    │
    ▼
DeepSeek Harness（DSH）
    ├── Agent Loop
    ├── Model Adapter
    ├── Shell / File / Git
    ├── Tool Calling
    ├── Session / Replay / Resume
    ├── Sandbox / Approval
    └── UI
    │
    ▼
Python 工具 / 外部代码 / HPC / 文献与知识库
```

---

## 4. 推荐项目结构

不建议把全部代码直接写入 DSH 源码。

```text
projects/
├── deepseek-harness/
│   └── DSH 上游源码
│
└── qresearch/
    ├── pyproject.toml
    ├── README.md
    ├── qresearch/
    │   ├── core/
    │   ├── planning/
    │   ├── execution/
    │   ├── verification/
    │   ├── analysis/
    │   ├── memory/
    │   ├── tools/
    │   └── dsh_plugin/
    ├── tools/
    ├── examples/
    ├── tests/
    ├── research_data/
    └── docs/
```

### 4.1 环境方式

不需要卸载已经安装的 DSH。

建议：

```bash
git clone https://github.com/deepseek-ai/deepseek-harness.git
cd deepseek-harness

python -m venv .venv
source .venv/bin/activate

pip install -e .
```

Windows：

```powershell
python -m venv .venv
.venv\Scripts\activate

pip install -e .
```

建议固定 DSH 的 Git commit，避免上游更新造成接口变化。

---

## 5. 核心数据模型

科研状态不能只保存在 Prompt 或聊天记录中，应独立持久化。

### 5.1 核心对象

```text
Project
 ├── Goal
 ├── Hypothesis
 ├── ResearchPlan
 ├── Experiment
 ├── Evidence
 ├── Tool
 └── Report
```

### 5.2 Research State

```yaml
project_id: heisenberg_001
question: >
  Study the ground-state properties of a quantum many-body model.
hypotheses:
  - id: hyp_001
    statement: ...
    status: proposed
plans:
  - id: plan_001
    status: awaiting_approval
experiments:
  - id: exp_001
    status: completed
evidence: []
next_actions: []
```

### 5.3 状态存储

第一版采用：

```text
SQLite + JSON/YAML + Markdown + Git
```

分离保存：

```text
DSH Session / Event Log
    → 记录 Agent 执行过程

Research Database
    → 记录科研问题、计划、实验、证据和结论
```

---

## 6. Research Agent 工作流

```text
科研问题
   ↓
理解目标与约束
   ↓
生成候选假设
   ↓
制定 Research Plan
   ↓
批评计划与识别风险
   ↓
人类审核
   ↓
发现或开发工具
   ↓
执行实验
   ↓
软件 / 数值 / 物理验证
   ↓
分析结果
   ↓
更新假设与证据
   ↓
判断是否完成
   ├── 是：生成报告
   └── 否：生成下一轮计划
```

第一版建议采用 **Human-in-the-loop**：

- 计划需批准后执行；
- 新工具需测试和审批；
- 高成本实验需批准；
- HPC 任务需受预算约束。

---

## 7. 主要模块

### 7.1 Goal Understanding

从用户问题中提取：

- 研究对象；
- 模型和参数；
- 目标物理量；
- 约束条件；
- 可用资源；
- 成功标准。

### 7.2 Hypothesis Manager

负责：

- 生成候选假设；
- 记录假设来源；
- 设计验证实验；
- 管理支持和反对证据；
- 维护假设置信度；
- 提出反例和替代解释。

### 7.3 Research Planner / Plan Critic

计划器负责制定研究步骤。

批评器检查：

- 是否缺少基准；
- 是否缺少收敛性分析；
- 是否忽略有限尺寸效应；
- 是否存在逻辑跳跃；
- 是否有更低成本的实验；
- 是否把数值趋势误判为物理结论。

### 7.4 Tool Discovery / Tool Builder

工具选择流程：

```text
研究计划
   ↓
确定所需能力
   ↓
搜索工具注册表
   ↓
检查已有代码
   ↓
评估适用范围
   ↓
调用已有工具或开发新工具
```

只有在确实缺少能力时，才调用 Coding Agent 开发新工具。

### 7.5 Experiment Manager

负责：

- 参数管理；
- 本地执行；
- 远程/HPC 执行；
- 日志保存；
- 结果文件管理；
- 失败重试；
- 代码版本记录；
- 实验复现。

每个实验至少保存：

```text
代码版本
参数
依赖环境
随机种子
运行时间
硬件信息
输出文件
日志
验证结果
```

### 7.6 Verification Manager

分为三层：

#### 软件验证

- 单元测试；
- 集成测试；
- 类型检查；
- 异常处理；
- 边界条件；
- 可重复运行。

#### 数值验证

- 收敛性；
- 误差估计；
- 有限尺寸分析；
- 多随机种子；
- 已知结果对照；
- 多方法对照。

#### 物理验证

- 对称性；
- 守恒量；
- 极限情况；
- 量纲；
- 物理可接受范围；
- 竞争解释和反例。

### 7.7 Result Analyst

输出应区分：

```yaml
observations:
  - ...
interpretations:
  - ...
uncertainties:
  - ...
alternative_explanations:
  - ...
recommended_next_steps:
  - ...
```

不能把未经验证的数值趋势直接表述为新物理结论。

### 7.8 Research Memory

第一版记录：

- 项目问题；
- 假设和结论；
- 实验结果；
- 工具接口；
- 方法适用范围；
- 失败案例；
- 验证经验；
- 参数经验。

先使用：

```text
SQLite + Markdown + JSON
```

后续再增加向量检索或知识图谱。

---

## 8. 工具接口

所有科研工具尽量采用统一接口：

```python
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolInput:
    parameters: dict[str, Any]


@dataclass
class ToolOutput:
    results: dict[str, Any]
    artifacts: list[str]
    metadata: dict[str, Any]


class ResearchTool:
    name: str
    version: str

    def validate_input(self, tool_input: ToolInput) -> None:
        ...

    def run(self, tool_input: ToolInput) -> ToolOutput:
        ...

    def verify(self, output: ToolOutput) -> dict:
        ...

    def describe(self) -> dict:
        ...
```

工具描述中应包含：

- 名称和版本；
- 输入输出；
- 适用问题；
- 资源需求；
- 依赖；
- 验证基准；
- 已知限制；
- 示例；
- 运行方式。

---

## 9. 分阶段开发计划

### Phase 0：DSH 可行性验证

目标：确认 DSH 能作为底层执行内核。

任务：

- 运行 DSH；
- 阅读项目结构；
- 找到插件和工具调用入口；
- 找到 Session、Sandbox 和审批机制；
- 完成一次 Shell/Python 工具调用。

验收：

```text
用户问题
 → DSH 调用 Python 脚本
 → 读取输出
 → 生成解释
```

---

### Phase 1：Research State

目标：建立独立科研状态。

任务：

- 定义 Project、Goal、Hypothesis；
- 定义 ResearchPlan、Experiment、Evidence；
- 使用 SQLite 持久化；
- 支持 JSON/YAML 导入导出；
- 实现事件日志；
- 实现关闭后恢复。

验收：

```text
创建项目
 → 保存问题、假设和计划
 → 关闭程序
 → 重新启动并恢复状态
```

---

### Phase 2：Research Plan

目标：生成和审核结构化研究计划。

任务：

- Goal Understanding；
- Hypothesis Manager；
- Research Planner；
- Plan Critic；
- 计划版本管理；
- 人类批准/拒绝/修改。

验收：

```text
科研问题
 → 结构化目标
 → 候选假设
 → Research Plan
 → 用户审核
```

---

### Phase 3：Experiment Manager

目标：执行可复现的数值实验。

任务：

- 工具注册表；
- Python 工具适配器；
- 参数管理；
- 日志和 Artifact 管理；
- 实验状态；
- Git 版本记录；
- 失败重试。

验收：

```text
Research Plan
 → 创建实验
 → 运行 Python 工具
 → 保存结果和日志
 → 生成实验摘要
```

---

### Phase 4：Verification Manager

目标：避免代码错误、数值误差和物理误判。

任务：

- 软件测试；
- 小系统精确基准；
- 收敛性测试；
- 极限情况测试；
- 对称性和守恒量检查；
- 多方法对照；
- 证据链生成。

验收：

```text
实验完成
 → 自动验证
 → 输出通过/失败/不确定
 → 给出原因和建议
```

---

### Phase 5：Research Loop

目标：形成科研闭环。

任务：

- 结果分析；
- 假设更新；
- 计划更新；
- 下一步实验建议；
- 停止条件；
- 报告生成。

验收：

```text
问题
 → 假设
 → 计划
 → 实验
 → 验证
 → 分析
 → 更新假设
 → 下一轮计划
```

---

### Phase 6：Tool Builder

目标：让 Agent 能够开发缺失工具。

任务：

- 生成 Tool Specification；
- 调用 Coding Agent；
- 自动生成测试；
- 运行物理基准；
- 创建 Git 分支；
- 生成变更报告；
- 人工审批后注册。

验收：

```text
发现工具缺口
 → 生成规格
 → Coding Agent 开发
 → 测试和验证
 → 注册工具
```

---

### Phase 7：Research Memory

目标：积累跨项目科研经验。

任务：

- 项目记忆；
- 方法记忆；
- 工具记忆；
- 失败案例；
- 实验摘要；
- Markdown 检索；
- SQLite 索引。

验收：

```text
新项目能够检索并复用旧项目的研究经验
```

---

### Phase 8：HPC 与多项目

目标：支持真实科研工作流。

任务：

- Slurm；
- 远程服务器；
- CPU/GPU 资源管理；
- 任务队列；
- 计算预算；
- 多项目；
- 暂停/恢复；
- 并行实验。

---

## 10. MVP 方案

第一版只实现：

```text
DSH
+ qresearch Plugin
+ Research State
+ SQLite
+ 一个或两个 Python 工具
+ Experiment Manager
+ Verification Manager
+ 人类审批
```

### 推荐验证问题

选择一个简单、可验证的量子多体问题，例如：

> 一维 Heisenberg 模型的基态能量、关联函数和收敛性分析。

流程：

1. 输入科研问题；
2. Agent 生成计划；
3. 用户批准；
4. 调用已有 ED 或 DMRG 工具；
5. 扫描系统尺寸和 bond dimension；
6. 保存结果；
7. 自动绘图；
8. 执行收敛性检查；
9. 与已知结果比较；
10. 生成分析和下一步建议。

重点是验证系统闭环，而不是第一版就实现完整 DMRG 或 iPEPS。

---

## 11. Agent 开发分工

### Claude Code

主要负责：

- 总体架构；
- 阅读和理解 DSH；
- Research State；
- Research Loop；
- 插件开发；
- 长流程调试；
- 系统重构。

### Codex

主要负责：

- 单个模块实现；
- 单元测试；
- 类型检查；
- Bug 修复；
- 代码审查；
- 性能优化。

### DSH

主要负责：

- Agent Runtime；
- 工具调用；
- Shell 和文件；
- Session；
- Sandbox；
- 审批；
- 执行轨迹；
- 模型适配。

推荐组合：

```text
Claude Code：主开发者
Codex：工程协作者
DSH：运行内核
```

---

## 12. 给 Coding Agent 的第一条指令

```text
请先不要编写大量代码。

第一步请完成以下工作：

1. 阅读当前 DeepSeek Harness 项目结构；
2. 找到插件系统、工具调用、Session、Sandbox 和审批机制；
3. 评估哪些功能可以直接复用；
4. 设计基于 DSH 的 qresearch 插件架构；
5. 设计 Research State 数据模型；
6. 给出推荐目录结构；
7. 给出 Phase 1 的最小实现计划；
8. 暂时不要修改 DSH 核心；
9. 先输出架构分析和实施计划，等待确认后再编码。

要求：
- 优先使用插件和外围 Python 包；
- 不要把科研状态只保存在 Prompt 中；
- 不要一开始实现完整 ED/DMRG/iPEPS；
- 所有实验和结论必须可追溯、可验证；
- 输出文件、接口、测试方法和已知限制。
```

---

## 13. 主要风险与控制

### 风险 1：过早追求完全自主

控制方式：

```text
Manual
 → Assisted
 → Semi-autonomous
 → Autonomous
```

### 风险 2：把 Code Agent 当作 Research Agent

Code Agent：

```text
修改代码 → 测试通过 → 结束
```

Research Agent：

```text
问题 → 假设 → 计划 → 实验 → 验证
→ 分析 → 更新假设 → 下一轮研究
```

### 风险 3：过早开发完整算法库

控制方式：

- 优先适配已有代码；
- 研究中出现工具缺口时再开发；
- 所有新工具必须有测试和物理基准。

### 风险 4：科研结论幻觉

控制方式：

- 强制证据链；
- 强制收敛性检查；
- 强制替代解释；
- 输出不确定性；
- 高风险结论需要人工审核。

---

## 14. 最终路线

```text
第一阶段：
DSH
+ qresearch Plugin
+ Research State
+ SQLite
+ 一个 Python 工具
+ 人类审批

第二阶段：
Research Planner
+ Hypothesis Manager
+ Experiment Manager
+ Verification Manager
+ Result Analyst

第三阶段：
Tool Discovery
+ Tool Builder
+ Coding Agent
+ Research Memory

第四阶段：
HPC
+ 多项目
+ 任务调度
+ 长期运行

第五阶段：
多 Agent 协作
+ 自动提出研究方向
+ 自动寻找反例
+ 自动设计高信息增益实验
+ 自动形成研究报告
```

## 核心判断

本项目的核心壁垒不是某一个 Code Agent，也不是预先实现多少物理算法，而是：

```text
科研状态建模
+ 假设与证据管理
+ 动态研究计划
+ 工具发现与开发
+ 软件/数值/物理验证
+ 长期研究记忆
+ 人类与 Agent 协作
```

最终目标：

> 让 DSH 成为执行内核，让 qresearch 成为科研智能层，逐步形成一个能够真正参与量子多体物理研究的自主科研系统。
