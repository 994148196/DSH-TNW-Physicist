"""归一化事件序列比对（计划 v3 / M1 回归门，评审修订 A5）。

用法：
  采基线：  python examples/compare_events.py --save <out.json> <events.jsonl 或目录>
  比  对：  python examples/compare_events.py <baseline.json> <events.jsonl 或目录>

为什么归一化：events.jsonl 里的 id（exp_/plan_/… 前缀 + uuid）与时间戳每次运行必然
不同，project_id 可携带时间戳后缀；字面 diff 不可行。本脚本把每条事件投影为
(actor, action, object_type, detail 归一化)：

- 剥离 timestamp 与易变键（elapsed_s）；
- id 形态（``(proj|goal|hyp|step|plan|exp|evidence|dec|tool|vr)_[0-9a-f]{10}``）→ ``<id>``；
- 10 位 unix 时间戳后缀（``_1757891234``）→ ``_<ts>``（覆盖 demo 的时间戳 project_id）；
- 含路径分隔符的字符串 → ``<path>``。

序列逐条比较；不一致时打印第一条分歧，退出码 1。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ID_RE = re.compile(r"\b(proj|goal|hyp|step|plan|exp|evidence|dec|tool|vr)_[0-9a-f]{10}\b")
_TS_RE = re.compile(r"_1\d{9}\b")
_PATH_RE = re.compile(r"[\\/]")
# DSHClient 会话 id 里的实例级随机段（<8 位 hex>c<调用序号>）——每次运行都不同，
# 与时间戳同类，归一化掉（会话 id 含该段防 DSH 会话重放，2026-09 起）。
_SESSION_TAG_RE = re.compile(r":[0-9a-f]{8}c\d+:a")
_VOLATILE_KEYS = {"elapsed_s"}


def _norm_value(v):
    if isinstance(v, str):
        v = _ID_RE.sub("<id>", v)
        v = _TS_RE.sub("_<ts>", v)
        v = _SESSION_TAG_RE.sub(":<tag>:a", v)
        if _PATH_RE.search(v):
            return "<path>"
        return v
    if isinstance(v, list):
        return [_norm_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm_value(x) for k, x in v.items() if k not in _VOLATILE_KEYS}
    return v


def normalize_event(e: dict) -> str:
    detail = e.get("detail")
    if isinstance(detail, dict):
        detail = {k: _norm_value(v) for k, v in detail.items() if k not in _VOLATILE_KEYS}
    else:
        detail = _norm_value(detail)
    payload = {
        "actor": e.get("actor"),
        "action": e.get("action"),
        "object_type": e.get("object_type"),
        "object_id": _norm_value(e.get("object_id")),
        "project_id": _norm_value(e.get("project_id")),
        "detail": detail,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def load_events(path: Path) -> list[str]:
    if path.is_dir():
        path = path / "events.jsonl"
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(normalize_event(json.loads(line)))
    return out


def load_many(paths: list[str]) -> list[str]:
    """多输入按给定顺序拼接（多项目 demo 的各 events.jsonl）。"""
    events: list[str] = []
    for p in paths:
        events.extend(load_events(Path(p)))
    return events


def main() -> int:
    args = [a for a in sys.argv[1:]]
    save = None
    if "--save" in args:
        i = args.index("--save")
        save = Path(args[i + 1])
        del args[i:i + 2]
    if len(args) < 1:
        print(__doc__)
        return 2
    if save is not None:
        events = load_many(args)
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_text(json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"BASELINE SAVED: {len(events)} 条归一化事件 → {save}")
        return 0
    base_path, cmp_paths = Path(args[0]), args[1:]
    base = json.loads(base_path.read_text(encoding="utf-8")) if base_path.suffix == ".json" \
        else load_many([str(base_path)])
    cmp_events = load_many(cmp_paths)
    for i, (b, c) in enumerate(zip(base, cmp_events)):
        if b != c:
            print(f"MISMATCH at #{i}（共 基线{len(base)} / 实测{len(cmp_events)} 条）")
            print(f"  基线: {b}")
            print(f"  实测: {c}")
            return 1
    if len(base) != len(cmp_events):
        print(f"LENGTH MISMATCH: 基线 {len(base)} 条 vs 实测 {len(cmp_events)} 条")
        tail = cmp_events[len(base):] or base[len(cmp_events):]
        print(f"  多出的一侧示例: {tail[0]}")
        return 1
    print(f"EVENT SEQUENCE MATCH: {len(base)} 条归一化事件完全一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
