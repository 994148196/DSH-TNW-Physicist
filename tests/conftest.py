import pytest
from qresearch.core.storage import Storage


@pytest.fixture()
def db(tmp_path):
    storage = Storage(tmp_path / "state.sqlite")
    yield storage
    storage.close()


@pytest.fixture()
def sample_bundle():
    from qresearch.core.testing import make_demo_bundle

    return make_demo_bundle("proj_sample")
