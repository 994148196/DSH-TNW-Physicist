"""研究预算（Phase 8）：轮数 + 实验数 + 墙钟上限。

代码层闸（铁律"LLM 只提议，代码记账"的延续）：预算在轮边界由代码检查并强制停，
不依赖模型自律；耗尽即 budget_exhausted，事件留痕。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Budget:
    """预算上限。max_rounds=None 表示由调用方的 rounds 参数决定。"""

    max_rounds: int | None = None
    max_experiments: int | None = None
    wallclock_min: float | None = None

    def exhausted(
        self, *, rounds_used: int, experiments_used: int, elapsed_s: float,
    ) -> str | None:
        """返回耗尽原因；None 表示预算尚可用。在轮边界调用。"""
        if self.max_rounds is not None and rounds_used >= self.max_rounds:
            return f"轮数预算耗尽（{rounds_used}/{self.max_rounds}）"
        if self.max_experiments is not None and experiments_used >= self.max_experiments:
            return f"实验数预算耗尽（{experiments_used}/{self.max_experiments}）"
        if self.wallclock_min is not None and elapsed_s >= self.wallclock_min * 60:
            return f"墙钟预算耗尽（{elapsed_s / 60:.1f}/{self.wallclock_min} 分钟）"
        return None
