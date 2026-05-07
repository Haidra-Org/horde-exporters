"""Common upstream-client errors."""

from __future__ import annotations


class UpstreamUnavailable(Exception):
    """Raised when an upstream signal source is unreachable or returns a non-2xx response."""

    def __init__(self, upstream: str, message: str, *, status_code: int | None = None) -> None:
        """Create an :class:`UpstreamUnavailable` exception.

        Args:
            upstream: Logical upstream name (e.g. ``alertmanager``).
            message: Human-readable detail.
            status_code: Optional HTTP status code from the failing call.
        """
        self.upstream = upstream
        self.status_code = status_code
        super().__init__(f"{upstream}: {message}")
