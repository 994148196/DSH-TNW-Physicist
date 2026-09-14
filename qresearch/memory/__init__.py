"""Research Memory（计划 v2 §7.9）：四层跨项目经验库。

- store.MemoryStore：SQLite + Markdown 镜像 + 确定性关键词检索；
- distill.distill_project：从项目台账确定性蒸馏四层经验；
- inject.memory_digest：按需检索并格式化为 PLAN/DECIDE 的 prompt 片段
  （失败案例优先——"哪些实验没有信息增益"是固定输入）。
"""
from .distill import distill_project
from .inject import memory_digest
from .store import MemoryEntry, MemoryLayer, MemoryStore, tokenize

__all__ = [
    "MemoryEntry", "MemoryLayer", "MemoryStore", "tokenize",
    "distill_project", "memory_digest",
]
