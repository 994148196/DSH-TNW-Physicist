"""Tool Builder（计划 v2 Phase 6）：真实工具缺口的自动补齐。"""
from .spec import ToolBuildSpec
from .builder import BuildResult, build_tool

__all__ = ["ToolBuildSpec", "BuildResult", "build_tool"]
