import importlib
import importlib.util
import json
import pathlib
from pydantic import ValidationError
from typing import Dict, Any

from .base import PluginManifest, BasePlugin, safe_execute

def load_plugins(directory: str) -> Dict[str, Any]:
    """Discover plugins under ``directory``.

    The function walks the directory tree looking for ``manifest.json`` files.
    For each manifest it validates the data with ``PluginManifest`` and attempts
    to import a Python module named ``plugin`` located in the same folder. If the
    module defines a class that subclasses ``BasePlugin`` we instantiate it and
    store it in the result map keyed by the plugin name.

    Returns:
        A dictionary mapping plugin name to a dict with keys ``manifest`` and
        ``instance`` (if import succeeded) or ``error`` if validation/import failed.
    """
    plugins: Dict[str, Any] = {}
    base_path = pathlib.Path(directory)
    for manifest_path in base_path.glob('**/manifest.json'):
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest_data = json.load(f)
            manifest = PluginManifest(**manifest_data)
        except (json.JSONDecodeError, ValidationError) as e:
            plugins[manifest_path.parent.name] = {"error": f"Manifest validation failed: {e}"}
            continue

        # Attempt to import a module named "plugin" in the same directory.
        module_path = manifest_path.parent / "plugin.py"
        if not module_path.exists():
            plugins[manifest.name] = {"manifest": manifest, "error": "Missing plugin.py file"}
            continue
        try:
            # Import the module using importlib; make the directory a package on the fly.
            spec = importlib.util.spec_from_file_location(f"plugins.{manifest.name}.plugin", module_path)
            module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            assert spec and spec.loader
            spec.loader.exec_module(module)  # type: ignore[assignment]
            # Find a subclass of BasePlugin defined in the module.
            plugin_cls = None
            for attr in dir(module):
                obj = getattr(module, attr)
                if isinstance(obj, type) and issubclass(obj, BasePlugin) and obj is not BasePlugin:
                    plugin_cls = obj
                    break
            if plugin_cls is None:
                plugins[manifest.name] = {"manifest": manifest, "error": "No BasePlugin subclass found"}
                continue
            instance = plugin_cls(manifest)
            plugins[manifest.name] = {"manifest": manifest, "instance": instance}
        except Exception as exc:
            plugins[manifest.name] = {"manifest": manifest, "error": f"Import failed: {exc}"}
    return plugins