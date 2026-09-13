"""存储层测试：各对象类型的 roundtrip、按项目过滤、upsert、引用完整性。"""
import pytest

from qresearch.core.models import Hypothesis, Project
from qresearch.core.status import HypothesisStatus
from qresearch.core.storage import Storage
from qresearch.core.testing import make_demo_bundle


def test_roundtrip_all_objects(db, sample_bundle):
    db.save_bundle(sample_bundle)
    restored = db.load_bundle(sample_bundle.project.project_id)
    assert restored.model_dump() == sample_bundle.model_dump()


def test_get_single_object(db, sample_bundle):
    db.save_bundle(sample_bundle)
    got = db.get(Hypothesis, "hyp_001")
    assert got is not None and got.statement == sample_bundle.hypotheses[0].statement
    assert db.get(Hypothesis, "hyp_nope") is None


def test_list_filters_by_project(db):
    b1 = make_demo_bundle("proj_a")
    b2 = make_demo_bundle("proj_b", id_suffix="_b")
    db.save_bundle(b1)
    db.save_bundle(b2)
    assert len(db.list(Project, "proj_a")) == 1
    assert len(db.list(Hypothesis, "proj_b")) == 1
    assert len(db.list(Hypothesis)) == 2


def test_upsert_overwrites(db, sample_bundle):
    db.save_bundle(sample_bundle)
    hyp = db.get(Hypothesis, "hyp_001")
    hyp.status = HypothesisStatus.SUPPORTED
    db.save(hyp)
    assert len(db.list(Hypothesis, sample_bundle.project.project_id)) == 1
    assert db.get(Hypothesis, "hyp_001").status == HypothesisStatus.SUPPORTED


def test_load_missing_project_raises(db):
    with pytest.raises(KeyError):
        db.load_bundle("proj_ghost")


def test_reference_check(db, sample_bundle):
    # 原始包引用自洽
    assert db.check_references(sample_bundle) == []
    # 抽走证据后：假设引用与 decision checklist 引用都应报问题
    broken = sample_bundle.model_copy(deep=True)
    broken.evidence = []
    problems = db.check_references(broken)
    assert any("hyp_001" in p for p in problems)
    assert any("dec_001" in p for p in problems)
    db.require_valid_references(sample_bundle)  # 不抛
    with pytest.raises(ReferenceError):
        db.require_valid_references(broken)


def test_unregistered_tool_flagged(db, sample_bundle):
    bundle = sample_bundle.model_copy(deep=True)
    bundle.tools = []
    problems = db.check_references(bundle)
    assert any("tool_simple_ed" in p for p in problems)
