"""Minimal Python bootstrap for explicit, versioned provider composition.

Selections are read before persistence and stay fixed until restart. Business
implementations use public providers; root ASGI assembly remains host-owned.
"""
from __future__ import annotations
from contextlib import ExitStack
import os
from pathlib import Path
from .platform_configuration import PlatformConfiguration
from .provider_host import ProviderHost


def create_app(*, mode: str | None = None, data_dir: str | Path | None = None,
               upstream_dir: str | Path | None = None, console_token: str | None = None):
    from .settings import load_secret_files
    load_secret_files()
    selected_mode = mode or os.getenv("MOTOR_MODE", "demo")
    if selected_mode not in {"demo", "control", "agent"}:
        raise ValueError("MOTOR_MODE must be demo, control or agent")
    directory = Path(data_dir or os.getenv("MOTOR_DATA_DIR", Path(__file__).resolve().parents[1] / ".data")).resolve()
    upstream = Path(upstream_dir or os.getenv("MOTOR_UPSTREAM_DIR", Path(__file__).resolve().parents[2] / "mindie-motor/upstream")).resolve()
    token = console_token if console_token is not None else os.getenv("MOTOR_CONSOLE_TOKEN", "")
    with ExitStack() as startup:
        providers = ProviderHost(settings={"data_dir": directory, "upstream_dir": upstream,
            "mode": selected_mode, "console_token": token,
            "configuration": PlatformConfiguration.from_env(directory), "startup": startup}, cleanup=startup)
        from .provider_host.bootstrap import legacy_artifacts
        profiles, signature = legacy_artifacts(directory, selected_mode) if providers.is_reference("plugins.packages") else ((), [])
        providers.set_bootstrap_artifacts("legacy-additive-packages", signature)
        from .provider_host.generation import guard
        guard(directory, providers.describe())
        result = providers.resolve("console.application", legacy_profiles=profiles)
        providers.seal()
        result.state.providers = {**providers.describe(), "generation": providers.generation}
        result.state.resource_lifetime = startup.pop_all()
        return result


def __getattr__(name):
    from . import default_console
    if name.startswith("_") or not hasattr(default_console, name):
        raise AttributeError(name)
    return getattr(default_console, name)


app = create_app()
