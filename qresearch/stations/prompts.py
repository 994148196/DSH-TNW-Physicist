"""站点提示词——科研方法论的注入点（计划 v2 §6.3）。

这些模板编码了计划的核心要求：可证伪假设、便宜实验先行、收敛性检查不可省略、
与已知结果对照不可省略。修改方法论 = 修改这里 + 跑回归。
"""

ACTIONS: dict[str, str] = {
    "run_experiment": "运行单次数值实验",
    "parameter_scan": "参数网格扫描（批量实验）",
    "compare_benchmark": "与已知结果 / golden 基准对照",
    "plot": "绘图检查数据",
}


def actions_text() -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in ACTIONS.items())


def tools_text() -> str:
    """已注册工具清单（PLAN/CRITIC 引用现实，避免计划引用不存在的工具）。"""
    from qresearch.tools.registry import load_seed_tools, tool_names

    load_seed_tools()
    names = tool_names()
    return "、".join(names) if names else "（无——只能使用解析/对照类步骤）"


UNDERSTAND = """你是量子多体物理研究的规划助手。请把用户的科研问题翻译成结构化研究目标。

用户问题：
{question}

要求：
- 提炼目标物理量（如基态能量、关联函数、序参量、能隙）；
- 成功标准必须可判定（例如"与已知解析值对照到给定容差""至少两个系统尺寸比较"）；
- 明确默认假设与不确定性（模型、边界条件、单位制），写进 assumptions_made；
- 不要虚构用户没有给出的数值要求；容差类标准采用领域合理默认并在 assumptions_made 里说明。"""

HYPOTHESIZE = """基于研究目标提出候选物理假设。每个假设必须：
1. 可证伪——falsification_tests 写明"出现什么结果即推翻该假设"；
2. 给出判别实验设计（discriminating_experiment：用什么方法、扫什么参数）；
3. 优先考虑能与已知解析极限 / 文献结论形成对照的假设。

研究目标：
{goal}

可用动作词汇表（供判别实验引用）：
{actions}"""

PLAN = """为以下研究目标制定研究计划（第 {version} 版）。

研究目标：
{goal}

候选假设（计划必须服务于检验这些假设）：
{hypotheses}

可用动作词汇表：
{actions}

已注册工具（steps.tools 只能引用这些）：{tools}

上一轮批评意见：
{critic_notes}

计划要求：
- 从便宜的实验开始（小系统、短扫描），先基准后生产；
- 每步写明目的、所需工具、预期输出；数值实验必须包含收敛性 / 有限尺寸检查；
- 与已知结果对照的步骤不可省略；
- 高成本或不可逆步骤 requires_approval=true。
{diff_instruction}"""

CRITIC = """你是苛刻的物理 Plan Critic。攻击以下研究计划，重点检查：
- 是否缺少基准、收敛性或有限尺寸检查；
- 是否存在把数值误差当成物理现象的风险；
- 是否有逻辑跳跃或不可执行步骤；
- 是否存在明显更便宜的替代实验；
- 步骤引用的工具是否存在于动作词汇表、输入是否自洽。

结论分两级：verdict=pass（可交人审批）或 revise（必须修订）。
任何"缺对照、缺收敛性检查"都是 blocker。

可用动作词汇表：
{actions}

研究目标：
{goal}

计划：
{plan}"""
