"""Research Memory（计划 v2 §7.9）：跨项目经验库。

四层：
- project  项目层——问题、假设命运、最终结论（复用对象：相似问题先看这里）；
- method   方法层——被验证支撑的分析/实验方法经验（外推形式、对照手段等）；
- tool     工具层——工具实战表现（使用/通过验证统计 + 观察到的限制）；
- failure  失败案例层——失败或无信息增益的实验与教训；DECIDE 与下一轮 PLAN
           的固定输入（计划 §7.9："哪些实验没有信息增益"显式记录）。

存储：自有 SQLite（跨项目，独立于每项目 state.sqlite）+ Markdown 镜像
（人可读）。检索：确定性关键词打分（向量检索是后续增强，不是本层依赖）。
"""
from __future__ import annotations

import json
import re
import sqlite3
from enum import Enum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from qresearch.core.models import utcnow

_LAYER_TITLES: dict[str, str] = {
    "project": "项目层",
    "method": "方法层",
    "tool": "工具层",
    "failure": "失败案例层",
}


class MemoryLayer(str, Enum):
    project = "project"
    method = "method"
    tool = "tool"
    failure = "failure"


class MemoryEntry(BaseModel):
    memory_id: str = Field(default_factory=lambda: f"mem_{uuid4().hex[:10]}")
    layer: MemoryLayer
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source_project: str
    refs: dict[str, str] = Field(default_factory=dict)  # experiment/decision/report 等 id
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())


_TOKEN_RE = re.compile(r"[0-9A-Za-z一-鿿]+")


def tokenize(text: str) -> list[str]:
    """检索分词：拉丁词按词切，中文按整串 + 2-gram（无分词器的确定性近似）。"""
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        tokens.append(raw)
        if not raw.isascii():  # 中文串：补 2-gram
            tokens.extend(raw[i:i + 2] for i in range(len(raw) - 1))
    return tokens


class MemoryStore:
    """跨项目经验库。一个实例对应一个 SQLite 文件 + 可选 Markdown 镜像。"""

    def __init__(self, db_path: str | Path, markdown_path: str | Path | None = None):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS memories ("
            "id TEXT PRIMARY KEY, layer TEXT NOT NULL, source_project TEXT NOT NULL, json TEXT NOT NULL)"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories (layer)")
        self._conn.commit()
        self.markdown_path = Path(markdown_path) if markdown_path else None

    def add(self, entry: MemoryEntry) -> MemoryEntry:
        self._conn.execute(
            "INSERT INTO memories (id, layer, source_project, json) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET json=excluded.json, layer=excluded.layer",
            (entry.memory_id, entry.layer.value, entry.source_project, entry.model_dump_json()),
        )
        self._conn.commit()
        self._write_markdown()
        return entry

    def get(self, memory_id: str) -> MemoryEntry | None:
        row = self._conn.execute(
            "SELECT json FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        return MemoryEntry.model_validate_json(row["json"]) if row else None

    def list(self, layer: MemoryLayer | str | None = None) -> list[MemoryEntry]:
        if layer is None:
            rows = self._conn.execute("SELECT json FROM memories").fetchall()
        else:
            layer_val = layer.value if isinstance(layer, MemoryLayer) else str(layer)
            rows = self._conn.execute(
                "SELECT json FROM memories WHERE layer = ?", (layer_val,)
            ).fetchall()
        entries = [MemoryEntry.model_validate_json(r["json"]) for r in rows]
        return sorted(entries, key=lambda e: e.created_at, reverse=True)

    def search(
        self, query: str, *, layers: list[MemoryLayer] | None = None, limit: int = 8,
    ) -> list[MemoryEntry]:
        """确定性关键词检索：按命中词数打分，零分不返回。

        中文 query 经 tokenize 生成整串与 2-gram；条目侧索引 =
        tokenize(title + content + tags)。
        """
        query_tokens = set(tokenize(query))
        if not query_tokens:
            return []
        allowed = {l.value for l in layers} if layers else None
        scored: list[tuple[int, str, MemoryEntry]] = []
        for entry in self.list():
            if allowed is not None and entry.layer.value not in allowed:
                continue
            hay = set(tokenize(
                f"{entry.title} {entry.content} {' '.join(entry.tags)}"
            ))
            score = len(query_tokens & hay)
            if score > 0:
                scored.append((score, entry.memory_id, entry))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [e for _, _, e in scored[:limit]]

    def _write_markdown(self) -> None:
        if self.markdown_path is None:
            return
        lines = ["# Research Memory（跨项目经验库）", ""]
        for layer in MemoryLayer:
            entries = self.list(layer)
            lines.append(f"## {_LAYER_TITLES[layer.value]}（{len(entries)}）")
            if not entries:
                lines.append("（空）")
            for e in entries:
                tags = ",".join(e.tags)
                lines.append(
                    f"- **{e.title}** — {e.content}\n"
                    f"  （来源 {e.source_project}；标签 {tags or '无'}；"
                    f"refs {json.dumps(e.refs, ensure_ascii=False) if e.refs else '无'}）"
                )
            lines.append("")
        self.markdown_path.parent.mkdir(parents=True, exist_ok=True)
        self.markdown_path.write_text("\n".join(lines), encoding="utf-8")

    def close(self) -> None:
        self._conn.close()
