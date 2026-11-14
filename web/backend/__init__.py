"""MindIE-Motor Console control plane and runtime adapters."""

from pathlib import Path
import sys

# A full source checkout uses the same public SDK sources that are packaged for
# external authors. Installed images provide motor_console_sdk next to backend.
# This is one fixed, reviewed SDK location, never a plugin search directory.
_source_sdk = Path(__file__).resolve().parents[2] / "packages" / "python-sdk"
if (_source_sdk / "motor_console_sdk" / "__init__.py").is_file():
    sys.path.insert(0, str(_source_sdk))
