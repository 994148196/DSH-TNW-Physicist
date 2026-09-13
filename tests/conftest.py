import pytest
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient


@pytest.fixture()
def db(tmp_path):
    storage = Storage(tmp_path / "state.sqlite")
    yield storage
    storage.close()


@pytest.fixture()
def sample_bundle():
    from qresearch.core.testing import make_demo_bundle

    return make_demo_bundle("proj_sample")


@pytest.fixture()
def make_scripted_client():
    """构造离线脚本化 DSHClient：按站点名出队响应，元素可为 callable(prompt, session_id)。"""
    def _make(responses: dict) -> DSHClient:
        # 值可以是字符串、callable，或响应序列（字符串/callable 混合）
        queues = {
            k: v if isinstance(v, list) else [v] for k, v in responses.items()
        }

        def runner(prompt: str, session_id: str) -> str:
            station = session_id.split(":")[1]
            q = queues[station]
            item = q.pop(0)
            if callable(item):
                q.insert(0, item)  # callable 常驻：每轮都重新根据 prompt 生成
                return item(prompt, session_id)
            return item

        return DSHClient(runner=runner)

    return _make
