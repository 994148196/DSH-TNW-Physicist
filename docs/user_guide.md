# qresearch 使用者指南

> 给"想用它做研究"的人：怎么跑起来、能得到什么、四个真实案例、怎么读懂输出。
> 实现原理与全部技术细节见 [manual.md](manual.md)。

---

## 1. 这是什么

qresearch 是一个**自主科研闭环系统**：你给它一个凝聚态物理的研究问题
（比如"一维 Heisenberg 链的基态能量与 Bethe ansatz 是否一致"），它自动：

1. 理解问题、提出可证伪的假设；
2. 制定数值实验计划 → **交你审批**（这是它唯一必须等你点头的地方）；
3. 执行实验（精确对角化 / DMRG 等，不经 LLM）→ 三层验证（程序/算法/物理）；
4. 分析证据、决定下一步（继续迭代 / 换路线 / 终止 / 宣布结论）；
5. 循环 2–4 直到给出结论，产出**研究报告 + 完整证据链 + 可回放的操作日志**。

多条经验会沉淀到**跨项目记忆库**：上个项目踩过的坑（比如"这个工具要求偶数
系统尺寸"），下个项目开始前就会被检索出来提醒，同样的失败不会重演。

你能得到：一份自动生成的 `report.md`、一条每个结论都能回溯到具体实验的
证据链、一份逐动作的事件日志、一个越用越准的经验库。**它不做没有来源的
断言**——验证不过的实验不能当证据，dry-run 不冒充真计算，宣布结论必须你确认。

---

## 2. 安装与准备

环境要求：Windows / git-bash（或相应 shell）、Python ≥3.10、Node.js。

```bash
# 1) 安装依赖（项目根目录）
uv pip install -e .                      # 或 pip install -e .

# 2) DSH runtime（首次）：项目内隔离安装，不动全局
#    已随仓库就位则跳过（.dsh-runtime/ 目录存在即可）

# 3) API 凭据（已在环境中则跳过；值不会被打印/记录）
export DEEPSEEK_API_KEY=sk-...

# 4) 连通性自检（真实轮次，~1 分钟）
.venv/Scripts/python.exe -X utf8 examples/dsh_smoke_test.py
```

无需 API key 也能跑：所有 `examples/phase*_demo.py` 不加 `--live` 时使用
离线脚本模型（秒级、确定性），适合先熟悉流程。

---

## 3. 快速上手

### 3.1 三分钟看全流程（离线）

```bash
.venv/Scripts/python.exe -X utf8 examples/phase5_demo.py
```

离线跑一个 3 轮的完整研究闭环：决策链 `iterate→iterate→declare_result`、
6 条证据、事件日志落盘、报告生成。产物在 `research_data/demo_phase5/`。

### 3.2 跑你自己的问题（真实 LLM，最小脚本）

```python
# my_research.py —— 放在项目根目录
from pathlib import Path
from qresearch.core.events import EventLog
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient
from qresearch.memory import MemoryStore
from qresearch.research_loop import run_research_loop

PID = "my_first_run"
DATA = Path("research_data") / PID
DATA.mkdir(parents=True, exist_ok=True)

client = DSHClient(cwd=DATA / "sandbox", dsh_home=DATA / "dsh_home")
storage = Storage(DATA / "state.sqlite")
log = EventLog(DATA / "events.jsonl")
memory = MemoryStore(Path("research_data/memory.sqlite"),
                     markdown_path=Path("research_data/memory.md"))
try:
    summary = run_research_loop(
        client, storage, log, PID,
        "一维自旋 1/2 Heisenberg 链（PBC, J=1）基态能量随 N 的收敛，"
        "并与 Bethe ansatz 热力学极限 e0=1/4-ln2 对照",
        rounds=3, auto_approve=True,     # 首次体验可 auto；正式研究请留人工审批
        memory_store=memory,
    )
    print(summary)
finally:
    client.close(); storage.close()
```

```bash
.venv/Scripts/python.exe -X utf8 my_research.py
```

耗时预期：每站点 1–5 分钟（模型 deepseek-v4-flash），3 轮约 20–40 分钟。
去看 `research_data/my_first_run/`：`report.md`（报告）、`events.jsonl`
（逐动作日志）、`sandbox/`（站内 agent 的草稿，被隔离在这里）。

> 建议路径：先用 `auto_approve=True` 跑通 → 正式研究改回 `False`，每轮审批
> 计划（审批动作会以你的身份留痕，而非 system）。

### 3.3 一行没跑完怎么办？

进程可以中断，状态在库里。重入时改用恢复入口：

```python
from qresearch.research_loop import resume_research_loop
summary = resume_research_loop(client, storage, log, PID, rounds=3, ...)
```

计划版本、决策、证据全部从台账衔接（见案例四的实测）。

---

## 4. 案例一：Heisenberg 链基态研究（真实 LLM 实测）

这是系统完成的第一个真实研究项目（`research_data/demo_phase8_live/projects/proj_a/`，
2 轮、约 20 分钟、151 个事件）：

**第 1 轮**：模型规划了 8 个偶数尺寸（N=6..20）的 ED 单点 + 扫描，共 16 个实验，
全部通过三层验证。分析得出：

> 能量密度按 1/N² 外推与 Bethe ansatz 热力学极限的相对偏差 **1.77e-4**
> （含 1/N⁴ 修正项为 7.2e-6），远优于预设判据。

**决策 iterate**——不满足于单点，下一轮换 DMRG 路线扩大尺寸（17 个 dmrg 实验
因本机 quimb 缺陷失败，**如实落账**，ED 路线继续）。

**第 2 轮结束**：模型给出 terminate 建议，rationale 里带着对证据覆盖的自我
批评（"本轮可核验证据只覆盖 N=4 与 N=20 两个尺寸，且 N=4 在预注册拟合窗口
N≥8 之外"）——不是套话，是对自己结果的审视。

**你可以复现**：

```bash
.venv/Scripts/python.exe -X utf8 examples/phase8_demo.py --live
```

（演示跑两个项目：proj_a 同款 Heisenberg 研究 + proj_b 横场 Ising。）

**怎么读结果**：打开 `proj_a/report.md`——假设区每条挂着证伪试验的命运
（支持/反驳/未判定），实验表每行是"步骤×工具×验证状态×关键数值"，决策记录
的每个 passed 检查项都引用具体 evidence id，而每条 evidence 又指向
`experiments/<exp_id>/result.json`。链条从结论一路闭合到原始数值文件。

## 5. 案例二：缺一个工具？让它自己造（tfim_ed）

研究横场 Ising 模型需要 `tfim_ed`，工具库里没有。写一份 Spec
（`tool_specs/tfim_ed.yaml`：目的、输入模型、物理断言、golden 定稿）：

```bash
.venv/Scripts/python.exe -X utf8 examples/phase6_demo.py --live
```

发生什么：

1. **Spec 先行**：golden 基准的数值来自解析/文献/独立实现，由出题侧锁定；
   编码站的 prompt 里**没有任何基准数值**（有测试断言这件事）。
2. DSH 编码站交付实现 + 自测脚本（要求自带独立 oracle、声明未用基准常数）。
3. 交付实现跑 golden：首轮 **8/8 通过**（Lanczos/稠密双后端、能隙到 8.57e-12 量级）。
4. 批评者独立审查：对"能隙何时归零"给出自己的参考值实测复核。
5. 你审批 → 注册 → 可被研究循环使用。审查通过后**晋升**：交付代码逐字节
   进入 `qresearch/tools/`（只追加注册块），diff 可追溯到 Spec。

值得一提的是插曲：Spec 初版写了一句错误的物理预期（"奇数 N 铁磁有受挫"），
**构建者用全枚举 + 解析论证反驳了它**，出题侧核对后采纳修订。"出题人≠答题人"
是双向的——出题侧错了也会被纠正。

## 6. 案例三：经验跨项目复用（不再踩同一个坑）

```bash
.venv/Scripts/python.exe -X utf8 examples/phase7_demo.py
```

演示里"旧项目"故意安排了一个必然失败的实验（`simple_ed` 被喂了奇数 N）。
项目结束后，这条教训被确定性蒸馏进记忆库的**失败案例层**。随后"新项目"
（同问题域的关联函数研究）在制定计划和做决策时，prompt 里被自动注入：

> 失败案例：simple_ed 要求偶数 N（本实验因奇数 N 失败）——
> 与拟议步骤雷同时，必须先修正做法或明确写出差异。

新项目的计划确实避开了奇数 N，干净收尾。**机制的可靠性来自确定性**：
每条记忆都是代码从台账提取的（哪次实验、什么错误、什么教训），可以一路
回放——记忆库里没有"模型总结出来的印象"。

Markdown 镜像（`research_data/memory.md`）供你人工翻阅，四个层各一节。

## 7. 案例四：把它当研究基础设施来运维

全部演示：`.venv/Scripts/python.exe -X utf8 examples/phase8_demo.py`（`--live`
则第 5 段接真实 LLM）。五段分别演示：

**并行实验**：计划里的多个独立实验并发跑（`max_parallel_experiments`），
4 个 0.25s 的实验 0.30s 完成；落账顺序仍然确定（先全 started 再逐个 finished）。

**预算闸**：`Budget(max_rounds=5, max_experiments=2)`——第 1 轮跑满 2 个实验后，
第 2 轮开始前被代码层拦停：

```
status=budget_exhausted，实验数预算耗尽（2/2），rounds_used=1
```

预算是**你的**执行控制，写在代码层，模型建议不能越过。

**暂停/恢复**：跑 1 轮 → `touch <项目目录>/PAUSE` → 下一轮边界优雅暂停
（`status=paused`）→ 删除旗标 → `resume_research_loop` 续跑至终止。实测计划
版本 [1,2,3]、决策/证据链全程衔接，进程重启也不丢（状态在 SQLite 里）。

**多项目队列**：`run_projects` 串行跑一批项目，每项目独立目录 + 独立沙箱
（DSH 站内 agent 的写入被隔离），经验共享进同一个记忆库。

**Slurm（HPC）**：一行切换执行后端——

```python
from qresearch.hpc import SlurmConfig, SlurmRunner
mgr = ExperimentManager(storage, log, runner=SlurmRunner(SlurmConfig(
    partition="gpu", time_min=90, mem_gb=8, cpus=4, account="qm"), dry_run=False))
```

注意诚实边界：本机没有集群时保持默认 `dry_run=True`，它只生成/校验 sbatch
脚本、不提交；dry-run 的结果**过不了证据门**（不是真实计算就不许当证据）。
`submit_cmd` 支持换成 `ssh user@cluster sbatch` 形态跨机提交。真实提交路径
需在集群上验收。

---

## 8. 读懂输出

### 8.1 run_research_loop 的返回与状态语义

| status | 含义 | 你该做什么 |
|---|---|---|
| `terminated` | 模型判定信息增益耗尽/结论成立，建议终止 | 读报告；terminate 也建议人工复核 |
| `budget_exhausted` | 你设的预算（轮数/实验数/墙钟）用完 | 加预算 resume 续跑，或就此收尾 |
| `paused` | PAUSE 旗标生效，轮间优雅暂停 | 删旗标后 resume |
| `needs_human`（含轮次与原因） | 校验重试耗尽 / 末轮还想迭代 / runtime 异常 | 按原因处理：多半是修正做法后 resume，或人工接管该轮 |
| `report` / `rounds_used` / `reason` / `decisions` | summary 附带报告路径、进度、原因、决策 id 列表 | — |

### 8.2 审批触点

需要你的地方只有两类：**每轮计划审批**（plan 通过 critic 之后；`auto_approve=True`
时代行但以 system 留痕注明"非人工"）和 **declare_result/terminate 确认**
（无条件人工，模型层强制，代码不代行）。

### 8.3 事件日志（events.jsonl）

一行一个动作 JSON，按时间序即完整历史。高频事件：
`station_call`（站点成功）/ `station_retry`（runtime 层失败重试，含错误）/
`approve` / `experiment_started|finished` / `verify_experiment` / `analyze` /
`decide` / `memory_retrieved|written` / `budget_exhausted` / `paused|resumed` /
`needs_human` / `report_generated`。调试先看它。

### 8.4 报告（report.md）

确定性骨架生成：研究问题与目标量 → 假设（含证伪试验命运）→ 计划（含风险）→
实验表（步骤×工具×验证状态×关键结果）→ 分析结论（每条挂实验 id）→ 决策记录
（每个 passed 检查项挂 evidence id）→ 局限与不确定性。**对外发布前需人工复核**。

---

## 9. 常见问题

**Q：站内 agent 在我的仓库里写文件吗？**
不会。DSH 站内 agent 的 cwd 是项目沙箱（`<项目目录>/sandbox/`），草稿都写在那里。
直接用 `DSHClient()` 且不传 `cwd` 时才指向仓库根——正式使用请总是传沙箱。

**Q：站点调用卡住很久？**
正常站点 1–5 分钟。默认 15 分钟看门狗会重启 runtime 并自动重试（事件日志里
出现 `station_retry`）；重试耗尽转 `needs_human`，不会无限挂死。环境很慢可调
`QRESEARCH_STATION_TIMEOUT_S`。

**Q：dmrg 实验大批失败？**
本机 quimb 1.15 的已知平台缺陷（局部求值器 NaN），失败已如实落账且
dmrg_adapter 会自动回退 MPO 稠密对角化（L≤10 与 ED 一致到 1e-15）。大规模
DMRG 建议换集群（Slurm 后端）。

**Q：重跑同一项目报 "session already exists"？**
DSH session 持久化在 dsh_home。换 project_id，或复用 `resume_research_loop`
从台账续跑（推荐，状态不丢）。

**Q：项目可以建在仓库目录之外吗？**
可以。所有路径都是显式参数：`Storage`/`EventLog`/`DSHClient(cwd=, dsh_home=)`/
`experiments_root`/`MemoryStore` 想放哪就放哪（`run_projects` 的 `data_root`
同理），报告自动生成在 `state.sqlite` 旁边。仓库里强绑定的只有代码与 DSH
runtime 二进制（按包位置解析，与数据位置无关）。注意**定好位置再跑**：实验
产物在台账里记绝对路径，跑完再挪目录会导致产物路径失效；DSH session 也按
沙箱绝对路径归档。

**Q：怎么加一个新工具？**
先写 Spec + golden（`tool_specs/`，数值来自解析/文献/独立实现），要么手写后
按 ToolSpec 注册，要么走 §5 的构建流程。工具被 golden 覆盖后才能产出 passed 证据。

**Q：模型建议的计划不靠谱？**
三道防线：PLAN 语义校验（工具名/动作/scan 格式不对直接打回重写）、critic
独立物理审查（blocker 强制修订）、你的审批。放开 `auto_approve` 前先看几轮
它规划的质量。

---

## 10. 去哪里看更多

- 实现细节与全部机制：[manual.md](manual.md)（技术手册）
- 开发过程与验收记录：[PROGRESS.md](../PROGRESS.md)
- 原始计划与验收标准：《基于DSH的量子多体自主科研系统开发计划_v2.md》§9
- 各阶段演示源码：`examples/phase2..8_demo.py`（每份文件头有"它演示什么"）
