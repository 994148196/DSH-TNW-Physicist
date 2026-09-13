# 开发进度

> 由 Coding Agent 维护：每完成一个阶段或里程碑必须更新本文件。阶段定义与验收标准见《基于DSH的量子多体自主科研系统开发计划_v2.md》第 9 节。

**一句话状态**：Phase 0 ✅ 与 Phase 1 ✅ 均已完成并通过验收；下一步 Phase 2（Planning 站点）。

最后更新：2026-09-13

## 阶段总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0 | DSH 可行性（侦察 + SDK 安装 + smoke test） | ✅ 完成 |
| Phase 1 | Research State：模型 + SQLite + 事件日志 + 恢复 | ✅ 完成 |
| Phase 2 | Planning 站点（UNDERSTAND/HYPOTHESIZE/PLAN/CRITIC + Decision） | ⬜ 未开始 |
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

## 里程碑日志

### 2026-09-13（开工日）
- **Phase 0 收尾完成**：`uv pip install -e deepseek-harness/python/sdk`（路径 A）成功；editable 模式缺预编译 runtime exe → 在 `.dsh-runtime/` 隔离安装 npm `@deepseek-ai/dsh@0.1.5-rc.1` 作为 runtime 载体（版本与源码 sync 一致），`dsh_bin` 指向 `.dsh-runtime/node_modules/.bin/dsh.cmd`。smoke test 通过：SDK → DSH runtime → DeepSeek 模型全链路真实轮次。
- **Phase 1 完成**：`qresearch.core`（models/status/storage/events/io/testing）+ `examples/resume_demo.py` + 3 个测试文件。模型层内置铁律校验（假设必须带证伪试验；Decision passed 项必须挂 evidence；declare_result 无条件人工）。
- 测试：**15 passed**（模型规则 / 存储与引用完整性 / 重启恢复与导出导入 roundtrip）。
- 修复两处实现问题：全局工具（project_id="global"）在 load_bundle 中被项目过滤漏掉；测试多项目场景固定 ID 冲突（引入 `id_suffix`）。
- commit：`d55d05c`（初始）→ `ae1cb34`（计划 v2 + PROGRESS）→ 本次（Phase 0 收尾 + Phase 1）。

## 阻塞 / 待决

- 无阻塞。
- 已获授权持续开发（无需逐项审批）。下一步：**Phase 2**——`dsh_client.call_station` 封装 + UNDERSTAND/HYPOTHESIZE/PLAN/CRITIC 站点执行器 + Decision 流转（计划 v2 §9 Phase 2）。
