"""
Django settings module used only for running the test suite (see
`pyproject.toml`'s `DJANGO_SETTINGS_MODULE`).

Guarantees a safe, non-production `DJANGO_SECRET_KEY` so the full test
suite — including tests that exercise Django's full middleware stack, e.g.
DRF's `APIClient` going through `django.contrib.messages`, which needs
`settings.SECRET_KEY` — passes in a genuinely clean shell with no env vars
exported, without ever adding a baked-in default to `config/settings.py`
itself (real deployments must still fail loudly if `DJANGO_SECRET_KEY` is
unset, per Task 1's review).

Why not a `conftest.py` doing `os.environ.setdefault(...)`: `config/settings.py`
reads `DJANGO_SECRET_KEY` at *module import time* (`SECRET_KEY =
os.environ.get(...)`), and that import is forced by pytest-django's own
`pytest_load_initial_conftests` hookimpl (`_initialize_django` ->
`dj_settings.DATABASES`). Pluggy calls hookimpls for the same hook in this
order: `tryfirst` implementations, then normal ones, then `trylast` last.
pytest-django's implementation of `pytest_load_initial_conftests` is a plain
(un-marked) hookimpl; pytest core's own implementation of that same hook --
the one that actually imports `conftest.py` files -- is marked
`@hookimpl(trylast=True)` (see `_pytest/config/__init__.py`,
`PytestPluginManager.pytest_load_initial_conftests`). That means pytest-django
always imports `config.settings` (and caches `SECRET_KEY = None` on it)
*before* any conftest.py in the whole run has even been imported, verified
empirically: a root `conftest.py` that only sets the env var still leaves
every `APIClient`-based test failing with `ImproperlyConfigured`. Since a
module-level assignment can't be "fixed" retroactively once it has already
executed, the env var has to be set *before* `config.settings` is imported --
which means setting it as part of importing whatever module
`DJANGO_SETTINGS_MODULE` points to, hence this wrapper module instead of a
conftest.py.
"""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key-not-for-production")

from config.settings import *  # noqa: E402,F401,F403
