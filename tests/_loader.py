"""Load the integration's pure-logic modules without Home Assistant.

The package ``__init__`` imports Home Assistant, so the package is registered
as an empty shell and only the modules under test are imported from it.
"""
import os
import sys
import types

PACKAGE = "morning_routine"
PACKAGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "custom_components",
    PACKAGE,
)


def load_package() -> types.ModuleType:
    """Register the package shell (without running its __init__) and return it."""
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [PACKAGE_DIR]
        sys.modules[PACKAGE] = package
    return sys.modules[PACKAGE]
