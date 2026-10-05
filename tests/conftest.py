"""Wstrzykuje pakiet `custom_components.alerty` bez wykonywania jego __init__.py.

Moduły logic/journal/options/const są czystym Pythonem; __init__.py integracji
importuje Home Assistant, którego lokalnie nie ma. Konwencja jak w repo
ecovacs-goat-o1200 (tests/ecovacs_goat/*).
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PATH = ROOT / "custom_components" / "alerty"

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(PACKAGE_PATH.parent)]
sys.modules.setdefault("custom_components", custom_components)

alerty = types.ModuleType("custom_components.alerty")
alerty.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault("custom_components.alerty", alerty)


@pytest.fixture
def now() -> datetime:
    """Stały „teraz” (UTC) dla testów."""
    return datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
