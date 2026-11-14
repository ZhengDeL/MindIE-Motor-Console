"""Read immutable legacy additive artifacts without executing plugin code."""
from pathlib import Path
import os

from ..plugin_host.contracts import PluginConfigurationError


def legacy_artifacts(directory, mode):
    if mode != "control": return (), []
    from ..plugin_host.packages import PackageStore
    configured = os.getenv("MOTOR_PLUGIN_DIR", "")
    root = Path(configured) if configured else Path(directory).resolve() / "plugin-packages"
    if not root.is_absolute():
        raise PluginConfigurationError("MOTOR_PLUGIN_DIR must be absolute")
    # No store/recovery directory is created by this metadata-only bootstrap.
    profiles = PackageStore(root, recover=False).active_profiles()
    signature = [{"id": profile.manifest["id"], "version": profile.manifest["version"],
                  "artifact": profile.digest, "configuration_digest": profile.configuration_digest}
                 for profile in profiles]
    return profiles, signature
