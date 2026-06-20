"""Probe implementations.

Each probe is a small async callable that returns a :class:`ProbeResult`
(see :mod:`horde_status_prober.probes.base`). Probes are stateless and
own no DB access — the prober pushes their results to the alerts service.
"""

from horde_status_prober.probes.base import Probe, ProbeOutcome, ProbeResult

__all__ = ["Probe", "ProbeOutcome", "ProbeResult"]
