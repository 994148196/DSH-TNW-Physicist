"""记忆检索注入（计划 v2 §7.9）：按需检索注入 PLAN / DECIDE prompt。

检索与格式化都是确定性代码（代码投影，与站点 prompt 的其他上下文同机制）；
LLM 决定如何利用，但不能虚构记忆库里没有的教训。
"""
from __future__ import annotations

from .store import MemoryEntry, MemoryLayer, MemoryStore

_EMPTY_DIGEST = "（记忆库暂无相关历史经验——首次研究或查询无命中）"


def memory_digest(
    store: MemoryStore | None,
    query: str,
    *,
    limit: int = 10,
) -> tuple[str, int]:
    """检索并格式化为 prompt 片段。返回 (digest, n_hits)。

    失败案例排最前（DECIDE/PLAN 的固定输入：先看哪些路走不通）。
    """
    if store is None:
        return _EMPTY_DIGEST, 0
    entries = store.search(query, limit=limit)
    if not entries:
        return _EMPTY_DIGEST, 0

    order = {MemoryLayer.failure: 0, MemoryLayer.method: 1,
             MemoryLayer.tool: 2, MemoryLayer.project: 3}
    sections: dict[MemoryLayer, list[MemoryEntry]] = {}
    for e in entries:
        sections.setdefault(e.layer, []).append(e)
    titles = {
        MemoryLayer.failure: "失败案例（无信息增益/失败实验——必须避开或说明差异）",
        MemoryLayer.method: "方法经验（被验证支撑的做法）",
        MemoryLayer.tool: "工具经验（实战表现）",
        MemoryLayer.project: "项目经验（相近问题）",
    }
    lines: list[str] = []
    for layer in sorted(sections, key=lambda l: order[l]):
        lines.append(f"### {titles[layer]}")
        for e in sections[layer]:
            lines.append(f"- {e.title}：{e.content}")
    return "\n".join(lines), len(entries)
