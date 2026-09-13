# Phase 0 侦察报告：DSH 实际形态与 qresearch 集成架构

日期：2026-09-13。侦察对象：本机 npm 安装（`@deepseek-ai/dsh@0.1.0-rc.6`）+ 源码 clone（`D:\AI\Agent\Try\deepseek-harness`，master @ `c291e79`，2026-09-10，release-0.1.5-sync）。

**结论一句话：DSH 是 TypeScript/Cordis 插件式 Agent 运行时，官方提供 Python SDK；qresearch 应以 Python 主导科研主循环、经 SDK 把 DSH 当作"单步执行引擎"驱动，而不是把 qresearch 做成 DSH 插件。精炼版计划的方向保留，4.1 节环境方式和接入拓扑需要修正。**

---

## 1. DSH 实际是什么（源码核实）

- **Cordis 插件框架**："不存在需要打补丁的特权内核"，一切皆插件：模型适配器、工具注册表、会话日志、agent loop 本身（`docs/architecture.zh.md`）。扩展 = 把插件挂到其他插件旁边。
- **Profile 体系**：profile = 有序 bundles 叠加 + 用户 `cordis.patch.yml` patch 层。随附 profile：`web` / `headless` / `sdk` / `sdk-minimal` / `acp`。`dsh-base` 是共享第一层：模型适配器、工具、持久化、**沙箱与审批策略**、设置、凭据。
- **配置叠加顺序**：bundles（按序）→ profile patch → home patch → `--patch` overlay。`dsh --profile <p> --dump-config` 可检查合成结果。
- **版本状态**：npm 已发布 `0.1.0-rc.6`（本机已装）；源码 master 为 0.1.5 sync。**精炼版"固定 Git commit"应改为"固定 npm / wheel 版本"。**

### 扩展点速查（来自 architecture.md）

| 需求 | 机制 |
|---|---|
| 加面向模型的工具 | `ctx.tools` 注册，schema 进提示词组装；或外接 **MCP server**（`dsh-mcp-client`，无需写 TS） |
| 加模型提供方 | `ctx.llm` 适配器（内置 `deepseek-official`） |
| 拦截请求/工具/轮次 | `agent/*`、`tools/*` 事件 |
| 沙箱 | `ctx.sandbox` 后端（`dsh-pwsh-sandbox` 等） |
| 后台工作 | `ctx.jobs` |
| 同会话目标 | `ctx.goals` |
| 持久会话状态 | 扩展 `SessionEventMap`；JSONL 会话日志支持 fork/resume/migration |
| 深度定制 | 写 TypeScript cordis 插件，经 `dsh plugin add file:...` 安装进 profile |

## 2. 官方 Python SDK（改变集成方式的关键事实）

`python/sdk`（`deepseek-harness-sdk`）+ `python/sdk-runtime`（`deepseek-harness-runtime-bin`，wheel 内含 dsh CLI 与原生伴随文件，**运行不需要系统 Node.js**）。

- API 形态：`DeepSeekHarness(dsh_home=..., cwd=..., provider="deepseek-official", model=...)` → `harness.run(prompt, session_id=...)` → `RunResult(final_response, finish_reason, events, notifications)`。
- **每次启动必须显式指定 `dsh_home`**（不会静默读 `~/.dsh`）→ 天然支持隔离的科研专用 home。
- `patches=(...)` 支持单次调用的 patch overlay；`dsh_bin` 可指向其他 dsh 可执行文件；自定义插件经 `dsh plugin --profile sdk add file:...` 持久安装。
- 源码面很小：`api.py / client.py / models.py / errors.py`。**SDK 源码中没有 "approval"** —— 审批策略在 dsh-base/profile 层配置，SDK API 未显式暴露审批回调（Phase 3 需验证；过渡方案：审批门放在 qresearch 循环层）。
- **获取途径（已核实）**：PyPI 上没有 `deepseek-harness-sdk`；源码内版本 `0.0.0.dev0`，`runtime-bin` 是本地 path 依赖（`uv.lock`，hatchling 构建）。安装候选路径：
  1. 按 `python/development.md` 构建运行时产物后本地安装（重，需要 pnpm build monorepo）；
  2. `uv sync` / `uv pip install -e python/sdk`（uv 会解析 path 依赖，最可能可行）；
  3. `pip install -e python/sdk` + `dsh_bin` 指向 npm 已装的 `dsh`（绕过 runtime wheel，需验证）。

## 3. 对精炼版计划的三处修正

1. **4.1 环境方式作废**：DSH 是 TS 项目，"clone + python venv + pip install -e ." 不成立。qresearch 侧通过 Python SDK 使用 DSH；源码 clone 仅作阅读参考，不参与安装。
2. **集成拓扑反转**：原设想"DSH 为主、qresearch 插件接入"→ 改为"**qresearch（Python）主导确定性科研主循环，DSH 经 SDK 作为执行引擎被驱动**"。理由：
   - 计划第 6 节的 `research_loop` 伪代码本身是确定性 Python 状态机，应由 Python 掌控流程；
   - "科研状态与执行状态分离"原则要求状态机与 LLM 循环解耦；
   - 人类审批门（Manual 模式）放在 Python 步骤之间最自然；
   - 直接规避计划自列的风险 2（把 Code Agent 当 Research Agent）。
   MCP 保留为 Phase 6（Tool Builder 让 DSH 的 agent 在轮次内自主查询/注册工具）；TS cordis 插件是最后手段。
3. **实验执行不经 LLM**：Experiment Manager 直接以 subprocess 执行 Python 工具并记录元数据（参数、版本、种子、日志）。DSH 只承担需要智能的步骤（理解、假设、计划、批评、分析、报告）与需要 agent 的步骤（Phase 6 开发工具）。

## 4. 职责映射（精炼版 2.1 ↔ DSH 实际机制）

| 计划中 DSH 的职责 | DSH 实际机制 | 复用方式 |
|---|---|---|
| 模型调用 | `ctx.llm` + `deepseek-official` 适配器 | SDK `provider`/`model` 参数 |
| Agent Loop | agent-loop 插件 | 每科研步骤 = 一次 `harness.run()` 轮次 |
| Shell/文件/Git | `dsh-tool-bash` / `dsh-tool-fs` 等 | 内置，profile 默认带 |
| Session | JSONL 会话日志，fork/resume | `session_id` 按实验/计划命名 |
| Sandbox | `ctx.sandbox`（`dsh-pwsh-sandbox`） | profile patch 配置 |
| 审批 | dsh-base 审批策略 | Phase 3 验证 SDK 暴露情况；过渡期在 qresearch 层做门 |
| 插件机制 | cordis + profile/patch | 不写 TS；用 MCP（Phase 6） |
| UI | `dsh web` | 交互式审阅可用（可选） |

## 5. qresearch 目标架构

```text
用户 / 研究者（审批、修改、报告）
    ↕
qresearch（Python 包，本项目全部代码）
 ├ Research Loop ── 确定性状态机（计划第 6 节伪代码的直接实现）
 ├ Research State ── SQLite + YAML/JSON，独立于会话持久化
 ├ Goal Understanding / Hypothesis Manager / Planner / Critic / Analyst
 │    └─ 每步 = 一次 SDK 轮次（结构化输出用 Pydantic 校验）
 ├ Experiment Manager ── 直接 subprocess 执行工具，不经 LLM
 ├ Verification Manager ── 纯 Python 检查器 + golden 基准 + 差分测试
 └ Tool Registry / Discovery
    ↕ deepseek-harness-sdk（stdio JSON-RPC，隔离 DSH_HOME）
DSH runtime（dsh --profile sdk）
    ↕
DeepSeek 模型 + 沙箱执行
```

## 6. Phase 0 残留项与风险

- [ ] SDK 安装（上面三条候选路径，按 2→3→1 顺序尝试），Windows 平台 wheel/可执行可用性待验证
- [ ] `DEEPSEEK_API_KEY` 与 provider/model 配置（`deepseek-official` 适配器的模型清单）
- [ ] SDK 对审批的暴露方式（Phase 3 验证）
- [ ] npm 0.1.0-rc.6 与源码 0.1.5 不同步：以 pip/uv 安装的 SDK 及其配套 runtime 为准，锁版本

## 7. Phase 1 最小实现计划（待确认后编码）

范围：**纯 Python 状态层，不依赖 DSH/SDK**（SDK 安装与 smoke test 作为 Phase 0 收尾并行推进）。

```text
pyproject.toml                    # qresearch 包；Python ≥3.10；依赖 pydantic≥2
qresearch/core/models.py          # Project/Goal/Hypothesis/ResearchPlan(+Step)/Experiment/Evidence/ToolRecord
qresearch/core/status.py          # 状态机枚举（proposed/under_test/supported/…、awaiting_approval/…）
qresearch/core/storage.py         # SQLite 持久化：建表、save/load、按 project_id 装载
qresearch/core/events.py          # 追加式 JSONL 事件日志（谁在何时改了什么状态）
examples/resume_demo.py           # 验收脚本：创建→保存→退出→重开→恢复
tests/test_models.py  test_storage.py  test_resume.py
```

- 数据模型字段以精炼版第 5 节为基准，用 Pydantic 强类型 + 序列化校验；
- 验收标准 = 精炼版 Phase 1 验收（创建项目→保存→关闭→重启→恢复）+ `pytest` 全绿；
- 完成后按计划要求输出：改动文件、设计理由、运行方式、测试方式、已知限制、下一步建议。
