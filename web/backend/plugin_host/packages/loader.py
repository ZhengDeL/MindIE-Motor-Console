"""Import an explicitly trusted source bundle inside its private namespace."""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import inspect
from types import ModuleType
import sys
import threading
from uuid import uuid4

from .errors import PluginPackageError

_IMPORT_LOCK = threading.RLock()


class BundleLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """No extraction, global sys.path edits, dependency installation or network."""
    def __init__(self, identifier: str, digest: str, files: dict[str, bytes]):
        self.namespace = "_motor_console_plugin_" + identifier.replace("-", "_") + "_" + digest[:16] + "_" + uuid4().hex[:12]
        self.files = files
        self.closed = False

    def _location(self, fullname: str):
        if not fullname.startswith(self.namespace + "."):
            return None
        relative = fullname[len(self.namespace) + 1:].replace(".", "/")
        if relative + "/__init__.py" in self.files:
            return relative + "/__init__.py", True
        if relative + ".py" in self.files:
            return relative + ".py", False
        if any(path.startswith(relative + "/") for path in self.files):
            return None, True
        return None

    def find_spec(self, fullname, path=None, target=None):
        if self.closed:
            return None
        location = self._location(fullname)
        if location is None:
            return None
        filename, package = location
        spec = importlib.util.spec_from_loader(fullname, self, is_package=package)
        spec.origin = "qhplugin://" + self.namespace + "/" + (filename or "")
        return spec

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        location = self._location(module.__name__)
        if location is None:
            raise PluginPackageError("Package module is outside the trusted namespace")
        filename, package = location
        if package:
            module.__path__ = []
        if filename is None:
            return
        module.__file__ = "qhplugin://" + self.namespace + "/" + filename
        source = self.files[filename].decode("utf-8-sig")
        code = compile(source, module.__file__, "exec", dont_inherit=True)
        exec(code, module.__dict__)

    def resolve(self, entrypoint: str):
        with _IMPORT_LOCK:
            if self.closed:
                raise PluginPackageError("Plugin module scope is already closed")
            root = ModuleType(self.namespace)
            root.__package__ = self.namespace
            root.__path__ = []
            root.__spec__ = importlib.util.spec_from_loader(self.namespace, loader=None, is_package=True)
            sys.modules[self.namespace] = root
            sys.meta_path.insert(0, self)
            try:
                module_name, attribute = entrypoint.split(":")
                module = importlib.import_module(self.namespace + "." + module_name)
                factory = getattr(module, attribute, None)
                if (not callable(factory) or inspect.iscoroutinefunction(factory)
                        or inspect.iscoroutinefunction(getattr(factory, "__call__", None))):
                    raise PluginPackageError("Package entrypoint must provide a synchronous callable")
                return factory
            except BaseException:
                self.close()
                raise

    def close(self):
        with _IMPORT_LOCK:
            if self.closed:
                return
            self.closed = True
            sys.meta_path[:] = [finder for finder in sys.meta_path if finder is not self]
            for name in tuple(sys.modules):
                if name == self.namespace or name.startswith(self.namespace + "."):
                    sys.modules.pop(name, None)
            self.files = {}


def trusted_factory(profile):
    """Return a factory without running code. Only registry.mount invokes it."""
    def invoke(context):
        # Re-read and re-hash the immutable archive immediately before import.
        record, files = profile._verified_archive()
        loader = BundleLoader(record["manifest"]["id"], record["digest"], files)
        registered = False
        try:
            context.on_shutdown(loader.close)
            registered = True
            selected = loader.resolve(record["manifest"]["entrypoint"])
            return selected(context)
        except BaseException:
            # A failing setup may already have registered worker cleanup that
            # imports its own helper modules. Preserve the namespace until
            # ExitStack has run those later callbacks in reverse order.
            if not registered:
                loader.close()
            raise
    return invoke
