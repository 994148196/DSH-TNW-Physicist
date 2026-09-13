"""Research State 的 YAML/JSON 导入导出（计划 v2 §9 Phase 1）。"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from .models import ProjectBundle
from .storage import Storage


def export_bundle(storage: Storage, project_id: str, out_path: str | Path) -> Path:
    """把项目完整状态导出为 .json / .yaml / .yml 文件。"""
    bundle = storage.load_bundle(project_id)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bundle.model_dump(mode="json")
    fmt = path.suffix.lstrip(".").lower()
    if fmt == "json":
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    elif fmt in {"yaml", "yml"}:
        path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    else:
        raise ValueError(f"不支持的导出格式: {fmt}（用 .json / .yaml / .yml）")
    return path


def import_bundle(storage: Storage, in_path: str | Path) -> ProjectBundle:
    """从 .json / .yaml 文件恢复项目状态（对象按 id upsert）。"""
    path = Path(in_path)
    text = path.read_text(encoding="utf-8")
    fmt = path.suffix.lstrip(".").lower()
    if fmt == "json":
        data = json.loads(text)
    elif fmt in {"yaml", "yml"}:
        data = yaml.safe_load(text)
    else:
        raise ValueError(f"不支持的导入格式: {fmt}")
    bundle = ProjectBundle.model_validate(data)
    storage.save_bundle(bundle)
    return bundle
