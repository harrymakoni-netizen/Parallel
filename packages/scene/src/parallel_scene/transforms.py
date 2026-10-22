"""Rigid/similarity transforms between capture, canonical, geographic and engine frames.

Conventions (see parallel_contracts.coordinates):
* 4x4 matrices, row-major storage, column vectors: p_to = M @ [x, y, z, 1].
* Quaternions: Hamilton convention, [w, x, y, z].
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence

import numpy as np

from parallel_contracts.coordinates import Transform


def as_matrix(m: Sequence[Sequence[float]] | np.ndarray) -> np.ndarray:
    a = np.asarray(m, dtype=float)
    if a.shape != (4, 4):
        raise ValueError("expected a 4x4 matrix")
    return a


def to_list(m: np.ndarray) -> list[list[float]]:
    return [[float(v) for v in row] for row in np.asarray(m, dtype=float)]


def apply(m: np.ndarray | Sequence[Sequence[float]], points: np.ndarray | Sequence[Sequence[float]]) -> np.ndarray:
    """Apply a 4x4 transform to an (N, 3) or (3,) array of points."""
    mat = as_matrix(m)
    p = np.asarray(points, dtype=float)
    single = p.ndim == 1
    p2 = np.atleast_2d(p)
    if p2.shape[1] == 2:
        p2 = np.hstack([p2, np.zeros((len(p2), 1))])
    h = np.hstack([p2, np.ones((len(p2), 1))])
    out = (mat @ h.T).T[:, :3]
    return out[0] if single else out


def invert(m: np.ndarray | Sequence[Sequence[float]]) -> np.ndarray:
    return np.linalg.inv(as_matrix(m))


def compose(*ms: np.ndarray | Sequence[Sequence[float]]) -> np.ndarray:
    """compose(A, B, C) applies C first, then B, then A (A @ B @ C)."""
    out = np.eye(4)
    for m in ms:
        out = out @ as_matrix(m)
    return out


def quat_to_matrix(q_wxyz: Sequence[float]) -> np.ndarray:
    w, x, y, z = np.asarray(q_wxyz, dtype=float) / np.linalg.norm(q_wxyz)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def matrix_to_quat(r: np.ndarray) -> np.ndarray:
    """Rotation matrix -> unit quaternion [w, x, y, z] with w >= 0."""
    r = np.asarray(r, dtype=float)
    tr = np.trace(r)
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        q = [0.25 * s, (r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = np.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = [(r[2, 1] - r[1, 2]) / s, 0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s]
    elif r[1, 1] > r[2, 2]:
        s = np.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = [(r[0, 2] - r[2, 0]) / s, (r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s]
    else:
        s = np.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = [(r[1, 0] - r[0, 1]) / s, (r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s]
    qa = np.asarray(q)
    qa /= np.linalg.norm(qa)
    return qa if qa[0] >= 0 else -qa


def similarity(scale: float = 1.0, q_wxyz: Sequence[float] = (1, 0, 0, 0), t: Sequence[float] = (0, 0, 0)) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = scale * quat_to_matrix(q_wxyz)
    m[:3, 3] = np.asarray(t, dtype=float)
    return m


def decompose_similarity(m: np.ndarray | Sequence[Sequence[float]]) -> tuple[float, np.ndarray, np.ndarray]:
    """Return (scale, quaternion wxyz, translation) of a similarity transform."""
    mat = as_matrix(m)
    a = mat[:3, :3]
    scale = float(np.cbrt(np.linalg.det(a)))
    if scale <= 0:
        raise ValueError("not a proper similarity transform (reflection or degenerate)")
    r = a / scale
    if not np.allclose(r @ r.T, np.eye(3), atol=1e-6):
        raise ValueError("matrix is not a similarity transform (contains shear or anisotropic scale)")
    return scale, matrix_to_quat(r), mat[:3, 3].copy()


def umeyama(src: np.ndarray, dst: np.ndarray, with_scale: bool = True) -> tuple[np.ndarray, float]:
    """Least-squares similarity transform mapping src -> dst (Umeyama 1991).

    Returns (4x4 matrix, rms residual in dst units). Requires >= 3 non-collinear points.
    """
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    if src.shape != dst.shape or src.shape[1] != 3 or len(src) < 3:
        raise ValueError("need at least 3 corresponding 3D points")
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    cov = xd.T @ xs / len(src)
    u, d, vt = np.linalg.svd(cov)
    if d[1] < 1e-12:
        raise ValueError("points are degenerate (collinear or coincident)")
    s_mat = np.eye(3)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        s_mat[2, 2] = -1
    r = u @ s_mat @ vt
    var_s = (xs**2).sum() / len(src)
    scale = float(np.trace(np.diag(d) @ s_mat) / var_s) if with_scale else 1.0
    t = mu_d - scale * r @ mu_s
    m = np.eye(4)
    m[:3, :3] = scale * r
    m[:3, 3] = t
    resid = apply(m, src) - dst
    rms = float(np.sqrt((resid**2).sum(1).mean()))
    return m, rms


def scale_from_reference(p_a: Sequence[float], p_b: Sequence[float], reference_distance_m: float) -> float:
    """Uniform scale factor making |p_b - p_a| equal to a known reference distance."""
    d = float(np.linalg.norm(np.asarray(p_b, float) - np.asarray(p_a, float)))
    if d <= 0:
        raise ValueError("reference points coincide")
    if reference_distance_m <= 0:
        raise ValueError("reference distance must be positive")
    return reference_distance_m / d


class FrameGraph:
    """Resolve transforms between named frames by chaining stored transforms (and their inverses)."""

    def __init__(self, transforms: Iterable[Transform]):
        self._edges: dict[str, list[tuple[str, np.ndarray]]] = {}
        for t in transforms:
            m = as_matrix(t.matrix)
            self._edges.setdefault(t.from_frame, []).append((t.to_frame, m))
            self._edges.setdefault(t.to_frame, []).append((t.from_frame, np.linalg.inv(m)))

    def resolve(self, from_frame: str, to_frame: str) -> np.ndarray:
        if from_frame == to_frame:
            return np.eye(4)
        seen = {from_frame}
        queue: deque[tuple[str, np.ndarray]] = deque([(from_frame, np.eye(4))])
        while queue:
            frame, acc = queue.popleft()
            for nxt, m in self._edges.get(frame, []):
                if nxt in seen:
                    continue
                total = m @ acc
                if nxt == to_frame:
                    return total
                seen.add(nxt)
                queue.append((nxt, total))
        raise KeyError(f"no transform chain from frame '{from_frame}' to '{to_frame}'")
