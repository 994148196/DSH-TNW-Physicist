"""工具注册表：PLAN 步骤可引用的工具在此登记（计划 v2 §8.3）。

注册即带：版本、适用范围、基准清单、已知限制——这些字段进 ToolRecord 入库，
golden 基准清单则放在 benchmarks/golden/<tool>.yaml（出题人≠答题人）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel


@dataclass
class ToolSpec:
    """一个可执行数值工具的完整描述。`run` 接收已校验的输入 dict，返回结果 dict。"""

    name: str
    version: str
    type: str
    description: str
    input_model: type[BaseModel]
    run: Callable[[dict[str, Any]], dict[str, Any]]
    applicable_domains: list[str] = field(default_factory=list)
    benchmark_refs: list[str] = field(default_factory=list)
    known_limitations: list[str] = field(default_factory=list)

    def to_tool_record_kwargs(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "type": self.type,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
            "applicable_domains": self.applicable_domains,
            "benchmark_refs": self.benchmark_refs,
            "known_limitations": self.known_limitations,
        }


_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec, *, replace: bool = False) -> ToolSpec:
    """登记工具；默认重复注册视为错误（工具集是受控词汇，不许静默覆盖）。

    replace=True 仅限两处：Tool Builder 覆写同名候选实现、正式注册前清理候选名。
    """
    if spec.name in _REGISTRY and not replace:
        raise ValueError(f"工具重复注册: {spec.name}")
    _REGISTRY[spec.name] = spec
    return spec


def get_tool(name: str) -> ToolSpec:
    if name not in _REGISTRY:
        raise KeyError(
            f"未注册的工具: {name}（可用: {', '.join(sorted(_REGISTRY)) or '无'}）"
        )
    return _REGISTRY[name]


def unregister(name: str) -> None:
    """移除注册（Tool Builder 注册正式名后清理候选名用；其余场景禁用）。"""
    _REGISTRY.pop(name, None)


def tool_names() -> list[str]:
    return sorted(_REGISTRY)


def load_seed_tools() -> None:
    """导入种子工具模块，触发注册（幂等：import 只执行一次）。"""
    from . import simple_ed  # noqa: F401  注册 simple_ed
    from . import dmrg_adapter  # noqa: F401  注册 dmrg_adapter
    from . import tfim_ed  # noqa: F401  注册 tfim_ed（Phase 6 构建晋升）
