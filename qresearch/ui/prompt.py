"""输入框视觉包装（2026-09-15 实测反馈：一屏滚动的输出里找不到"该我在哪打字"）。

每个交互点的提示行用两条横线夹住——提示正文在框内，`>> ` 是输入光标位。
纯制表符 + GBK 兼容字符（※），rich 缺失、管道重定向都不炸；宽度取终端实宽。
"""
from __future__ import annotations

from shutil import get_terminal_size


def _rule() -> str:
    return "─" * max(40, min(get_terminal_size().columns, 96))


def framed_block(text: str) -> str:
    """提示框（上线 + 正文 + 下线），用于多行输入等不带单行光标位的场景。"""
    return f"{_rule()}\n※ {text.rstrip('：:')}：\n{_rule()}"


def framed_prompt(prompt: str) -> str:
    """把提示文本包装成可直接交给 input() 的多行提示串。

    渲染效果（提示框 + 输入行）：
        ──────────────────────────────
        ※ 审批：[y]批准 / [c]提修改意见 …
        ──────────────────────────────
        >>
    """
    return f"{framed_block(prompt)}\n>> "
