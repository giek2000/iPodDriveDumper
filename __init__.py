"""Read-only IpodRawDriveDumper."""

from .version import APP_NAME, VERSION
from .models import DumpStatus

__version__ = VERSION

__all__ = ["DumpStatus", "__version__"]
