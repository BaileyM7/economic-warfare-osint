"""Swarm data-lake ingest package.

Public surface
--------------
``Source``            — Abstract base class for all ingest adapters.
``ingest_all``        — Async orchestrator; runs a list of sources concurrently.
``IngestionRunResult``— Result dataclass returned by each source run.

Quick usage::

    from ingest import Source, ingest_all, IngestionRunResult
"""

from ingest.base import IngestionRunResult, RawRecord, Source
from ingest.runner import ingest_all

__all__ = [
    "Source",
    "ingest_all",
    "IngestionRunResult",
    "RawRecord",
]
