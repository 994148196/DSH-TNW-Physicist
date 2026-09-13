# 开发进度

> 由 Coding Agent 维护：每完成一个阶段或里程碑必须更新本文件。阶段定义与验收标准见《基于DSH的量子多体自主科研系统开发计划_v2.md》第 9 节。

**一句话状态**：Phase 0 ✅ / Phase 1 ✅ / Phase 2 ✅ 均已完成并通过验收；下一步 Phase 3（Experiment Manager + 种子工具）。

最后更新：2026-09-13

## 阶段总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0 | DSH 可行性（侦察 + SDK 安装 + smoke test） | ✅ 完成 |
| Phase 1 | Research State：模型 + SQLite + 事件日志 + 恢复 | ✅ 完成 |
| Phase 2 | Planning 站点（UNDERSTAND/HYPOTHESIZE/PLAN/CRITIC + Decision） | ✅ 完成 |
| Phase 3 | Experiment Manager + 种子工具（simple_ed、dmrg_adapter） | ⬜ 未开始 |
| Phase 4 | Verification Manager（三层基准 + 证据资格门） | ⬜ 未开始 |
| Phase 5 | Research Loop 闭环（ANALYZE/DECIDE + 报告） | ⬜ 未开始 |
| Phase 6 | Tool Builder（Spec 先行 + 防串通验证） | ⬜ 未开始 |
| Phase 7 | Research Memory | ⬜ 未开始 |
| Phase 8 | HPC 与多项目 | ⬜ 未开始 |

图例：✅ 完成 · 🔄 进行中 · ⬜ 未开始 · ⛔ 阻塞

## Phase 0 验收清单

- [x] DSH 实际形态核实（npm `0.1.0-rc.6` / 源码 `0.1.5`，TypeScript/Cordis + 官方 Python SDK）
- [x] 扩展机制确认（SDK / profile patch / MCP / skill，见 `docs/phase0-recon.md`）
- [x] 接入架构确定（qresearch 经 SDK 驱动 DSH，实验不经 LLM）
- [x] 精炼版计划评审（4 项问题）与完整计划修订（v2）
- [x] 安装 `deepseek-harness-sdk`：**路径 A 成功**（uv editable 从源码安装，runtime-bin 一并装上）
- [x] runtime 可执行文件：editable 安装缺预编译 exe → **项目内隔离安装 npm `@deepseek-ai/dsh@0.1.5-rc.1`**（`.dsh-runtime/`，已 gitignore），SDK `dsh_bin` 指向其 shim；不动全局 npm
- [x] smoke test：`finish_reason=completed`，`final_response=OK` → **PASS**（`examples/dsh_smoke_test.py`）
- [x] API 凭据：`DEEPSEEK_API_KEY` 已在环境中，默认模型 `deepseek-v4-flash` 可用

## Phase 1 验收清单

- [x] `pyproject.toml`（Python ≥3.10，pydantic ≥2，PyYAML；dev: pytest）
- [x] `qresearch/core/models.py`（Project/Goal/Hypothesis/Plan/Experiment/Evidence/Decision/Tool/VerificationReport/ProjectBundle 全对象）
- [x] `qresearch/core/status.py`（全部状态机枚举）
- [x] `qresearch/core/storage.py`（SQLite 持久化 + 引用完整性校验）
- [x] `qresearch/core/events.py`（追加式 JSONL 事件日志）
- [x] `qresearch/core/io.py`（YAML/JSON 导入导出）
- [x] `examples/resume_demo.py`
- [x] `pytest` 全绿：**15 passed**

验收标准：创建项目 → 保存 → 关闭 → 重启 → **恢复全部状态** → `RESUME DEMO: PASS`。

## Phase 2 验收清单

- [x] `qresearch/dsh_client.py`——DSH 唯一耦合点：`DSHClient`（runner 注入可离线测试）+ `call_station`（prompt+JSON Schema → Pydantic 校验 → 失败带错误反馈重试 → 耗尽抛 `NeedsHuman`）+ `extract_json` + `resolve_dsh_bin`
- [x] `qresearch/stations/schemas.py`——站点输出契约（UnderstandOutput/HypothesisDraft/PlanOutput/CriticIssue…）
- [x] `qresearch/stations/prompts.py`——方法论 prompt（可证伪假设、便宜优先、收敛性/有限尺寸必查、基准对照强制）
- [x] `qresearch/stations/executors.py`——understand/hypothesize/make_plan/critique/plan_with_critic（修订循环，未过也返回交人裁决）
- [x] `qresearch/loop.py`——`run_planning_phase`（Project→Goal→假设→计划→批准落账）+ `approve_plan`/`reject_plan`
- [x] `examples/phase2_demo.py`（`--auto-approve` / `--offline` / `--model`）
- [x] `pytest` 全绿：**29 passed**（新增 dsh_client 5 / stations 7 / 流程端到端 2）
- [x] **真实 LLM 验收**：`phase2_demo.py --auto-approve` 全链路跑通（13 条事件，Decision 链可追溯）

验收标准（计划 v2 §9 Phase 2）：科研问题 → Goal → 假设 → plan v1 → 批准 → Decision 链可追溯。**实测表现**：Goal 含 9 个目标量与 8 条量化成功标准；3 条假设均带数值化证伪判据；critic 对 plan v1 提出实质物理攻击（5 blocker：BA 对照缺约定声明、外推自由度不足、数值分辨率当物理结论、伪像风险、计划引用尚不存在的 dmrg_adapter）；plan v2 逐条修订（14 步 / 17 风险，diff_summary 逐 blocker 对应）；v2 仍被判 revise → 按设计返回交人工裁决，auto-approve 留痕（actor=system）。

## 里程碑日志

### 2026-09-13（开工日）
- **Phase 0 收尾完成**：`uv pip install -e deepseek-harness/python/sdk`（路径 A）成功；editable 模式缺预编译 runtime exe → 在 `.dsh-runtime/` 隔离安装 npm `@deepseek-ai/dsh@0.1.5-rc.1` 作为 runtime 载体（版本与源码 sync 一致），`dsh_bin` 指向 `.dsh-runtime/node_modules/.bin/dsh.cmd`。smoke test 通过：SDK → DSH runtime → DeepSeek 模型全链路真实轮次。
- **Phase 1 完成**：`qresearch.core`（models/status/storage/events/io/testing）+ `examples/resume_demo.py` + 3 个测试文件。模型层内置铁律校验（假设必须带证伪试验；Decision passed 项必须挂 evidence；declare_result 无条件人工）。
- 测试：**15 passed**（模型规则 / 存储与引用完整性 / 重启恢复与导出导入 roundtrip）。
- 修复两处实现问题：全局工具（project_id="global"）在 load_bundle 中被项目过滤漏掉；测试多项目场景固定 ID 冲突（引入 `id_suffix`）。
- commit：`d55d05c`（初始）→ `ae1cb34`（计划 v2 + PROGRESS）→ 本次（Phase 0 收尾 + Phase 1）。

### 2026-09-13（Phase 2）
- **Phase 2 完成**：`dsh_client`（call_station + NeedsHuman 升级路径）→ `stations`（schemas/prompts/executors）→ `loop.run_planning_phase`（含 revise 循环与审批落账）。
- 测试：**29 passed**。修复测试基建一处缺陷：脚本化假客户端对裸字符串响应误按字符拆队（改为单元素队列）。
- **真实 LLM 端到端验收通过**：Heisenberg 问题全流程 ~18 分钟（understand 24s → hypothesize 5min → plan 3min → critic 3min → plan v2 → critic v2 → approve）。critic 给出实质物理审查（含"计划引用尚不存在的工具应降级或删除门控"这类可执行意见）；plan v2 的 diff_summary 逐条对应 blocker。
- 已知运行特征：deepseek-v4-flash 单站调用 3–5 分钟（长方法论 prompt + JSON Schema），后续阶段若成瓶颈可考虑裁剪 prompt 或换更快的 profile。
- **架构发现（Phase 3 待办）**：DSH runtime 以 `cwd=项目根` 运行，站内 agent 会自行在仓库写草稿脚本（演示期间产生了 `ed_heis.py`/`corr_heis.py`，已移入 `research_data/demo_phase2/sandbox/` 存档）。Phase 3 Experiment Manager 必须为每个项目建立隔离沙箱目录并把 DSH `cwd` 指向它。
- 下一步：**Phase 3**——Experiment Manager + 种子工具（simple_ed：scipy 稀疏 Lanczos；dmrg_adapter 桩）+ golden fixtures（出题人≠答题人，spec 先行）。

## 阻塞 / 待决

- 无阻塞。
- 已获授权持续开发（无需逐项审批）。
