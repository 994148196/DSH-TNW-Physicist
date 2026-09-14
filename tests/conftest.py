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


class Persistent:
    """常驻应答包装：不被消费，每次调用按 prompt 现生成。

    与之相对，裸 callable 只消费一次（出队即用）——按序编排的
    decide 队列依赖这一点；此前 callable 一律常驻，导致按序队列
    永远停在第一个应答上（test_pause_and_resume 的教训）。
    """

    def __init__(self, fn):
        self.fn = fn

    def __call__(self, prompt: str, session_id: str) -> str:
        return self.fn(prompt, session_id)


@pytest.fixture()
def make_scripted_client():
    """构造离线脚本化 DSHClient：按站点名出队响应。

    队列元素三态：str（一次性）/ callable（一次性，出队即用）/
    Persistent(callable)（常驻）。站点队列耗尽即报错——编排写少
    了一轮应答要立刻显式失败，不许静默串位。
    """
    def _make(responses: dict) -> DSHClient:
        queues = {
            k: v if isinstance(v, list) else [v] for k, v in responses.items()
        }

        def runner(prompt: str, session_id: str) -> str:
            station = session_id.split(":")[1]
            q = queues[station]
            if not q:
                raise AssertionError(f"站点 {station} 的应答队列已耗尽（编排少写了一轮）")
            item = q.pop(0)
            if isinstance(item, Persistent):
                q.insert(0, item)
            if callable(item):
                return item(prompt, session_id)
            return item

        return DSHClient(runner=runner)

    return _make
