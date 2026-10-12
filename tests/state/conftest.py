import pytest

from studio.state import sources
from studio.state.api import local_services
from tests.state.helpers import Clock, two_page_pdf


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def services(tmp_path, clock):
    return local_services(tmp_path / "state", clock)


@pytest.fixture
def source(services) -> dict:
    return sources.seal(services.store, two_page_pdf())
