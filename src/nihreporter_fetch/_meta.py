from importlib.metadata import PackageNotFoundError, version

TOOL_NAME = "nihreporter-fetch"
# Bump when the structure of output records changes in a way consumers must handle.
SCHEMA_VERSION = 1

try:
    __version__ = version(TOOL_NAME)
except PackageNotFoundError:  # pragma: no cover - running from a source tree without installing
    __version__ = "0.0.0"


class ReporterError(Exception):
    """Raised when NIH RePORTER cannot be queried or returns an error."""


def utc_now() -> str:
    """Current time as an ISO-8601 UTC timestamp, e.g. 2024-01-31T12:00:00Z."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
