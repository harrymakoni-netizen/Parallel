# Benchmarks

Benchmark workloads from spec section 30.5, measured on real engine runs. **These are benchmark
workloads, not promised performance figures**: they describe one host and one configuration.
Regenerate with `scripts/benchmark.py` (see its docstring); raw results are in
[`benchmarks/engines.json`](benchmarks/engines.json) and [`benchmarks/viewer.json`](benchmarks/viewer.json).

## Host

- Recorded: 2026-10-23 06:47 UTC
- CPU: Intel(R) Xeon(R) Processor @ 2.80GHz, 4 logical CPUs; memory 15.7 GB; GPU: none (CPU only)
- OS: Linux 6.18.44-fc-v153; Python 3.13.16
- Mode: in-process API, tasks executed eagerly (queue latency excluded). Each engine workload ran 3 times; median (min to max) shown.

## Engine workloads

| Workload | Engine | End to end | Engine execution | Outputs |
| --- | --- | --- | --- | --- |
| JuPedSim evacuation of a level mine section, 100 agents, dt 0.01 s, CollisionFreeSpeedModel | 1.4.2 | 1.72 s (1.67 to 1.85) | 1.25 s (1.25 to 1.25) | 0.58 MB |
| JuPedSim evacuation of a level mine section, 160 agents, dt 0.01 s, CollisionFreeSpeedModel | 1.4.2 | 3.76 s (3.58 to 4.45) | 1.86 s (1.85 to 2.09) | 0.88 MB |
| SUMO, priority junction corridor (4 approaches, 30 network objects), 1 h demand | 1.28.0 | 4.69 s (4.54 to 4.92) | 3.68 s (3.46 to 3.71) | 8.48 MB |
| SWMM, 4 junctions, 4 conduits, 1 outfall, 60 min design storm | 5.2.4 | 0.71 s (0.53 to 0.86) | 0.25 s (0.24 to 0.46) | 0.43 MB |
| SimPy, site delivery operations, 10 replications with common random numbers | 4.1.2 | 0.5 s (0.47 to 0.52) | 0.25 s (0.25 to 0.27) | 0.27 MB |

End to end covers validation, input materialisation, the engine run, result collection,
normalisation, cross-checks and storage of every artifact. Engine execution is the time the
run spent in the `running` state.

## Report generation

PDF, CSV and JSON report of a three-run comparison (charts with table twins, provenance): 0.77 s median (0.32 to 0.78 s, n = 3).

## Reconstruction

| Workload | End to end | Slowest stages |
| --- | --- | --- |
| 36 synthetic photographs (560 x 420 px) of the training road scene; COLMAP 3.9.1 sparse reconstruction on CPU, scale alignment, checks, point cloud and Gaussian initialisation | 24.48 s | pose estimation 20.8 s, frame extraction 2.26 s, quality assessment 0.48 s |
| 72 synthetic photographs (560 x 420 px) of the training road scene; COLMAP 3.9.1 sparse reconstruction on CPU, scale alignment, checks, point cloud and Gaussian initialisation | 70.22 s | pose estimation 65.26 s, frame extraction 3.76 s, frame filtering 0.63 s |

Gaussian splat training needs a GPU worker. This host has none, so the training stage recorded
only the untrained initialisation (structure-from-motion points as isotropic Gaussians), and the
workspace labels it as untrained.

## Viewer

Browser: Chromium 141.0.7390.37, ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver), viewport 1440 x 900, application served
from http://127.0.0.1:5173 (the Vite development server, API on the same host). Software WebGL
(SwiftShader) on this CPU-only host, so frame rates are a lower bound for a machine with a GPU.
Measured with `apps/web/bench/viewer-benchmark.mjs`; times run from navigation to the state named.

| Measurement | Result |
| --- | --- |
| Open the scene workspace: prepared mine plan (13 objects), navigation to interactive | 1.27 s median (1.23 to 1.35, n = 3) |
| Open a reconstructed scene and load its point cloud (1208 points) | 1.16 s median (1.07 to 1.26, n = 3) |
| Orbit the reconstructed scene (frame rate while dragging) | 55.0 frames/s |
| Playback SUMO corridor, 1 h of traffic: 1001 frames, 450 agents, 47,519 positions, 1.08 MB playback derivative; load until playable | 1.66 s median (1.61 to 1.70, n = 3) |
| Playback SUMO corridor, 1 h of traffic at 16x (frame rate while playing) | 18.2 frames/s |
| Playback JuPedSim, 160 people: 113 frames, 160 agents, 9,672 positions, 0.14 MB playback derivative; load until playable | 1.71 s median (1.70 to 2.11, n = 3) |
| Playback JuPedSim, 160 people at 4x (frame rate while playing) | 23.0 frames/s |
