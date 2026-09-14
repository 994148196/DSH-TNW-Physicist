# 开发进度

> 由 Coding Agent 维护：每完成一个阶段或里程碑必须更新本文件。阶段定义与验收标准见《基于DSH的量子多体自主科研系统开发计划_v2.md》第 9 节。

**一句话状态**：Phase 0–6 ✅ 已完成（P5 live 完整 3 轮闭环 + P6 live 构建晋升均通过）；Phase 7 🔄 进行中（Research Memory）。

最后更新：2026-09-13

## 阶段总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0 | DSH 可行性（侦察 + SDK 安装 + smoke test） | ✅ 完成 |
| Phase 1 | Research State：模型 + SQLite + 事件日志 + 恢复 | ✅ 完成 |
| Phase 2 | Planning 站点（UNDERSTAND/HYPOTHESIZE/PLAN/CRITIC + Decision） | ✅ 完成 |
| Phase 3 | Experiment Manager + 种子工具（simple_ed、dmrg_adapter） | ✅ 完成 |
| Phase 4 | Verification Manager（三层基准 + 证据资格门） | ✅ 完成 |
| Phase 5 | Research Loop 闭环（ANALYZE/DECIDE + 报告） | ✅ 完成 |
| Phase 6 | Tool Builder（Spec 先行 + 防串通验证） | ✅ 完成（live 验收 + 晋升） |
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

## Phase 3 验收清单

- [x] `benchmarks/golden/simple_ed.yaml`——spec 先行：解析值（N=2：-1.5J、gap=2J；N=4：-2J）+ 独立 oracle（稠密对角化）+ Bethe 热力学极限 + 单态/SU(2) 自洽 + 单调收敛，每条检查带 layer 标签与出处
- [x] `benchmarks/golden/dmrg_vs_simple_ed.yaml`——独立实现差分（L=4,6,8,10，含 gap）
- [x] `qresearch/tools/registry.py`——工具注册表（注册即带版本/适用范围/基准清单/已知限制，§8.3）
- [x] `qresearch/tools/simple_ed.py`——Heisenberg 链 ED（PBC、Sz=0 扇区稀疏 Lanczos + Sz=+1 扇区中转算 ⟨S²⟩）
- [x] `qresearch/tools/dmrg_adapter.py`——quimb DMRG 接入；本机 quimb 1.15 局部本征求解器缺陷（DMRG/DMRG2 均复现）→ 自动回退 MPO 稠密对角化，`method`/`fallback_note` 如实留痕
- [x] `qresearch/tools/cli.py` + `SubprocessToolRunner`——实验经子进程执行（不经 LLM、崩溃隔离）
- [x] `qresearch/experiments/manager.py`——execute_step / execute_scan（批量扫描）/ execute_plan（跳过需审批与非实验步骤）/ 复现元数据（seed、code_version、environment）/ `summarize` 摘要表 / 失败也落账
- [x] `qresearch/verification/golden.py`——golden 检查器（approx / oracle_dense / diff / increasing_toward），Phase 4 的种子
- [x] PLAN/CRITIC prompt 注入真实工具清单（避免计划引用不存在的工具）
- [x] `pytest` 全绿：**47 passed**（新增 simple_ed 6 / golden 3 / manager 7 / cli 2）
- [x] **演示验收**：`phase3_demo.py`——批准的计划 → 自动建 6 个实验（1 基准 + 4 扫描点 + 1 交叉验证）→ 子进程批量跑 → 全部落账（13 条事件）→ 摘要表；golden 16/16 PASS → **PHASE 3 DEMO: PASS**

验收标准（计划 v2 §9 Phase 3）：批准的计划 → 自动建实验 → 批量跑 → 落账 → 实验摘要。**全部达成。**

## Phase 4 验收清单

- [x] `qresearch/verification/manager.py`——VerificationManager：
  - `verify_tool`：跑该工具全部 golden 基准 → software/numerical/physics 三层逐层 VerificationReportItem（缺基准 = UNCERTAIN"禁止出具物理证据"）；
  - `verify_experiment`：程序层（结果可读 + 同参数复跑可重复）+ 数值层（残差阈值 / 变分上界，字段缺失判 UNCERTAIN 不冒充合格）+ 物理层（继承工具 golden 结论）→ overall 取最差；VerificationReport 落账，验证状态回写 Experiment，事件留痕；
  - `evidence_gate`：只有 overall=PASSED 放行；UNCERTAIN 需人工复核、FAILED 禁止出具证据（计划 v2 §7.6"不过关的实验取消证据资格"）。
- [x] golden 检查器加固：逐 case 异常隔离（工具崩溃 = 该 case 全部检查 fail，不炸套件）、layer 标签贯通
- [x] 复现语义校准：复跑判定为物理精度内可重复（相对容差 1e-10）——ARPACK/BLAS ulp 级抖动允许，真实不确定行为必抓
- [x] simple_ed 修复隐藏缺陷：ARPACK 固定初始向量 v0（原随机 v0 导致机器精度级不可复现）
- [x] `pytest` 全绿：**55 passed**（新增 verification 8 + golden 语义更新 1）
- [x] **验收条文达成**：`phase4_demo.py`——注入偏移 -0.01J 的错误实现 → 三层全抓（software 复跑不一致；解析值 |-1.51−(-1.5)|=1e-2>1e-10；Bethe/差分/自洽层连锁失败）→ VerificationReport 出具 → 证据门关闭；正常实验全部放行 → **PHASE 4 DEMO: PASS**

验收标准（计划 v2 §9 Phase 4）：注入一个故意写错的工具能被抓住并出具报告。**达成。**

## Phase 5 验收清单

- [x] `qresearch/research_loop.py`——`run_research_loop` 确定性主循环：Round 0（UNDERSTAND+HYPOTHESIZE）→ 逐轮 PLAN（critic 修订循环，previous + based_on_decision 挂链）→ 审批（人工触点 1）→ EXECUTE → VERIFY（证据资格门）→ ANALYZE → DECIDE（人工触点 2）→ 终止判定 → `report.md` 自动生成
- [x] `stations/executors.py` 新增 ANALYZE/DECIDE 执行器：五段式分析（observations/interpretations/uncertainties/alternative_explanations/recommended_next_steps），每条主张挂已验证实验 id 并落账为 Evidence；Decision 由 `_REC_MAP` 从 LLM 建议映射（iterate/terminate/replan/declare_result），checklist passed 项必须引用真实 evidence id
- [x] `dsh_client.call_station` 新增 `validator` 钩子：schema 通过后的语义校验（引用合法性）失败视同校验失败——带错误反馈重试，耗尽抛 `NeedsHuman` 转人工，不再硬崩溃
- [x] **证据资格门贯通全链**：只有三层验证 PASSED 的实验进入 ANALYZE prompt 且引用合法由代码校验；DECIDE checklist 只能引用已落账 evidence——LLM 全程只提议，资格与记账在代码
- [x] **预算闸**：rounds 上限耗尽时代码强制 `requires_human` + rationale 加 `[预算闸]` 前缀，iterate 不被自动执行
- [x] 报告生成：假设/计划/实验表/分析结论（挂实验 id）/决策记录/局限，全部可回溯
- [x] `pytest` 全绿：**58 passed**（新增 research_loop 3：三轮端到端/预算闸/资格门拒绝）
- [x] **验收演示**：`phase5_demo.py` 离线脚本模型 3 轮全自动闭环 → status=terminated，决策链 iterate→iterate→declare_result，6 条 Evidence，50 条事件全程可回放 → **PHASE 5 DEMO: PASS**（`--live` 支持真实 LLM 复跑同一流程）

验收标准（计划 v2 §9 Phase 5）：MVP 问题全自动走完 3 轮，人工只批计划与终止，全程可回放。**达成（离线 + 真实 LLM 双验证）。**

- [x] **live 完整闭环验收 PASS（2026-09-14）**：真实 DSH 全自动走完 3 轮 → status=terminated（非 needs_human），决策链 iterate→replan→terminate，381 条 Evidence，302 条事件可回放，报告 `research_data/demo_phase5_live/report.md`。三轮计划 v2/v4/v6 各 16–17 步；24+37+... 个实验（ED N=4–20 偶数尺寸能量序列 + SU(2) 单态校验 + 能隙标度 + dmrg_adapter 交叉验证），首轮 1 个 dmrg 失败被优雅落账且后续轮修复复跑；ANALYZE 一次通过资格门（8 观察+5 解读+10 不确定度）；prompt 加固（动作选择指引 + scan 错误信息给替代方案 + live 重试 2）后 plan 校验不再耗尽

## Phase 6 验收清单（进行中）

- [x] `tool_specs/tfim_ed.yaml`——真实缺口的 Tool Spec（横场 Ising 链 ED；出题侧先于实现定稿，DSH 无 fixtures/容差修改路径）
- [x] `tool_specs/tfim_ed.golden.yaml`——golden 定值：h=0 偶 N 精确 E0=-NJ 与两重简并 gap=0（解析）、临界 e0(∞)=-4J/π（Lieb-Schultz-Mattis 1961，N=12 容差 1e-2）、独立稠密 oracle 对照（E0+gap，tol 1e-8）、单调收敛；锚点全部经独立稠密对角化预校验
- [x] `qresearch/verification/golden.py`——`dense_tfim_ground` 独立 oracle（bit-flip 稠密构造，与被测工具无共享代码路径）；oracle_dense 检查支持 oracle 选择；`run_benchmark(tool_override=)` 支持构建期候选名
- [x] `qresearch/tool_builder/`——build_tool 全流程：DSH 编码（隔离 workspace，编码 prompt 不含任何基准数值）→ golden 自动跑（失败回喂修复，上限 3）→ 三层验证 → 批评者审 diff vs Spec → 构建报告 → 审批 → 注册正式名 + fixtures 安装 + ToolRecord 落账
- [x] `registry.register(replace=)`/`unregister`：候选覆写与清理（受控词汇保护不变：正式名重复注册仍报错）
- [x] `dsh_client.run_agent`：开放轮次（无 schema 的文件交付任务，调用方在文件层验收）
- [x] `pytest` 全绿：**70 passed**（新增 tool_builder 4：修复循环 / 耗尽不注册 / 编码 prompt 无基准值泄漏 / client_factory 每轮独立工作目录；晋升后含 tfim_ed golden 主套件）
- [x] **离线演示 PASS**：`phase6_demo.py`——注入横场偏移 0.01 的带错首版 → golden 解析锚点当场抓住 → 回喂修复 → 三层验证 passed → 批评者 pass → 注册 → 研究闭环接入实验验证 PASSED（证据资格门放行）
- [x] **live 构建验收 PASS**：真实 DSH（deepseek-v4-flash）编码 tfim_ed——**首轮即通过全部 8 项 golden**（含 h=0 精确锚点、临界 -4J/π、独立稠密 oracle、单调收敛），三层验证 passed；批评者给出实质审查（1 concern：gap 归零阈值 1e-11 会吞掉 N≥18 有序相的指数小能隙——实测 N=20,h=0.3 真实 gap 8.56e-12 被报为 0；2 minor：docstring 夸大自检、与 Spec 无关的元评论），无 blocker；构建报告出具（`research_data/demo_phase6/builds/tfim_ed/build_report.md`），auto-approve 留痕（actor=system，演示）
- [x] **晋升完成**：交付代码原样复制入 `qresearch/tools/tfim_ed.py`（仅追加 seed 注册块，保持 diff-vs-Spec 可追溯）+ `load_seed_tools` 注册 + fixtures `benchmarks/golden/tfim_ed.yaml` 随晋升提交（与出题侧定稿逐字节一致）。人工审查（代行）结论：① 构建者正确反驳了 Spec 初版的错误表述——铁磁 TFIM 在 h=0 无奇 N 受挫（键算符对易且可同时取 -1，全对齐态 E0=-NJ 任意 N 成立），受挫的是 J<0 反铁磁（奇 N 环 E0=-|J|(N-2)），出题侧采纳并修订 `tool_specs/tfim_ed.yaml`；② critic 的 gap 归零 concern 作为已知限制接受（golden 覆盖域之外、docstring 已如实声明）。晋升暴露并修复一处真实缺陷：`build_tool` 正式注册不容忍同名 seed/旧版本 → `register(replace=True)`（重建升级路径）。晋升后子进程模式可用，注册表 = simple_ed + dmrg_adapter + tfim_ed

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
- **Phase 3 完成**：spec 先行（golden 基准先于实现定稿，数值全部来自解析/文献/独立实现）→ 工具注册表 + simple_ed + dmrg_adapter → 子进程执行 + 批量扫描 + 复现元数据落账。
- 测试：**47 passed**。实现期修了三处自己的 bug（COO 构造签名、S⁺ 跃迁需 Sz=+1 中转扇区、关联求和漏除 N）——均被 golden 解析值当场拦截，验证了"出题人≠答题人"机制有效。
- **quimb 平台缺陷（已记录、已绕过）**：quimb 1.15 的 DMRG/DMRG2 局部本征求解器在本机产生 NaN/Singular matrix（与 NUMBA_DISABLE_JIT 无关）。dmrg_adapter 主路径保留 DMRG2，运行时自动回退 quimb MPO 稠密对角化（仍为独立实现，差分基准 L≤10 一致到 1e-15），`method`/`fallback_note` 字段留痕，缺陷写入 known_limitations。L>14 的 DMRG 需在 quimb 可用的环境运行（Phase 8 HPC 场景）。
- **演示验收 PASS**：批准计划 → 6 实验自动建立 → 子进程批量执行 → 全部落账 → 摘要表；golden 16/16。
- **Phase 4 完成**：三层验证编排 + VerificationReport + 证据资格门。验证层当场抓到两个真问题：① simple_ed 用 ARPACK 随机初始向量 → 复跑 ulp 级不可复现（修：固定 v0，Marshall 定理保证与基态有重叠）；② 复现语义校准为物理精度内可重复（1e-10 相对容差），ulp 抖动允许、真实不确定必抓。
- **验收演示 PASS**：注入偏移 -0.01J 的同名错误实现 → software 层解析值、numerical 层 oracle 差分、physics 层 Bethe 极限/自洽连锁失败 + 复跑不一致 → 报告出具、证据门关闭；正常实验放行。

### 2026-09-13（Phase 5）
- **Phase 5 完成**：`research_loop.run_research_loop` 确定性闭环 + ANALYZE/DECIDE 站点执行器 + Evidence 全链贯通 + 预算闸 + 报告自动生成。
- 设计要点：① 语义校验（引用合法性）下沉为 `call_station` 的 `validator` 钩子——违规先带错误反馈重试，仍违规则 `NeedsHuman` 转人工，避免 LLM 偶发违规直接炸掉整个闭环；② 预算闸是代码层覆盖（LLM 建议 iterate 而预算耗尽 → 强制 requires_human 并在 rationale 留痕 `[预算闸]`）；③ DECIDE prompt 必须显式列出 evidence id（模型无法引用看不见的 id）。
- 测试：**58 passed**。三轮端到端测试断言了计划版本递增与 `based_on_decision` 挂链、决策类型序列、evidence 挂真实实验、事件链完整（plan_ready/approve/experiment_started/verify_experiment/analyze/decide 各 3 或 6 次 + report_generated）。
- **离线验收演示 PASS**：MVP Heisenberg 问题 3 轮全自动 → 决策链 iterate→iterate→declare_result，6 条 Evidence，50 条事件可回放，`research_data/demo_phase5/report.md` 自动生成。
- 下一步：**Phase 6**——Tool Builder：Spec 先行生成工具缺口实现 + 防串通验证（出题人≠答题人），验收：一个真实工具缺口被自动补齐并通过三层验证。

### 2026-09-13（Phase 5 live 验收 + Phase 6 机制）
- **Phase 5 live 验收暴露并修复三个真实闭环缺陷**（离线脚本模型覆盖不到的真实 LLM 行为）：
  1. LLM 把动作名当工具名填入 plan（tools: ["run_experiment"]）→ execute_step KeyError 炸闭环。修：make_plan 语义校验（validator 钩子：tools 只能引用注册工具 + 可执行动作恰好 1 工具，违规重试耗尽转人工）+ execute_step 未知工具按 FAILED 落账。
  2. DSH session 持久化于 dsh_home，重跑同 project_id → JsonRpcError "session already exists"。修：演示 project_id 加时间戳 + finally 关闭 runtime（遗留孤儿 node 进程教训）。
  3. parameter_scan 子实验 step_id 带 _idx 后缀 → tool_by_step 精确匹配落空 → 18 个实验全部跳过验证、eligible 为空 → ANALYZE 引用 "none" 被资格门拒绝（资格门本身工作正常）。修：_tool_for 沿父步骤回溯 + ANALYZE prompt 明确空数组规则。
- **Phase 5 live 状态**：新鲁棒性（needs_human 优雅落账 + 部分报告）在真实运行中得到验证；修复后 live 复跑进行中。
- **Phase 6 机制完成**：见上方 Phase 6 验收清单。防串通设计：编码 prompt 零基准数值（测试断言）、fixtures/容差出题侧锁定、批评者审 diff、审批后注册。测试 66 passed。
- 已知物理陷阱（测试设计教训）：不能用 +J"反铁磁"当 TFIM 的破坏实现——偶数双分环上与铁磁谱完全等价（规范变换），golden 会放行；破坏实现改用横场偏移。

### 2026-09-13（Phase 6 live 验收 + 晋升）
- **live 构建一次通过**：DSH 编码站（cwd=workspace 隔离 + client_factory 每轮独立 runtime）在 attempt_1 交付 394 行 Lanczos/稠密双后端实现 + selftest.py（独立 Kronecker oracle + 显式"无基准常数"声明——防串通指令落地），golden 8/8 首轮全过，三层验证 passed。批评者给出高质量物理审查：定位了 gap 归零阈值与 Spec 定义的真实冲突并用独立 ncv=200 参考值实测（8.56e-12 vs 8.57e-12）——concern 而非 blocker，登记为已知限制。
- **构建者反驳出题侧并获得采纳**：Spec 初版"奇 N h=0 铁磁受挫"是错的（受挫的是 J<0 反铁磁）。构建者用全枚举验证 + 解析论证反驳——正是 Spec 期望的行为（发现 Spec 错误时如实声明而非迎合）。
- **晋升完成**：交付代码逐字节保留（仅追加注册块），fixtures 与出题侧定稿 diff 一致后入库，`build_tool` 注册路径修为 `replace=True`（同名 seed/旧版本重建升级）。测试 **70 passed**（golden 主套件现含 tfim_ed 全部 8 项检查）。
- **P5 live 复跑继续**：plan 校验反馈再加固（动作选择指引：run_experiment=单点 / parameter_scan=网格必须带 inputs.scan；错误信息给出替代动作）、live 重试上限提到 2——待复跑验证。

### 2026-09-14（Phase 5 live 完整闭环）
- **P5 live 验收 PASS**：加固后首跑即完整走完 3 轮（~25 分钟，302 事件）——status=terminated，决策链 iterate→replan→terminate（第 2 轮模型主动判定路线信息增益耗尽建议 replan，第 3 轮换路线后 terminate 收束），381 条 Evidence，报告自动生成。plan 校验零耗尽（动作选择指引 + 错误信息给替代动作生效），1 个 dmrg 实验失败被优雅落账并在 replan 后复跑成功。至此 Phase 5 验收标准的"全自动 3 轮"在离线与真实 LLM 两种模式下均达成。
- **Decision 质量观察**：checklist 全部挂真实 evidence id；replan 的 rationale 明确引用"第 2 轮状态与第 1 轮实质相同"——预算/信息增益判断有依据，非套话。
- 下一步：**Phase 7**——Research Memory（四层记忆：项目/方法/工具/失败案例 + 检索注入；验收：新项目能检索复用旧项目经验，含"哪些实验没信息增益"）。

## 阻塞 / 待决

- 无阻塞。
- 已获授权持续开发（无需逐项审批）。
- **待办：DSH 站内 agent 的写入沙箱**——DSH runtime 以 `cwd=项目根` 运行，站内 agent 会自行在仓库写草稿脚本（Phase 2 演示期间产生了 `ed_heis.py`/`corr_heis.py`，已移入 `research_data/demo_phase2/sandbox/` 存档）。实验层已隔离（`research_data/experiments/<exp_id>/`），但 DSH 站点调用的 `cwd` 仍指向项目根；应在 dsh_client/loop 层为每项目设沙箱目录。
