"""
Smoke tests to verify Django loads correctly.
"""

import os
import subprocess
import sys
from pathlib import Path

from django.apps import apps

BASE_DIR = Path(__file__).resolve().parents[2]


def test_django_loads():
    """Test that Django initializes without errors.

    Checks that all configured apps are ready without requiring
    SECRET_KEY (avoids cookie-signing middleware paths).
    """
    # Verify all installed apps are loaded
    assert apps.ready, "Django apps should be ready"
    # Verify our app is in the registry
    assert apps.is_installed('fuelroute'), "fuelroute app should be installed"


def _import_real_settings(extra_env):
    """Import `config.settings` in a fresh interpreter with a controlled env."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("DJANGO_SECRET_KEY", "ORS_API_KEY", "DJANGO_SETTINGS_MODULE")
    }
    env.update(extra_env)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import config.settings as s; print(s.SECRET_KEY)",
        ],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


def test_real_settings_refuse_to_load_without_secret_key():
    # An explicitly empty variable is "already set" as far as python-dotenv
    # is concerned (override=False), so this is deterministic even if a
    # developer's local .env defines a real key.
    result = _import_real_settings({"DJANGO_SECRET_KEY": ""})
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DJANGO_SECRET_KEY is not set" in result.stderr


def test_real_settings_load_with_secret_key():
    result = _import_real_settings({"DJANGO_SECRET_KEY": "from-environment"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "from-environment"


def test_constants_imported():
    """Test that fuelroute constants can be imported."""
    from fuelroute.constants import (
        VEHICLE_RANGE_MILES,
        VEHICLE_MPG,
        EARTH_RADIUS_MILES,
        ROUTE_CORRIDOR_BUFFER_MILES,
    )

    assert VEHICLE_RANGE_MILES == 500
    assert VEHICLE_MPG == 10
    assert EARTH_RADIUS_MILES == 3958.8
    assert ROUTE_CORRIDOR_BUFFER_MILES == 2.0
