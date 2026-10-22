"""Shared, versioned data contracts for Parallel.

Every structure that crosses a process boundary (API <-> web, API <-> workers,
workers <-> engine runtimes, stored revisions, result envelopes) is defined here
so that all components validate against the same schema version.
"""

SCHEMA_VERSION = "1.0"

__all__ = ["SCHEMA_VERSION"]
