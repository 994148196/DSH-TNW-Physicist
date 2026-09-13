"""Tool Spec 加载（计划 v2 Phase 6）：spec 是出题侧工件，先于任何实现定稿。

铁律：出题人≠答题人——fixtures 与容差在编码前锁定；编码站（DSH）只读
spec 的物理约定与契约，拿不到基准数值，也没有 fixtures 的写入路径。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict


class ToolBuildSpec(BaseModel):
    """一次工具构建的完整契约（tool_specs/<tool>.yaml）。"""

    model_config = ConfigDict(extra="ignore")

    tool: str
    version: str
    type: str
    purpose: str
    physics_convention: str
    inputs: dict[str, dict[str, Any]]
    outputs: dict[str, str]
    invariants: list[str] = []
    known_limitations: list[str] = []
    falsification_basis: list[str] = []
    fixtures_ref: str
    fixtures_install_to: str | None = None

    @classmethod
    def load(cls, path: str | Path) -> "ToolBuildSpec":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(raw)

    def digest(self) -> str:
        """供 critic 与报告使用的紧凑摘要。"""
        inputs = "\n".join(
            f"  - {k}: {v.get('desc', '')}（{v.get('type', '?')}"
            f"{'，默认 ' + str(v['default']) if 'default' in v else ''}"
            f"{'，范围 ' + str(v['bounds']) if v.get('bounds') else ''}）"
            for k, v in self.inputs.items()
        )
        outputs = "\n".join(f"  - {k}: {v}" for k, v in self.outputs.items())
        invariants = "\n".join(f"  - {s}" for s in self.invariants)
        return (
            f"{self.tool} v{self.version}（{self.type}）：{self.purpose}\n"
            f"约定：{self.physics_convention}\n"
            f"输入：\n{inputs}\n输出：\n{outputs}\n不变量：\n{invariants}"
        )
