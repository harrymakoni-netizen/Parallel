"""Deterministic materialisation of stochastic inputs from a seed.

Samples are drawn in the adapter (not inside the engine) and stored with the
run's inputs, so identical seeds give identical, hashable populations and
service times across scenarios (common random numbers).
"""

from __future__ import annotations

import numpy as np

from parallel_contracts.domain.common import Distribution
from parallel_contracts.units import convert


def rng_for(seed: int | None, *stream: str) -> np.random.Generator:
    """Independent named streams derived from one run seed."""
    base = 0 if seed is None else int(seed)
    key = [base] + [int.from_bytes(s.encode()[:8].ljust(8, b"\0"), "little") for s in stream]
    return np.random.default_rng(np.random.SeedSequence(key))


def sample(dist: Distribution, n: int, rng: np.random.Generator, unit: str | None = None) -> np.ndarray:
    k = dist.kind
    if k == "constant":
        out = np.full(n, float(dist.value))  # type: ignore[arg-type]
    elif k == "normal":
        out = rng.normal(dist.mean, dist.sd, n)
    elif k == "uniform":
        out = rng.uniform(dist.low, dist.high, n)
    elif k == "triangular":
        out = rng.triangular(dist.low, dist.mode, dist.high, n)
    elif k == "lognormal":
        # mean/sd are of the variable itself; convert to underlying normal parameters
        m, s = float(dist.mean), float(dist.sd)  # type: ignore[arg-type]
        sigma2 = np.log(1 + (s * s) / (m * m))
        out = rng.lognormal(np.log(m) - sigma2 / 2, np.sqrt(sigma2), n)
    elif k == "exponential":
        out = rng.exponential(dist.mean, n)
    else:  # pragma: no cover
        raise ValueError(k)
    if dist.clip_low is not None or dist.clip_high is not None:
        out = np.clip(
            out,
            dist.clip_low if dist.clip_low is not None else -np.inf,
            dist.clip_high if dist.clip_high is not None else np.inf,
        )
    if unit and unit != dist.unit:
        out = np.array([convert(v, dist.unit, unit) for v in out])
    return out
