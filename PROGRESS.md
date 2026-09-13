# 开发进度

> 由 Coding Agent 维护：每完成一个阶段或里程碑必须更新本文件。阶段定义与验收标准见《基于DSH的量子多体自主科研系统开发计划_v2.md》第 9 节。

**一句话状态**：Phase 0 架构侦察完成；SDK 安装 smoke test 待做；Phase 1 已就绪待开工。

最后更新：2026-09-13

## 阶段总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0 | DSH 可行性（侦察 ✅ / SDK 安装+smoke test ⬜） | 🔄 进行中 |
| Phase 1 | Research State：模型 + SQLite + 事件日志 + 恢复 | ⬜ 未开始 |
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
- [x] 精炼版计划评审（4 项问题：环境方式、拓扑、种子工具、验证协议）
- [x] 完整计划修订（计划 v2）
- [ ] 安装 `deepseek-harness-sdk`（路径 A: uv → B: pip+dsh_bin → C: 构建产物）
- [ ] smoke test：`harness.run("Say hi.")` 返回 `final_response`
- [ ] 记录 DEEPSEEK_API_KEY 配置方式与实际可用模型 ID

## Phase 1 验收清单

- [ ] `pyproject.toml`（Python ≥3.10，pydantic ≥2）
- [ ] `qresearch/core/models.py`（Project/Goal/Hypothesis/Plan/Experiment/Evidence/Decision/Tool/Report 全对象）
- [ ] `qresearch/core/status.py`（状态机枚举）
- [ ] `qresearch/core/storage.py`（SQLite 持久化）
- [ ] `qresearch/core/events.py`（追加式 JSONL 事件日志）
- [ ] YAML/JSON 导入导出
- [ ] `examples/resume_demo.py`
- [ ] `pytest` 全绿

验收标准（计划 v2 §9）：创建项目 → 保存 → 关闭 → 重启 → 恢复全部状态。

## 里程碑日志

### 2026-09-13
- 项目建立：`agent.md`、git init（commit `d55d05c`）
- 计划评审：精炼版 4 项问题——① 4.1 环境方式与 DSH 实际形态不符（TS/npm 非 Python）② 接入拓扑需反转 ③ Phase 3 缺种子工具 ④ 验证缺可执行协议
- Phase 0 侦察完成：DSH = TS/Cordis 运行时 + 官方 Python SDK（不在 PyPI，需源码安装）；确定"qresearch 主导循环、SDK 驱动 DSH"拓扑（`docs/phase0-recon.md`）
- 与用户对齐设计：三层架构（账本/状态机/步骤执行器）、LLM 三种合法形态、Decision 问责机制、Spec 先行验证协议、上下文=状态投影、目标三档校准
- 完整计划修订为 **v2**；本进度文件建立

## 阻塞 / 待决

- 无阻塞。
- 待用户确认：按计划 v2 §12 指令开工（Phase 0 收尾 + Phase 1 并行）。
