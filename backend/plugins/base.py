import abc
from pydantic import BaseModel, Field
from typing import Dict, Any, List

class PluginManifest(BaseModel):
    """Pydantic model describing a plugin's metadata."""
    name: str = Field(..., description="Unique plugin identifier")
    description: str = Field(..., description="Human‑readable description of what the plugin does")
    version: str = Field(..., description="Semantic version string, e.g. '1.0.0'")
    capabilities: List[str] = Field(default_factory=list, description="List of capabilities or commands the plugin provides")
    required_keys: List[str] = Field(default_factory=list, description="List of input keys required by the plugin")

class BasePlugin(abc.ABC):
    """Abstract base class all runtime plugins must inherit from.

    Each concrete plugin receives a validated ``PluginManifest`` instance on
    construction and must implement the asynchronous ``execute`` method.
    """
    def __init__(self, manifest: PluginManifest, config: Dict[str, Any] = None):
        self.manifest = manifest
        self.config = config or {}

    @abc.abstractmethod
    async def execute(self, query: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Run the plugin logic.

        Args:
            query: The input string or command for the plugin.
            context: A dictionary with runtime context (e.g. user, session info).

        Returns:
            A list of dictionaries representing structured results.
        """
        raise NotImplementedError

async def safe_execute(plugin: BasePlugin, query: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Execute a plugin in an isolation wrapper.

    Any exception raised by the plugin is caught and re‑raised as a
    ``RuntimeError`` so that the core engine loop can continue operating.
    """
    try:
        return await plugin.execute(query, context)
    except Exception as exc:
        # Log the error or re‑raise a generic error to keep the engine alive.
        raise RuntimeError(f"Plugin '{plugin.manifest.name}' execution failed: {exc}") from exc