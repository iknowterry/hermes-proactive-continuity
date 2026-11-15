"""Load the plugin directory as the package ``proactive_continuity`` for tests.

Hermes imports a directory plugin as a package, so ``__init__.py`` uses relative
imports. Tests mirror that under a fixed name, and tell pytest to treat the
plugin root as a plain folder instead of importing its ``__init__.py`` directly.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NAME = "proactive_continuity"

if NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = module
    spec.loader.exec_module(module)


class _RootIsAFolder:
    @pytest.hookimpl(tryfirst=True)
    def pytest_collect_directory(self, path, parent):
        if Path(path).resolve() == ROOT:
            return pytest.Dir.from_parent(parent, path=path)
        return None


def pytest_configure(config):
    if not config.pluginmanager.has_plugin("proactive-continuity-root"):
        config.pluginmanager.register(_RootIsAFolder(), "proactive-continuity-root")
