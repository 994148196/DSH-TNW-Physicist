"""SQLite 持久化（计划 v2 §5）。

每类对象一张表：id / project_id / JSON payload。无数据库外键——引用完整性
（假设与决策的证据引用、实验的工具引用）在包层面由 check_references 校验。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import (
    Decision,
    Evidence,
    Experiment,
    Goal,
    Hypothesis,
    Project,
    ProjectBundle,
    ResearchPlan,
    ToolRecord,
    VerificationReport,
)

# (类型, 表名, 主键字段)
_SPECS: tuple[tuple[type, str, str], ...] = (
    (Project, "projects", "project_id"),
    (Goal, "goals", "goal_id"),
    (Hypothesis, "hypotheses", "hypothesis_id"),
    (ResearchPlan, "plans", "plan_id"),
    (Experiment, "experiments", "experiment_id"),
    (Evidence, "evidence", "evidence_id"),
    (Decision, "decisions", "decision_id"),
    (ToolRecord, "tools", "tool_id"),
    (VerificationReport, "verification_reports", "report_id"),
)

# ProjectBundle 中各类型的字段名
_BUNDLE_FIELDS: dict[type, str] = {
    Goal: "goals",
    Hypothesis: "hypotheses",
    ResearchPlan: "plans",
    Experiment: "experiments",
    Evidence: "evidence",
    Decision: "decisions",
    ToolRecord: "tools",
    VerificationReport: "verification_reports",
}


def _spec_for(cls: type) -> tuple[type, str, str]:
    for c, table, id_field in _SPECS:
        if cls is c:
            return c, table, id_field
    raise TypeError(f"未注册的存储类型: {cls.__name__}")


class Storage:
    """一个 Storage 实例对应一个 SQLite 文件；重开实例即模拟进程重启。"""

    def __init__(self, db_path: str | Path):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    @property
    def path(self) -> Path:
        """SQLite 文件路径（供报告/产物定位同目录）。"""
        return self._path

    def _init_schema(self) -> None:
        for _cls, table, _id_field in _SPECS:
            self._conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} ("
                "id TEXT PRIMARY KEY, project_id TEXT NOT NULL, json TEXT NOT NULL)"
            )
            self._conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_project ON {table} (project_id)"
            )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @property
    def path(self) -> Path:
        return self._path

    def save(self, obj) -> None:
        cls, table, id_field = _spec_for(type(obj))
        self._conn.execute(
            f"INSERT INTO {table} (id, project_id, json) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET json=excluded.json, project_id=excluded.project_id",
            (getattr(obj, id_field), obj.project_id, obj.model_dump_json()),
        )
        self._conn.commit()

    def get(self, cls, object_id: str):
        cls, table, _id_field = _spec_for(cls)
        row = self._conn.execute(
            f"SELECT json FROM {table} WHERE id = ?", (object_id,)
        ).fetchone()
        return cls.model_validate_json(row["json"]) if row else None

    def list(self, cls, project_id: str | None = None) -> list:
        cls, table, _id_field = _spec_for(cls)
        if project_id is None:
            rows = self._conn.execute(f"SELECT json FROM {table}").fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT json FROM {table} WHERE project_id = ?", (project_id,)
            ).fetchall()
        return [cls.model_validate_json(r["json"]) for r in rows]

    # -- 聚合（ProjectBundle）----------------------------------------------
    def save_bundle(self, bundle: ProjectBundle) -> None:
        self.save(bundle.project)
        for cls, field_name in _BUNDLE_FIELDS.items():
            for obj in getattr(bundle, field_name):
                self.save(obj)

    def load_bundle(self, project_id: str) -> ProjectBundle:
        project = self.get(Project, project_id)
        if project is None:
            raise KeyError(f"项目不存在: {project_id}")
        data: dict = {"project": project}
        for cls, field_name in _BUNDLE_FIELDS.items():
            if cls is ToolRecord:
                # 工具注册表跨项目共享：加载本项目专属 + 全局工具
                data[field_name] = [
                    t for t in self.list(ToolRecord)
                    if t.project_id in (project_id, "global")
                ]
            else:
                data[field_name] = self.list(cls, project_id)
        return ProjectBundle.model_validate(data)

    # -- 引用完整性 ---------------------------------------------------------
    def check_references(self, bundle: ProjectBundle) -> list[str]:
        """返回问题列表；空列表表示引用完整。"""
        problems: list[str] = []
        evidence_ids = {e.evidence_id for e in bundle.evidence}
        tool_ids = {t.tool_id for t in bundle.tools}

        for h in bundle.hypotheses:
            for ref in h.supporting_evidence + h.contradicting_evidence:
                if ref not in evidence_ids:
                    problems.append(f"{h.hypothesis_id}: 证据 {ref} 不存在")
        for d in bundle.decisions:
            for item in d.checklist:
                if item.status == "passed" and item.evidence and item.evidence not in evidence_ids:
                    problems.append(f"{d.decision_id}: checklist 引用证据 {item.evidence} 不存在")
        for e in bundle.experiments:
            if e.tool_id not in tool_ids:
                problems.append(f"{e.experiment_id}: 工具 {e.tool_id} 未注册")
        return problems

    def require_valid_references(self, bundle: ProjectBundle) -> None:
        problems = self.check_references(bundle)
        if problems:
            raise ReferenceError("引用完整性校验失败:\n" + "\n".join(problems))
