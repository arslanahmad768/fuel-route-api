import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def clear_cache():
    """Isolate tests from each other's cached geocoding results."""
    cache.clear()
    yield
    cache.clear()
