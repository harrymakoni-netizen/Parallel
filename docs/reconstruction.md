# Reconstruction

Reconstruction turns photographs, video or drone footage into a scene that can be viewed, measured and
used to prepare models. It produces a **visual** representation: a reconstruction never establishes
scale, measurement accuracy or physical validity by itself, and each of those is assessed separately
and recorded as a quality state with its evidence.

The stage sequence follows the reference 3D Gaussian Splatting pipeline published at
[rakeshsuthar6322/3d_gaussian_splatting_for_model_city](https://github.com/rakeshsuthar6322/3d_gaussian_splatting_for_model_city).
Parallel's implementation (`workers/reconstruction`) is independent; no code from that repository is used.

## Capturing

The Captures page shows this guidance before upload (served from `GET /api/v1/capture-guidance`):

- **Overlap and coverage.** Move slowly so consecutive frames overlap by about 70 to 80 per cent; cover
  each surface from several viewpoints and heights; return to the start to close the loop.
- **Light and sharpness.** Stable, even light; avoid backlight and moving shadows; keep the camera
  steady (blurred frames are rejected).
- **Reference measurements.** At least two surveyed or tape-measured targets in view for scale, and at
  least one independent distance to check the result. Record each measurement's source and
  uncertainty: they become provenance.
- **Problem surfaces.** Moving people and vehicles, glass, water and featureless walls reconstruct
  poorly. Captured surfaces are not hidden infrastructure: pipe sizes, inverts and rock properties must
  be supplied or explicitly assumed.
- **Offline capture.** Capture without connectivity and upload later; hashes are recorded at upload.

## Inputs

A capture batch holds the photographs or videos and, optionally:

| File | Format | Used for |
| --- | --- | --- |
| `camera_positions.csv` | `frame,x,y,z` surveyed camera centres in metres | Scale and orientation by a similarity transform; every third position is held out as an independent check point |
| `reference_points.csv` | `name,x,y,z` surveyed targets | Measurement checks |

Every upload is size-limited while it streams, hashed (SHA-256), checked against its declared type by
content signature and stored once as an original.

## Pipeline

Each stage is checkpointed to object storage, so a job can resume from the last valid stage after a
failure or cancellation, and records its timing, log and outputs.

| # | Stage | What happens |
| --- | --- | --- |
| 1 | Upload verification | Re-hash the stored inputs against the SHA-256 recorded at upload |
| 2 | Media inspection | Image and video properties |
| 3 | Frame extraction | FFmpeg samples video at the configured rate; images are normalised |
| 4 | Frame filtering | Blur rejection by Laplacian variance (absolute floor and relative to the median sharpness) and near-duplicate removal by perceptual hash |
| 5 | Pose estimation | COLMAP feature extraction, matching and incremental mapping; a minimum registered fraction is required |
| 6 | Training | Splatfacto (nerfstudio) Gaussian training on a GPU worker, with the reference pipeline's presets. Without a GPU, the stage records why and the next stage exports an **untrained initialisation**, labelled as such |
| 7 | Visual export | Point cloud PLY and Gaussian splat PLY |
| 8 | Geometry generation | Dominant ground plane by RANSAC (an algorithm estimate); a collision mesh only where Open3D is available |
| 9 | Scale and orientation | With surveyed references: Umeyama similarity on control points, residuals on held-out check points. Without them: orientation from the ground plane only, and scale is recorded as unknown |
| 10 | Quality assessment | Registered frames, reprojection error, alignment residuals, independent distance checks against the scene's tolerance |
| 11 | Scene publication | A scene revision with the visual assets, transforms, quality states and provenance |

Settings (defaults in `pipeline.py`): `frame_rate` 2 frames/s, `blur_threshold` 15, `blur_relative`
0.35, `duplicate_hash_distance` 3, `max_frames` 240, `registered_fraction_required` 0.6,
`splatfacto_preset` `refined`, `tolerance_abs_m` 0.05 with its purpose, and `reference_source`.

## Setup

| Requirement | Purpose | Notes |
| --- | --- | --- |
| `ffmpeg` | Frame extraction | Ubuntu: `apt install ffmpeg` |
| `colmap` | Pose estimation | Verified with COLMAP 3.9.1 (Ubuntu 24.04 package, CPU) |
| NVIDIA GPU, `nvidia-smi`, nerfstudio `ns-train` | Gaussian splat training | Optional: on the `gpu` queue. Without it, scenes carry an untrained initialisation |
| Open3D | Collision mesh | Optional |

The worker image (`infra/docker/worker.Dockerfile`) includes FFmpeg and COLMAP. Run the reconstruction
worker with `celery -A parallel_reconstruction.worker worker -Q reconstruction`.

## Example

The CrashLens demonstration reconstructs a synthetic training road scene end to end, and the same
generator can be used directly:

```python
from pathlib import Path
from parallel_reconstruction.synthetic import generate, road_scene

gt = generate(road_scene(), Path("capture"), n_frames=36, width=560, height_px=420, video=False)
# capture/frames/*.png       36 rendered photographs of a textured road scene
# gt.camera_centres          the true camera positions, to write as camera_positions.csv
# gt.targets                 surveyed targets, to write as reference_points.csv
```

Then, through the API (or the Captures page): create a capture batch of kind `photos`, upload the frames
and the two CSV files into it, and start a reconstruction:

```http
POST /api/v1/projects/{project_id}/reconstructions
{"capture_batch_id": "...", "settings": {"scene_name": "Training road scene",
 "reference_source": "synthetic surveyed camera positions", "tolerance_abs_m": 0.10}}
```

On the benchmark host (4 CPUs, no GPU) 36 frames took 24.5 s end to end, 20.8 s of it in COLMAP; 72
frames took 70.2 s ([benchmarks](benchmarks.md)). In the demonstration (36 frames, every ninth
deliberately blurred), the filter rejected the 4 blurred frames and COLMAP registered all 32 remaining,
with 1,208 points and a mean reprojection error of 0.54 px. The similarity alignment used 22 surveyed
camera positions as control and held 10 out as check points, with an RMS residual of 0.04 m, and three
independent distance checks fell within the 0.1 m tolerance. The scene's quality states therefore
record a visual reconstruction, established scale and checked measurements; model preparation, review,
integration and calibration are met only as the models, reviews and runs that cite the scene appear.
