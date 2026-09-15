# 开发进度

> 由 Coding Agent 维护：每完成一个阶段或里程碑必须更新本文件。阶段定义与验收标准见《基于DSH的量子多体自主科研系统开发计划_v2.md》第 9 节。

**一句话状态**：计划 v2 Phase 0–8 ✅ 全部完成且均经 live 验证（84→104→113 tests）。计划 v3（交互式 Agent 化，分支 `feature/interactive-agent`）：**M0/M1/M2/M3 全部 ✅**（125 tests；profile 含权限预设已落盘 `~/.dsh/profiles/research`，`--dump-config` 零警告）——待 L3 live 五步走查（人工）。

最后更新：2026-09-15

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
| Phase 7 | Research Memory | ✅ 完成 |
| Phase 8 | HPC 与多项目 | ✅ 完成 |
| 计划 v3 M1 | 内核接口化：ResearchEngine façade + job 注册表 | ✅ 完成 |
| 计划 v3 M2 | MCP 接入 + CLI 审批台 + skill + profile | ✅ 完成（待 L3 live 走查） |
| 计划 v3 M3 | 协作式取消 + 汇报模板 + markdown 卡片 + 权限预设 + 文档 | ✅ 完成（待 L3 live 走查） |

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

## Phase 7 验收清单

- [x] `qresearch/memory/store.py`——`MemoryStore`：跨项目独立 SQLite + Markdown 镜像（人可读）；四层 `MemoryEntry`（project 项目层 / method 方法层 / tool 工具层 / failure 失败案例层）；确定性关键词检索（拉丁分词 + 中文 2-gram，命中打分，零分不返回——向量检索为后续增强）
- [x] `qresearch/memory/distill.py`——`distill_project`：项目结束时从台账**确定性蒸馏**（铁律延续：代码提取，不经 LLM，每条可回放到源实验/决策/报告）：项目层（问题+假设命运+决策轨迹）、方法层（被验证支撑的证据主张，上限 6 条）、工具层（使用/失败/验证统计）、失败案例层（FAILED 实验 + 验证未通过实验，含错误与教训）；全部条目携带问题域标签（相似问题可检索）与工具名（人类可读，非 ToolRecord 哈希）
- [x] `qresearch/memory/inject.py`——`memory_digest`：检索结果格式化为 prompt 片段，**失败案例排最前**（计划 §7.9：DECIDE 与下一轮 PLAN 的固定输入）
- [x] 注入贯通：PLAN/DECIDE 模板新增 `{memory}` 块（"失败案例与拟议步骤雷同时必须先修正做法或明确写出差异"）；`run_research_loop(memory_store=)` 每轮检索注入并落账 `memory_retrieved` 事件，项目结束蒸馏入库并落账 `memory_written`；未接记忆库时闭环行为不变（占位"（无）"，无 memory 事件）
- [x] `pytest` 全绿：**75 passed**（新增 memory 5：四层蒸馏 / 检索与镜像 / 闭环注入+蒸馏落账 / 无记忆库行为不变 / 空库摘要）
- [x] **验收演示 PASS**：`examples/phase7_demo.py`——旧项目（含奇数 N 必败实验）蒸馏四层入库 → 新项目（同问题域）PLAN/DECIDE prompt 均检索到旧项目失败案例"simple_ed 要求偶数 N"与方法/工具经验 → 新项目干净收尾不产失败案例（如实）但项目/方法/工具层滚动入库 → Markdown 镜像可读 → **PHASE 7 DEMO: PASS**

验收标准（计划 v2 §9 Phase 7）：新项目能检索复用旧项目经验（含"哪些实验没信息增益"）。**达成。**

## Phase 8 验收清单

- [x] **并行实验**：`ExperimentManager` 重构为"计算段线程池并行 + 记账主线程"——存储层（SQLite 非线程安全）写路径保持在主线程，`EventLog` 自带锁；`execute_plan(max_workers=N)` 并发峰值>1、耗时近线性缩短，落账顺序仍确定（`experiment_started` 全部先于任何 `experiment_finished`，按提交顺序 finalize）
- [x] **预算闸**：`qresearch/core/budget.py`——`Budget(max_rounds / max_experiments / wallclock_min)`，代码层轮间硬闸（LLM 不可越过），耗尽原因留痕（"实验数预算耗尽（2/2）"等）并落账 `budget_exhausted` 事件
- [x] **暂停 / 恢复**：轮间检查 `PAUSE` 旗标文件（`pause_flag`）→ 优雅暂停落账；`resume_research_loop` 从台账重建完整状态（真相在库里）续跑——计划版本 [1,2,3] 跨恢复衔接、决策/证据/事件链不断；`research_loop.py` 重构为共享的 `_rounds_loop`（fresh/resume 同一主干，消除双实现漂移）
- [x] **Slurm 后端**：`qresearch/hpc/slurm.py`——`render_sbatch` 纯函数（#SBATCH 分区/时限/内存/CPU/账户，实验不经 LLM：脚本内 `python -m qresearch.tools.cli run`）+ `SlurmRunner(ToolRunner)`。**诚实边界：本机无 Slurm 集群，`dry_run=True`（默认）只生成/校验 sbatch 脚本不提交；dry-run 结果带 `dry_run` 标记且无工具输出 → 三层验证不放行、证据资格门拒绝引用（诚实失败优于冒充成功）。真实提交路径（sbatch 解析 job id / squeue 轮询 / result.json 回收）按标准 Slurm 命令实现，需集群环境验收**
- [x] **多项目编排**：`qresearch/orchestrator.py`——`run_projects` 项目队列串行（LLM 站点是瓶颈，串行即可饱和；项目内实验另按 `max_parallel_experiments` 并行）；每项目独立 `<data_root>/<pid>/{state.sqlite, events.jsonl, sandbox/, experiments/, report.md, PAUSE}`；**`client_factory(sandbox)` 以沙箱为 cwd**——收口 Phase 2 发现的"DSH 站内 agent 写仓库根"问题；`auto_approve` 默认 False（长期队列不默认越过人工审批）
- [x] **测试基建修正**：脚本化假客户端的 callable 语义由"一律常驻"改为"一次性消费 + 显式 `Persistent` 包装"——旧语义使按序编排（如 decide: iterate→iterate→terminate）永远停在第一个应答上，正是 test_pause_and_resume 失败的根因；队列耗尽改为显式报错（"编排少写一轮"立即暴露，不再静默串位）
- [x] `pytest` 全绿：**81 passed**（新增 hpc 6：并行计时与落账顺序 / 实验数预算 / 墙钟预算 / 暂停恢复三段衔接 / sbatch 渲染+dry-run 证据门 / 双项目队列隔离与记忆共享）
- [x] **验收演示 PASS**：`examples/phase8_demo.py` 五段——① 4×0.25s 实验 0.30s 完成（并发峰值 4）；② 实验数预算闸第 2 轮前拦停；③ 暂停→恢复→终止全程版本/决策衔接；④ sbatch 渲染字段齐备 + dry-run 被证据门拒绝；⑤ 双项目队列 terminated + 沙箱隔离 + 记忆库跨项目入库
- [x] **live 验收 PASS**（`--live`，真实 LLM 双项目队列）：proj_a（Heisenberg）**terminated**——2 轮 39 实验（simple_ed 22 次 + dmrg_adapter 17 次，含 29 条本机 quimb 缺陷失败案例如实入库），R1 iterate（1/N² 外推 vs Bethe ansatz 偏差 1.77e-4，含 1/N⁴ 项 7.2e-6），R2 terminate（带如实的证据覆盖自我批评）；proj_b（TFIM 临界区）hypothesize 期间 runner 断链悬挂 ~21 分钟 → 重试耗尽 → **needs_human 优雅收尾**（rounds_used=0 部分报告 + 记忆蒸馏 project 层）。队列/隔离/记忆断言全部成立
- [x] **live 暴露并修复健壮性缺口——站点看门狗**：runner 子进程死亡后 SDK 等待可能永不返回（本例 runtime 侧 ~10 分钟静默后才断开，尾部风险是无限悬挂）。`DSHClient._run` 加 wallclock 看门狗（默认 15 分钟，`QRESEARCH_STATION_TIMEOUT_S` 可调）：超时 → 重启 runtime 子进程（旧 RPC 随之消亡）→ `StationTimeout`；`call_station` 将 runtime 层失败换 session 确定性重试并落账 `station_retry` 事件，耗尽转 `NeedsHuman`。测试 84 passed（新增看门狗 3：超时重启 / runtime 失败重试成功 / 持续失败转人工）

验收标准（计划 v2 §9 Phase 8）：多项目可编排队列执行、实验可并行、可暂停恢复、预算可控、HPC 扩展点就位。**达成**（Slurm 真实提交路径留待集群环境验收，dry-run 已验证到脚本层）。

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

### 2026-09-14（Phase 7 完成）
- **Phase 7 完成**：四层跨项目记忆库 + 确定性蒸馏 + 检索注入（见 Phase 7 验收清单）。设计要点：① 记忆库是独立 SQLite（跨项目），与每项目 state.sqlite 分离，Markdown 镜像供人审；② 蒸馏是代码不是 LLM——记忆里的每条教训都能回放到源实验/验证报告/决策，避免"记忆本身成为无来源结论"；③ 检索是确定性关键词打分（拉丁分词 + 中文 2-gram），不引入向量库依赖（后续可换）；④ 失败案例层排注入最前，PLAN/DECIDE 模板明确"与拟议步骤雷同时必须修正或说明差异"。
- 测试 **75 passed**；离线验收演示 8 项检查全过。蒸馏期发现并修复可读性问题：experiment.tool_id 存的是 ToolRecord 哈希，记忆条目统一解析回工具名。
- 至此计划 v2 的核心科研闭环（Phase 0–7）全部完成。下一步：**Phase 8**——HPC 与多项目（Slurm/远程执行、预算、队列、暂停恢复、并行实验、沙箱隔离补全）。

### 2026-09-14（Phase 8 完成）
- **Phase 8 完成**：并行实验 / 预算闸 / 暂停恢复 / Slurm 后端 / 多项目编排（见 Phase 8 验收清单）。设计要点：① 并行只切计算段，记账保持主线程（SQLite 非线程安全），落账顺序确定；② `research_loop.py` 重构为 `_rounds_loop` 共享主干 + `resume_research_loop` 台账重建——暂停/恢复的真相在库里，fresh 与 resume 不再有双实现；③ Slurm 后端实现 ToolRunner 接口（实验不经 LLM 铁律不变），dry-run 结果物理性过不了证据门——诚实边界落在机制上而非文档上；④ 编排器每项目独立沙箱 cwd，收口 Phase 2 的"站内 agent 写仓库根"遗留项。
- **测试基建修正**：假客户端 callable 语义改一次性 + 显式 `Persistent`——旧语义的"callable 一律常驻"使按序 decide 编排永远停在第一个应答（test_pause_and_resume 的 budget_exhausted≠terminated 由此而来，循环本体无缺陷）。这修正波及 test_research_loop / test_tool_builder 的既有编排写法，全部改为显式常驻；队列耗尽从静默串位改为显式报错。
- 测试 **81 passed**；验收演示五段全 PASS。Slurm 真实提交路径需集群环境（诚实边界已记入验收清单）。
- **live 验收**（`--live` 双项目真实 LLM 队列）：proj_a terminated（2 轮 39 实验，1/N² 外推偏差 1.77e-4 的实质物理结论；dmrg_adapter 本机缺陷在 live 复现并被蒸馏为 29 条失败案例——跨项目记忆第一次从真实失败中积累）；proj_b 遭遇 runner 断链（悬挂 ~21 分钟）→ NeedsHuman 优雅收尾：部分报告 rounds_used=0、记忆蒸馏照常执行、队列继续。**事故驱动修复**：DSHClient 加站点看门狗（wallclock 上限 → 重启 runtime → station_retry 重试 → 耗尽转人工），把"runtime 死亡可能无限悬挂"变成有界确定性失败；这是 P2 以来"SDK 等待无超时"的最后一个已知裸露点。
- 测试 **84 passed**。至此计划 v2 Phase 0–8 全部完成且均经 live 验证。后续可选项：计划 v2 阶段五（多 Agent 协作 + 自动研究方向，仅探索不验收）；Slurm 真实集群验收（需集群环境）。

## 阻塞 / 待决

- 无阻塞。

### 2026-09-14（文档化）
- **README + 双文档完成**：`README.md`（入口索引 + 功能总览）；`docs/user_guide.md`（使用者指南：安装、快速上手、四个真实案例——Heisenberg live 研究 / tfim_ed 构建 / 跨项目记忆 / 运维操作——读懂输出、FAQ）；`docs/manual.md`（技术手册：架构、五条铁律、13 个台账对象、六站点、三层验证、证据资格门、Tool Builder 防串通、研究记忆、HPC/编排、配置、设计权衡备忘）。

### 2026-09-14（首个仓库外完整研究项目：TFIM 临界标度 + 中心荷提取）
- **完整研究示例 PASS**（`D:\AI\Agent\Try\research-projects\tfim_cft_scaling\`，仓库外目录，数据与代码分离）：题目"TFIM 临界点 h/J=1 基态能量密度有限尺寸标度 + CFT 中心荷提取"。3 轮 ~18 分钟，status=terminated，决策链 iterate→iterate→accept（预算 3/3 收敛），68 实验（tfim_ed 21 / simple_ed 22 / dmrg 25，max_parallel=4），290 条证据，记忆库蒸馏 50 条（failure 40）。
- **物理结论**（全部可回放）：① ED 与精确 Jordan-Wigner 解析式 E0(N)=−2/sin(π/2N) 机器精度一致（≤4e-15），gap 与 2tan(π/4N) 一致——分析自主发现该解析式并用作交叉验证；② e∞=−1.2732178 vs −4/π 偏差 2.18e-5（判据 1e-3）；③ 标度幂次 p=2.006（7 尺寸），1/N² 系数 a=−0.5235862 vs −π/6 偏差 0.0024%，双项拟合恢复 1/N⁴ 系数 b 与解析 −7π³/1440 偏差 0.97%；④ **中心荷 c∈[0.5034, 0.5166]**（Ising 普适类 c=1/2），全部落在预设 [0.40,0.60]。
- 决策质量：R1 iterate 自主识别三个决定性缺口（尺寸不全/平凡窗口/跨工具交叉验证失败——simple_ed 被误用于 TFIM 对照，分析层当场发现并弃用）；R2 iterate 要求补 N=20 杠杆点、系统误差量化、c 置信区间；R3 在预算耗尽时就地收敛并给出 accept。dmrg_adapter 本机缺陷再次复现（25 次使用多数失败，如实入库）。
- 运行采用 auto_approve=True（actor=system 留痕"非人工"）；最终 accept 决策 requires_human=True——**结论确认留给用户**。
- 已获授权持续开发（无需逐项审批）。

### 2026-09-14（报告总结 + 计划版本历史 + 交互与可视化）
- **报告重做**（generate_report 确定性拼装）：置顶**研究总结**——最终状态+决策链、结论（收束决策 rationale 原文，附决策 id）、支撑结论的关键证据（passed 检查项逐条挂 evidence id）、假设命运（支持/反驳/未判定）、规模统计；新增**计划版本历史**一节——每版的 critic 判定与阻塞项（⛔ 标注）、依据决策、diff_summary、步骤清单与需审批标记（由 _finalize 从 plan_ready/approve 事件重建）。未收束的运行如实标注"未给出最终结论"，不编造。
- **对话式计划审批**（类 Claude Code）：`[y]批准 / [c]提修改意见 / [s]看步骤细节 / [q]放弃` 循环。c → plan_feedback 事件（actor=HUMAN）→ 带意见重新 plan_with_critic（user_notes 以最高优先级注入计划 prompt"研究者本人的修改意见"栏，不采纳须写明理由）→ 新版重新过 critic 再请批；EOF 一律按放弃，绝不默认批准；修订次数受 max_revisions=5 约束。_interactive_approval 返回最终计划（调用方以返回值为准）。
- **轮末回调 round_callback**（fresh/resume 通用）：每轮 decide 后（未收束时）收到摘要 dict（轮号/决策/本轮实验与证据数/证据总量），返回 None 继续 / "stop" 当轮收尾（status=stopped_by_user，事件 user_stop，可 resume）/ 意见字符串（事件 user_feedback，actor=HUMAN，注入下一版计划、只生效一轮）。收束轮不触发——结论走 declare_result 无条件人工确认通道。_finalize 顺带把 stopped_by_user/paused 的 hint 并回 summary。
- **可视化 qresearch/viz.py**（可选依赖 `pip install -e ".[viz]"`，matplotlib 已入 venv）：`plot_project()` 三张 PNG——工具×状态堆叠柱图；数值结果 vs 参数（按计划版本着色，实心=passed/空心=未过验证；只画 ≥2 个不同取值的参数，排除 `_` 前缀簿记参数与结果回显的输入值）；证据累计曲线+决策时间线。全部从台账确定性绘制，含失败、不做美化。
- **测试**：新增 test_reporting.py（总结/版本历史/未收束不编造结论/viz 冒烟）+ round_callback 注入与叫停测试 + 对话审批（c→修订→y；EOF 不批准）测试。**92 passed**。修复两处集成缺陷：_interactive_approval 原先不返回修订后的新计划（调用方拿旧版继续跑）；收束轮原先绕过回调语义（改为显式 _terminal 分支）。
- **TFIM 示例重生成**（research-projects/tfim_cft_scaling/regen_report.py，只读台账）：report.md 102K 字符带全部新节（结论=accept 决策 rationale 原文；计划 v2/v4/v6 逐版 critic 结论与修订说明可见）；plots/ 三张 PNG 落盘。
- 文档同步：user_guide（§8.2 对话审批 / §8.3 round_callback / §8.4 可视化 / §8.6 报告结构）、manual（§11.2–11.4 + 模块地图）、README（功能总览行）。
- **待办：DSH 站内 agent 的写入沙箱**——DSH runtime 以 `cwd=项目根` 运行，站内 agent 会自行在仓库写草稿脚本（Phase 2 演示期间产生了 `ed_heis.py`/`corr_heis.py`，已移入 `research_data/demo_phase2/sandbox/` 存档）。实验层已隔离（`research_data/experiments/<exp_id>/`），但 DSH 站点调用的 `cwd` 仍指向项目根；应在 dsh_client/loop 层为每项目设沙箱目录。

### 2026-09-14（计划 v3 M0+M1：评审修订 + 内核接口化）
- **计划 v3 评审修订 A1–A7 入库**（commit `ce13f9d`）：A1 审批台账仲裁+CLI 审批台+TTY 守卫 / A2 异步 job 协议扩到站点调用（实测 3–5 分钟，60s 工具超时下同步必超时→agent 误判重跑）/ A3 引擎核心全同步、jobs.py 仅 MCP 层 / A4 Windows 沙箱部分执行→L4-3 判据放宽 / A5 验收数字与归一化事件比对 / A6 adhoc 语义（bash 现算永不可验证；注册工具路径可升级）/ A7 MCP server 内嵌自己的 DSHClient。
- **M0 基线**（commit `843c7dd`）：`examples/compare_events.py`——归一化事件序列比对（剥离时间戳与每次运行新生成的 id/路径/elapsed），支持多 events.jsonl 串接；四条基线入库 `tests/baselines/{phase2,phase5,phase8,resume}.json`（14/64/164/2 条事件；phase8 为 6 个事件文件的串接）。L1 门从"字面 diff"升级为可执行的归一化回归。修 phase2 demo 离线剧本陈旧格式（parameter_scan 需 `inputs.scan` 包裹）。
- **M1 内核接口化**（commit `e06c160`）：`qresearch/engine.py`——`ResearchEngine` 唯一科研入口（open/understand/hypothesize/plan_create(+revise 语义)/approve/reject/experiments_run/verify/analyze/decide/adhoc_record/adhoc_verify/report_generate/plot/status/events/ledger/resume_state），report 函数随迁；`qresearch/jobs.py`——进程内异步 job 注册表（submit/status/result/cancel/set_progress；**同 key 活跃 job 幂等**（D4 防重复实验）；pending 可取消、running 只置请求不谎报）；`research_loop.py`/`loop.py` 收口为薄驱动（公共签名不变，session/orchestrator/examples 零改动）。**A3 铁律：引擎方法全同步，异步只是 MCP 层封装**。
- 配套：SQLite 线程安全（`check_same_thread=False` + RLock，job worker 线程写台账）；`tests/test_engine.py` 9 项（只读零写入/execute+verify/adhoc 诚实边界/审批事件/job 生命周期与幂等/异步端到端）。
- **L1 门全绿**：113 tests passed + 四 demo 归一化事件序列与基线**零漂移**（重构前后行为逐字一致）。测试净增 104→113（既有测试文件零改动）。

### 2026-09-14（计划 v3 M2：MCP 接入 + 审批台 + skill + profile）
- **`qresearch/mcp_server.py`**：22 个工具（mcp 2.x，`MCPServer`/stdio；FastMCP 已更名）。要点：① **A2** 全部长工具（understand/hypothesize/plan_create/plan_revise/verify/analyze/decide/experiments_run/adhoc_verify）返回 job_id 轮询，同 key 活跃幂等；② **A1** `plan_approve` 是**纯台账仲裁**——不收 actor 参数、不写批准，只查 actor=HUMAN 事件并返回操作指引（工具 schema 实测仅 project_id+plan_id）；③ **D6（比计划更严的有意偏离）**：不设独立 `declare_result` 工具——结论只能经 decide 站点产生（引擎无条件 requires_human=True），agent 没有"自己宣布结论"的工具面；④ 错误统一 `{"error": ...}` dict（模型可读原因、通道不炸）；⑤ `_LazyClient` 按项目惰性创建 DSHClient（读工具零 runtime 子进程）；目录契约与 orchestrator 一致（`<root>/<pid>/{state.sqlite, events.jsonl, sandbox/, experiments/}`）。
- **`qresearch/ui/approvals.py` + session.py 子命令分发**：`qresearch approvals/approve/reject/conclude`——**actor=HUMAN 审批事件的唯一写入口**。TTY 守卫 fail-closed（非 TTY 即拒）+ 显式 y 确认 + 批准前展示计划简报；`conclude`（D6）只对 requires_human=True 的决策开放、重复确认幂等、落 `conclude` 事件（actor=HUMAN）并刷新报告；`approvals` 只读列出全部待办。
- **engine.report_generate 签名微调**：`status=None` 时从台账推导——存在 conclude 事件 → `concluded`，否则 `terminated`（agent 之后再生成报告不会冲掉人工结论状态；轮内驱动显式传 status 不受影响）。
- **`qresearch/skills/research-protocol/SKILL.md`**：研究协议包（标准流程/审批指引/adhoc 纪律/禁止事项，对应计划 M2 第 3 条）。
- **`examples/setup_dsh_profile.py`**：research profile 落盘到 `<DSH_HOME>/profiles/research/`（package.json 与 web 同底座 dsh-base+dsh-web-app；cordis.yml 空根；patch 默认不覆盖已有——`--force` 重写）。**实测发现并修正 patch 语法**：loader 里 `id:` 定位只用于覆盖既有条目，**新增插件条目必须经 `insert:` 追加**（直接写 `- id: mcp-qresearch` 会 "entry not found" 被静默跳过）——计划文档片段已同步修正。真实 `~/.dsh` 安装后 `dsh --profile research --dump-config` **零警告**、mcp-qresearch/sandbox-policy(workspace-write)/approval(ask) 全部挂载（D7）。
- **`examples/mcp_smoke.py`**：真实 stdio 子进程冒烟（与 DSH mcp-client 同契约）——22 工具清单 / research_open / **只读工具字节级零写入**（L2）/ A1 仲裁 / 错误形状 / adhoc_verify job 协议与后台失败可查。
- **`tests/test_mcp_server.py` 6 项**：工具面与 A1 schema（无 actor 参数）/ 只读零写入 / A1 仲裁+TTY 守卫全链（MCP 查→CLI 写→MCP 再查）/ D5 未批禁执行 / **A2 全链**（understand→hypothesize→plan_create→CLI 批准→experiments_run(幂等)→verify→ledger→analyze→decide(declare_result, requires_human)→CLI conclude(幂等)→报告 concluded）/ adhoc 诚实边界。测试 113→**119 passed**。
- **M2 完成标志**：L2 离线断言全过（smoke + pytest 双通道）；L4 反例的 MCP 面种子已入测试（L4-1/2/4/6 对应 adhoc/结论/检查项语义；L4-3 留待 live，L4-5 引用校验在 decide validator）；**L3 待人工**：`dsh --profile research` 五步走查（见计划 §4.2 M2 检验 4）。


### 2026-09-15（L3 第二轮实测三修复：工具子进程相对路径 / ANALYZE 零资格死锁 / 输入框两线框）
- **工具子进程相对路径 bug（本轮 27 个实验全败的根因，框架级）**：交互式会话默认 `--data-root research_data`（相对路径）→ `experiments_root` 相对 → `inputs.json` 路径以相对串传给工具子进程，而子进程 `cwd=workspace` 自身 → 在子进程里解析到错误位置，`FileNotFoundError`（文件明明在）。phase8 demo 未触发是因为其 root 走 `ROOT`（绝对）。修复：`ExperimentManager.root` 一律 `.resolve()`（Slurm runner 同享）；27 个失败台账保留（诚实记录），重跑即好。
- **ANALYZE 零资格死锁**：全部实验失败 → 可引用实验区渲染"（本轮无已通过验证的实验）"，而 prompt 只说 experiment_ids 输出空数组、schema 却要求每条 observation 至少挂 1 个 id——模型被两条矛盾指令夹住，3 次尝试全 ValidationError → needs_human。修复 prompt 契约：零资格时 observations 与 interpretations 都必须为空数组（id 无处可挂），本轮教训写进 uncertainties。
- **输入框两线框**（用户反馈："一堆字里看不见在哪输入"）：新增 `ui/prompt.py`（`framed_prompt`/`framed_block`，纯制表符 + GBK 兼容 ※，宽度取终端实宽）；接入 REPL 主提示、开题确认环、审批菜单、[e] 回读确认、多行意见引导、轮末插话、/resume 轮数询问——每个交互点渲染为"上线/※ 提示/下线/>> 光标"。
- **报告"核心结果"置顶**（用户反馈：结果不该埋在实验表里）：研究总结节在状态/结论之后新增**核心结果**块——有通过验证的实验则逐条列关键数值（E0/e0/gap…，最多 8 条+余数指引）；**全失败时明说"无通过验证的实验（失败 N/M，首要原因：…）"**（首要原因=失败错误聚类众数；experiment row 增加 error 字段）。不再出现"开头只有状态、结果要翻到实验表才知道全败了"。
- **计划宁简勿繁**（用户反馈：计划过于复杂）：PLAN prompt 首条要求=第一版计划是**最小可信实验集**（通常 ≤6 步、单点先行、扫描小网格 2–3 取值，先回答"算得通+对照吻合"再扩展）；CRITIC 新增攻击点：过度规划（第一轮大网格/大量同质步骤）按 blocker 处理。
- **测试 +2（153→155）**：报告核心结果置顶（成功列数值）/ 全失败给首要原因（换 FailingRunner 离线验证全败闭环：失败→analyze 零资格空数组→decide）/ 输入框渲染契约。


### 2026-09-15（审批菜单语义修复：回车≠批准 / 单字符不进修订环 / critic 问题首次可见）
- **动机**（L3 走查第二轮实测）：审批菜单按回车被落进"意见通道"（空文本→"空意见，未修订"→重出菜单），审批人以为回车=同意，感觉"一直在思考没在跑"；随后单字符手滑（想按 y 打成 t）被当成修改意见，白白触发一轮数分钟的 plan↔critic 修订；且 critic 判 revise 送审时的 9 个问题从未展示，审批人不知道 critic 在纠结什么、也不知道等待花在哪。台账回放证实全程无卡死——每个站点调用 1~3.5 分钟（understand 1min → hypothesize 3.3min → plan 1.4min → critic 1.7min → 自动修订再评），是真实 LLM 工作量，但 UI 没有把"在做什么、为什么不满意"讲出来。
- **三处修复（loop.py `_interactive_approval`）**：① 空输入明示"回车不批准——批准请按 y"后重问（不落意见通道）；② 非命令单字符（len<2）按手滑处理，提示后重问——不再触发 LLM 修订环；③ critic 判 revise 送审时菜单上方列出主要问题（severity/步骤/描述，最多 3 条+余数指引）；意见通道触发修订时先打印"按意见修订计划中（约需数分钟）"。
- **测试 +3（149→152）**：回车非批准 / 单字符非意见（均断言零 plan_feedback、版本不增）/ revise 问题清单展示。
- **文档**：user_guide §8.2（回车语义、critic 问题展示、批准后耗时预期）。


### 2026-09-15（交互式会话三处结构性修复：确认环 / understand blocking 闸门 / 会话外编辑警示）
- **动机**（L3 走查实测踩坑）：会话里误输 `y` 被当成研究问题建项目开跑；UNDERSTAND 站点**诚实**判定"问题不可解析"（constraints.blocking=true + requires_clarification 清单），但闭环不读该信号——hypothesize→plan→critic 空转 8 次调用产出 17 步垃圾计划；用户随后在会话外直接改 plan_v2.md，v3 毫无变化且无任何提示。诊断结论：**LLM 接入正常且行为正确**（台账 station_calls 可查），坏在闭环代码忽略 blocking 信号。
- **F1 understand blocking 闸门（engine）**：`_require_parseable_goal`——understand 一返回即检查 Goal.constraints：blocking=true 或 requires_clarification 非空 → 落 `understand_blocked` 事件 → 抛 NeedsHuman（理由带澄清清单，人可照着补）。**确定性闸门**：结构化字段说话，LLM 无法"顺便"绕过；测试证实闸后零计划/零审批/零假设。UNDERSTAND prompt 增第 5 条契约：不可解析时 blocking=true + 逐项清单 + quantities 以"（待确认）"占位，**不许虚构物理系统**。
- **F2 会话确认环（ui/session.py）**：`start_project` 升级为确认循环——确认提示符处**直接输入修正后的问题就地替换**并重新确认（回车取消/y 开跑）；主提示符 <8 字符的碎片输入（如 `y`）直接提示"这不像研究问题"不再建项目。根因：此前主提示符的碎片成为问题、真实问题却在确认提示符被当无效回答吞掉。
- **F4 会话外编辑警示（loop.py）**：审批循环每次提示前比对计划文档内容，检测到会话外修改即明示"⚠ 不按 [e] 读回不会生效"；[e] 读回路径补"已读取文档（N 行）→ 转录 → critic"确认行。（v3 没变的直接原因：编辑没删掉污染的 constraints 块且无任何生效提示。）
- **测试 +5（144→149）**：`test_understand_blocking_stops_loop`（needs_human + 澄清清单 + 零计划/零假设 + 报告如实标注）；会话确认环（碎片 'y' → 就地改述真实 Hubbard 问题 → Project.question 正确）；确认取消不建目录；REPL 碎片输入零项目；审批循环会话外编辑警告。
- **文档**：user_guide §3.3（确认环语义 + understand 拦截 + 会话外编辑必须走 [e]）；manual 站点表 understand 行（诚实自报 blocking 契约）。


### 2026-09-15（审批通道与保真度审计 + dsh_client 两处修复）
- **动机**：日常使用中"批个计划/确认个结论都要去终端敲 y"太重——在**不放宽人工在场要求**的前提下，加一条便利通道，并用台账把两条通道的差别如实记账（不靠约定俗成）。
- **`ApprovalChannel`（core/status.py）**：tty / webui-local / system-auto / unspecified 四级。核心事实：TTY 是**进程能力**（agent 子进程结构性拿不到），webui-local 的凭据（URL token+cookie+CSRF）与 agent 同一 OS 用户信任域——**保真度较低**，台账必须如实记录，不得与 TTY 等同。历史事件读作"未记录"，不得反推为 tty。
- **`ui/approval_ops.py`（新）**：写路径唯一化——TTY 与浏览器两通道共用 `apply_approve/apply_reject/apply_conclude`（幂等、报告刷新）；`_require_human_channel` 结构性拒绝 system-auto/unspecified 写 actor=HUMAN（防伪造人工签字）。`pending_in_root/project` 只读待办（已被后续计划 `based_on_decision` 采纳的决策不再列为待办）。
- **`ui/web_approvals.py`（新）**：`qresearch approvals-web`——localhost 审批页（决策卡含 checklist/rationale，计划卡含步骤/critic/blocker/diff），URL token 首次进入 + HttpOnly SameSite=Strict cookie + CSRF，**只许回环地址**（非回环拒绝启动）；页面自陈"较低保真度通道"。Windows 细节：POST 先排空请求体再回包（避免 403 时客户端看到连接重置）。
- **`approvals.py` 重构**：TTY 路径行为不变（守卫 fail-closed + 显式 y），写操作改走 approval_ops（channel=tty）；`approvals list` 输出复用同一待办收集；编码安全打印（GBK 控制台不崩）。
- **保真度对读者可见**：`engine.status` 每条决策新增 `confirmed_by_human` + `channel`（与 `requires_human` 区分）；报告置顶总结单列"结论确认通道"一行并附保真度说明；`plan_approve/reject` 落账 channel（auto-approve=system-auto，缺省=unspecified）。**MCP 面未动**：plan_approve 仍纯查询（actor=HUMAN 即视为已批，channel 随事件可查）。
- **dsh_client 两处修复（L3 走查实战反馈）**：① runtime `finish_reason=error` 直抬为 `StationRuntimeError`，配置/凭据类错误码（缺 key/配额/模型不存在）立刻转 NeedsHuman 并带 runtime 原文——不再伪装成"回复里没有 JSON"空转三轮；② **会话 id 每次调用全新**（实例随机前缀+调用序号）：修复 DSH 对残留会话**重放旧回复**导致的"一次畸形输出被永久缓存"（重试/resume 也逃不掉）。
- **L1 基线重采（A5 纪律）**：会话 id 新增随机段属**有意行为变更**——compare_events 归一化新增 `:<tag>:` 剥离（nonce 与时间戳同类）；phase2/phase5/phase8 基线重采（14/64/164 条，逐条 diff 确认差异仅为 session tag 与 approve 事件的 channel 字段）；resume 基线正交不动。
- **测试 +14**：`tests/test_approval_channels.py`——结构性闸门拒非人工通道 / TTY 落账 channel=tty 且报告标注 / TTY 守卫不放松 / web token-cookie-CSRF 三重校验 / webui-local 落账+报告标注+幂等 / 批准与拒绝落账 / 非回环拒绝启动 / 空待办页 / 历史无 channel 事件不反推 tty / 未确认不出现通道行 / status 投影区分 required 与 confirmed / 待办收集与消失。全量 144 passed。


### 2026-09-14（计划 v3 M3：体验与运维补齐）
- **协作式取消全栈**（jobs → engine → manager）：`JobManager.cancel_requested(job_id)`/`find_active(key)` 公开；`engine.experiments_run(cancel_check=)` 检查点直通 `ExperimentManager`——**准备段**（execute_plan 步骤循环与 scan 网格循环）取消即不再建账；**执行段**（_finish_prepared 串行与并行两路）已建账未执行的按取消落账：FAILED + `error="CancelledError: 人工取消（job_cancel），实验未执行"` + 事件标 `status=cancelled`（不引入 CANCELLED 状态：验证层对 FAILED 的处理天然适用）；**已在跑的照常完成**（协作式，不杀进程，不谎报）。`experiments_run_async` 回接 `cancel_requested` 形成 job_cancel → 检查点闭环；async 同 key 活跃时复用同一 job（幂等不重复提交）。
- **plan_show markdown 卡片**：`render_plan_markdown(..., edit_hint=False)`——目标量/步骤表/风险/critic 结论与 blocker/diff_summary 渲染成 markdown 随 `plan_show.markdown` 返回（给 `dsh web` 直接渲染；编辑提示不属于 MCP 面）。
- **adhoc 蒸馏**（memory/distill.py）：未验证 adhoc 记录（`plan_id==""` 且 `step_id` 以 `adhoc_` 开头、COMPLETED 但 NOT_RUN）蒸馏为失败案例层经验——"bash 现算不可引用为证据，要进证据链必须用注册工具跑并 adhoc_verify"（A6 的记忆侧闭环）。
- **SKILL.md 节点汇报模板**：固定模板（轮次/实验/证据/决策建议/下一步），字段全部取自 `research_status`/`job_status`，requires_human 时必须注明等待人工——约束 agent 汇报不自由发挥。
- **权限预设**（examples/setup_dsh_profile.py）：permission 插件 presets 全量 map（内置 3 个 + `research-interactive` workspace-write+ask / `research-unattended` read-only+ask）——注意 patch config 是顶层浅替换，预设必须带全量；真实 `~/.dsh` `--force` 重写后 `--dump-config` 复核零警告。
- **测试 +7（113→119→125 passed）**：`tests/test_m3_ops.py` 5 项——取消先于执行（零账目）/ job_cancel 协作取消（在跑完成+未开取消落账+事件诚实）/ **混跑连续性**（交互式 1 轮 actor=HUMAN + 无人值守 2 轮 actor=SYSTEM → 计划版本 v1..v3 连续、证据累计 6 条、报告含全部轮次）/ adhoc 蒸馏失败案例 / viz 冒烟（计划+adhoc 混合台账 3 图）；`test_mcp_server.py` +1（plan_show markdown 卡片：无 critic/critic-pass/blocker 三态与 edit_hint=False）。全量 125 passed（exit 0），L1 基线不受 cancel_check 默认值影响。
- **文档**：README（状态+功能表 MCP 行）/ user_guide §3.5（交互式研究：profile 落盘→dump-config→五步走查→两条硬边界→job/adhoc 要点）/ manual §14（engine 门面 + JobManager + MCP 22 工具 + CLI 审批台 + M3 运维，旧 14–17 顺延 15–18）。
- **M3 完成标志**：计划 §4.3 检验 1（取消诚实落账）/ 2（混跑连续性）/ 3（viz）均入 pytest；汇报模板与权限预设落盘；**L3 待人工**：`dsh --profile research` live 走查（顺带实证 DSH bash 无 TTY——A1 TTY 守卫在壳内的真实行为）。


### 2026-09-14（Phase 9 完成：交互层——CLI 会话 / 计划文档 / 实时进度）
- **P9.1 实时进度**：EventLog 加 `subscribe()` 订阅钩子（追加时同步回调，订阅者异常被吞——显示层永不干扰记账）；dsh_client 每次尝试前发 `station_started` 事件。`qresearch/ui/progress.py ConsoleProgress`：rich Live 单行刷新（spinner + "站点 X 调用中（第 N 次尝试）" + 统计行：轮次/实验完成失败/运行中/验证/已用时间），里程碑事件（plan_ready/approve/decide/实验完成…）持久打印；rich 缺失退化 `
` 单行刷新。修 rich 15.0.0 Live 无 get_render → `_Dynamic.__rich_console__` 动态渲染包装。
- **P9.2 计划文档 + 双通道审批**：每版计划自动渲染为 `<项目目录>/plans/plan_vN.md`（ui/plan_doc.py，确定性渲染含步骤/输入 yaml/预期输出/风险/diff/critic 结论与 ⛔ blocker 标记，文末固定"修改意见"节）。审批菜单升级 `[y]/[c]/[e]/[s]/[q]`：意见通道支持**口述一长串**（任意自由文本）或 c 多行（空行结束）；**编辑通道 [e]**——用户直接改文档 → 回车 → `transcribe` 站点（第七站点）把编辑忠实转录回结构化计划 → 同一语义 validator（`_validate_plan_steps` 抽为共享函数）→ critic 独立重审 → 新版再批——**用户编辑不绕过验证管线**。事件留痕：`plan_feedback`（mode=verbal/edit_doc，actor=HUMAN）、`plan_transcribed`、`plan_ready`（from_user_edit/from_user_feedback）。
- **P9.3 CLI 会话**：`pyproject.toml [project.scripts]` 注册 `qresearch` 命令（`[ui]` extra，rich 入依赖）。`ui/session.py` REPL：自然语言问题确认后跑 `run_research_loop(auto_approve=False, round_callback=…)`，全程 ConsoleProgress；斜杠命令 /projects /status /report /plots /pause /resume /quit（无参时单项目自动识别）；轮末交互点回车继续/输入意见（注入下一版计划）/stop 叫停；Ctrl+C 一次=轮末优雅停（stop_requested → 回调 "stop"），两次=立即中断（台账安全，/resume 续跑）。单线程 + 终端 typeahead：实验执行期间敲的字在下一个交互点被读，无需并发。设计边界：会话层零研究决策、零台账写入，只把用户的话路由成参数/反馈/命令。
- **测试**：新增 tests/test_ui.py 12 项（订阅容错 / station_started / 进度计数与 Live 冒烟 / 文档渲染与 split_user_notes / 口述意见 / 多行 c / 编辑通道全流程 / EOF 不批准 / 会话命令 / 会话开题端到端 / pause 旗标 / 轮末 stop 语义）。既有测试同步 P9 语义（station_started 每尝试一条；多行意见需空行收尾）。**104 passed**。实现期修复：_validate_plan_steps 抽取时丢了延迟导入（NameError）；save_plan_doc 目录契约统一为"传项目目录，函数自加 plans/"（此前调用方传 plans/ 导致 plans/plans/）。
- 文档同步：user_guide（§2 [ui,viz] 安装、§3.3 交互式会话、§8.2 双通道审批、§8.5 事件清单）、manual（模块地图 + ui 包、§4.4 subscribe、§6 七站点+transcribe、§11.2 双通道、§11.5 交互层）、README（状态行/功能总览/最小示例/测试数）。
- 用户验收路径：`uv pip install -e ".[ui,viz]"` 后 `qresearch`——输入问题开跑；运行中看 spinner；审批时改 `plans/plan_vN.md` 或直接打一段话；轮末回车/插话/stop。
