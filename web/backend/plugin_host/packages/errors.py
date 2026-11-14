"""Stable errors shared by offline tooling and the authenticated host API."""
class PluginPackageError(ValueError):
    code = "invalid_plugin_package"
    status_code = 422


class PackageConflict(PluginPackageError):
    code = "plugin_revision_conflict"
    status_code = 409


class PackageNotFound(PluginPackageError):
    code = "plugin_package_not_found"
    status_code = 404


class PackageStoreUnavailable(PluginPackageError):
    code = "plugin_store_unavailable"
    status_code = 503
