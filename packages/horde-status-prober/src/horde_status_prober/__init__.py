"""External blackbox prober for the AI Horde status page.

The prober runs a small set of probes (``probes/*.py``), each implementing
:class:`~horde_status_prober.probes.base.Probe`, on independent schedules
and posts each result to ``ai-horde-service-alerts`` via
``POST /api/v1/internal/probe-results`` using a shared-secret header.

Layout
------
- :mod:`horde_status_prober.config`: pydantic-settings env loader.
- :mod:`horde_status_prober.probes`: probe implementations + base class.
- :mod:`horde_status_prober.pusher`: HTTP client that submits results.
- :mod:`horde_status_prober.main`: CLI entrypoint (APScheduler + healthz).
"""

from horde_status_prober.config import ProberSettings

__all__ = ["ProberSettings"]
__version__ = "0.1.0"
