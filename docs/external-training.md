# Training a Gaussian splat on a free or rented GPU

Parallel trains Gaussian splats on its own GPU worker (see [deployment](deployment.md#gpu-worker-isaac-sim-gaussian-splat-training)).
Without one, a reconstruction still gives camera poses, a sparse point cloud, scale and orientation, and an
**untrained initialisation** (the sparse points drawn as blobs), labelled as untrained.

This guide covers the alternative: take the reconstruction to a GPU you can borrow for an hour (Google Colab,
Kaggle, or a rented card), train there, and bring the splat back. Nothing about the platform's rules changes:

- the splat is recorded as **trained outside Parallel**, with who imported it, the trainer, GPU and settings
  it reported, and the check it passed; Parallel never presents it as its own training;
- it is an **appearance layer**. Like every captured visual, it is never treated as walkable or solid geometry,
  and it does not establish scale, measurement or calibration quality;
- importing creates a **new scene revision**. Earlier revisions, and the untrained visual, are unchanged;
- Parallel never runs uploaded code. The notebook is code you run on your own GPU session; what comes back is
  a data file that Parallel parses and validates.

## The steps

1. **Reconstruct as usual.** Upload the capture and start a reconstruction. With no GPU worker, the training
   stage is *skipped* and the job completes with the untrained initialisation.
2. **Open the job** under *Captures → Reconstruction jobs* and expand **Train the Gaussian splat on a free GPU**.
   Choose **Download training package** (a zip of the accepted frames and the COLMAP model, with a manifest of
   SHA-256 hashes, stored as an asset so the exact package is on record) and **Download notebook**.
3. **Open the notebook** in [Google Colab](https://colab.research.google.com) (File → Upload notebook) or
   Kaggle. Choose a **GPU** runtime (a T4 is enough). Run the cells in order, uploading the package when asked.
   The notebook installs nerfstudio, checks the package against its manifest, trains Splatfacto, exports the
   splat, brings it back into the reconstruction's frame if nerfstudio moved it, checks that it lines up, and
   downloads `splat.ply` and `training_info.json`.
4. **Import.** Back in the job, choose both files and **Import trained splat**. On success, open the new scene
   revision and use **Load** beside *Gaussian splat (trained outside Parallel)*.

## What Parallel checks on import

| Check | Why |
| --- | --- |
| Binary little-endian PLY with the Gaussian splat properties, finite values, size matching the header, at most 15 million Gaussians | A malformed or truncated file is refused with the reason |
| **Alignment with the reconstruction**: the median distance from the reconstruction's sparse points to the nearest Gaussian is at most 5% of the scene's spread, and the splat's spread is within 0.5 to 2 times the sparse points' | The splat is stored in the reconstruction's own frame and placed by the scene's existing alignment. A splat that nerfstudio re-oriented or re-scaled would sit in the wrong place, so it is refused with the measured numbers rather than shown misplaced |
| `training_info.json` names this reconstruction's job and, if present, the hash of the package Parallel issued | A splat trained from a different package, or from an edited one, is refused |
| The exact file is not already part of the scene | No duplicate layers |

The imported file is stored and hashed like any other asset, and `derived_from` points at the training package.
An audit event records the import, the alignment measurements and the training details. If `training_info.json`
is omitted the import still works and the layer says that no training information was supplied.

## How much video to plan for

These are planning figures from how the pipeline is configured, not measurements. The pipeline extracts 2 frames
per second by default and keeps at most 240 frames (more are thinned evenly), so **about two minutes of video
uses the default budget in full**. Longer captures lose detail unless you lower the frame rate or split the site.

| Module | Typical capture | Plan for | Notes |
| --- | --- | --- | --- |
| CrashLens (a collision site, tens of metres) | One slow walk-round, plus overlapping passes of the vehicles' rest positions | 1 to 2 minutes at the default 2 fps | Place at least two surveyed targets in view for scale; keep the original media for the evidence record |
| Mine Evacuation (a section of drive) | A steady walk along the section | 2 to 4 minutes, with `frame_rate` set to 1 | Low light is the limit, not length: use a strong, even head-mounted light and avoid blur |
| Construction Sites (a site or a zone) | Walk or drone loops | 3 to 5 minutes with `frame_rate` set to 1, or one capture per zone | Split large sites; one reconstruction per zone trains and aligns better than one huge one |
| Urban Planning (a street or block) | Walk, or drone at low height | 3 to 5 minutes with `frame_rate` set to 1 | Moving traffic and people reconstruct poorly; capture at quiet times |
| Urban Drainage (a manhole, channel or outfall) | A close, slow pass | 1 to 2 minutes per feature | Captured surfaces are not hidden infrastructure: invert levels and pipe sizes still need to be supplied |

**Training time on a free T4 (an estimate, not a measurement):** 150 to 240 frames at 15,000 iterations and a
downscale factor of 2 should take roughly 25 to 45 minutes, plus 5 to 10 minutes of set-up the first time while
nerfstudio compiles its CUDA kernels. Raise the iterations to 30,000 for more detail if the session allows.
Free sessions can end without warning, so download the result as soon as the notebook produces it.

## Free and cheap GPUs

Availability and limits change; check each service's current terms.

| Service | Cost | Fit |
| --- | --- | --- |
| Google Colab (free tier) | Free; a shared T4 for a limited session | Easiest start. Sessions are cut off, and a GPU is not guaranteed |
| Kaggle notebooks | Free weekly GPU allowance (needs a verified phone number) | A dependable fallback; set the notebook's accelerator to GPU and point `PACKAGE_ZIP` at the input file |
| RunPod, Vast.ai and similar | Pay by the hour, usually well under a pound for a T4-class card | No session cut-offs; the same notebook runs in their Jupyter environment |
| Your own deployment | Cost of a GPU machine | Connect a GPU worker instead: see [deployment](deployment.md#gpu-worker-isaac-sim-gaussian-splat-training) |

## Limits and what has not been verified

- The helper code in the notebook (package verification, conversion back to the reconstruction's frame, the
  alignment check, PLY reading and writing) is tested in this repository on synthetic data (`services/api/tests/test_external_training.py`),
  as are the import, its refusals and the access rules, against a real COLMAP reconstruction. The web panel, both downloads
  and a refused import have a browser test (`apps/web/e2e/external-training.spec.ts`); a successful import and loading the
  splat into the scene were checked by hand in a browser.
- **The training commands themselves have not been run on a GPU.** They use nerfstudio's documented Splatfacto and
  COLMAP data-parser options (orientation, centring and scale switched off so the splat stays in the
  reconstruction's frame). If your nerfstudio version names an option differently, adjust the command in step 6 of
  the notebook; the notebook's own alignment check, and Parallel's, will tell you if the result is not in the right
  frame. A splat that nerfstudio re-oriented is converted back using `dataparser_transforms.json`.
- View-dependent colour (the higher spherical-harmonic terms) is dropped by default to keep the file about five
  times smaller; set `KEEP_VIEW_DEPENDENT_COLOUR = True` to keep it. It is also dropped when a conversion back to
  the reconstruction's frame is needed, because it cannot be rotated here.
- A trained splat shows how the place looks. It does not make the scene more measurable: scale and measurement
  states come only from the scene's alignment and its independent checks.
- The package is built from the reconstruction's stored checkpoints (accepted frames and the COLMAP model). If they
  are missing, the API says so (`reconstruction_not_ready`); resume the reconstruction from its frame filtering
  stage to recreate them.

## Interfaces

| Method and path | Purpose |
| --- | --- |
| `POST /api/v1/reconstructions/{job_id}/training-package` | Build (or return) the job's training package asset; needs edit access |
| `GET /api/v1/external-training/notebook` | The Colab/Kaggle notebook |
| `POST /api/v1/reconstructions/{job_id}/import-splat` | Multipart `splat` (.ply) and optional `training_info` (.json); returns the new scene revision and the alignment check. Errors: `splat_invalid`, `splat_not_aligned` (with the measurements), `package_mismatch`, `training_info_invalid`, `already_imported`, `reconstruction_not_ready` |

The notebook is generated from `scripts/training_notebook/` (`build.py` embeds `splat_tools.py`); a test fails when the
committed notebook at `services/api/src/parallel_api/resources/parallel_splat_training.ipynb` is out of date.
