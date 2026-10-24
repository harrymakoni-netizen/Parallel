"""Generate services/api/src/parallel_api/resources/parallel_splat_training.ipynb.

    uv run python scripts/training_notebook/build.py          # write the notebook
    uv run python scripts/training_notebook/build.py --check  # exit 1 if the committed notebook is stale

The notebook embeds splat_tools.py verbatim, so the code that the tests exercise is the code that runs
on the GPU. It trains with nerfstudio's Splatfacto (the same trainer Parallel's own GPU worker uses).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "services" / "api" / "src" / "parallel_api" / "resources" / "parallel_splat_training.ipynb"


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}


def code(text: str) -> dict:
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
            "source": text.strip("\n").splitlines(keepends=True)}


def _embeddable(source: str) -> str:
    """The module without its docstring and ``__future__`` import, which are only valid at the top of a file."""
    body = source.split('"""', 2)[2]
    return "\n".join(ln for ln in body.splitlines() if ln.strip() != "from __future__ import annotations").strip()


def cells() -> list[dict]:
    tools = _embeddable((HERE / "splat_tools.py").read_text())
    return [
        md("""
# Parallel: train a Gaussian splat on a free GPU

This notebook turns a **training package** from Parallel into a trained Gaussian splat, using
nerfstudio's *Splatfacto* (the trainer Parallel's own GPU worker uses), and prepares the result for
**Import trained splat** in Parallel.

**What you need:** the training package `.zip` (Parallel: Captures, reconstruction job, *Train on a free GPU*).
**Runtime:** Runtime, Change runtime type, choose a **GPU** (T4 is enough).

**Rough time on a free T4 (an estimate, not a measurement):** a package of 150 to 240 frames trains for
15,000 iterations in about 25 to 45 minutes, plus 5 to 10 minutes of set-up the first time (nerfstudio
compiles its CUDA kernels). A free session can be cut off, so keep the page open and download the result
as soon as it is ready.

**What this notebook will not do:** it does not change the reconstruction's scale or orientation, and it
does not make the splat a measurement. A splat is how the place looks; Parallel keeps it separate from
the geometry used for simulation.
"""),
        code("""
# 1. Check the GPU
import subprocess, sys
smi = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                     capture_output=True, text=True)
if smi.returncode != 0:
    raise SystemExit("No GPU found. Use Runtime > Change runtime type and choose a GPU, then run again.")
GPU_NAME = smi.stdout.strip().splitlines()[0]
print("GPU:", GPU_NAME)
"""),
        code("""
# 2. Install nerfstudio (about 5 to 10 minutes). If pip reports a conflict, run Runtime > Restart session
#    once and run this notebook again from the top.
%pip install -q nerfstudio
import importlib.metadata as md_
print("nerfstudio", md_.version("nerfstudio"))
"""),
        md("## 3. The helper code (tested in the Parallel repository; do not edit)"),
        code(tools),
        code("""
# 4. Upload the training package (.zip). In Colab, a file chooser appears; on Kaggle or Drive,
#    set PACKAGE_ZIP to the file's path instead, for example "/kaggle/input/my-data/parallel-training-xxxx.zip".
import os, shutil, zipfile
from pathlib import Path

PACKAGE_ZIP = None  # e.g. "/content/drive/MyDrive/parallel-training-xxxx.zip"
if PACKAGE_ZIP is None:
    from google.colab import files
    up = files.upload()
    PACKAGE_ZIP = next(iter(up))
ROOT = Path("/content/capture")
shutil.rmtree(ROOT, ignore_errors=True)
with zipfile.ZipFile(PACKAGE_ZIP) as z:
    z.extractall(ROOT)
manifest = verify_package(ROOT)
print(f"Package for reconstruction job {manifest['job_id']}: {manifest['frames']} frames, "
      f"camera model {manifest['colmap_camera_model']}. All {len(manifest['files'])} files match the manifest.")
"""),
        code("""
# 5. Settings. 15,000 iterations is a good balance for a free GPU; raise to 30,000 for more detail if the
#    session allows. DOWNSCALE 2 halves the image size, which saves memory and time.
ITERATIONS = 15000
DOWNSCALE = 2
KEEP_VIEW_DEPENDENT_COLOUR = False  # True makes the file about 5 times larger for a small gain in realism
"""),
        code("""
# 6. Train. The orientation, centring and scale options keep the splat in the reconstruction's own frame.
import datetime, time
STARTED = datetime.datetime.now(datetime.timezone.utc).isoformat()
t0 = time.time()
CMD = [
    "ns-train", "splatfacto", "--data", str(ROOT), "--output-dir", "/content/outputs",
    "--max-num-iterations", str(ITERATIONS), "--vis", "tensorboard",
    "colmap", "--colmap-path", "colmap/sparse/0", "--images-path", "images",
    "--downscale-factor", str(DOWNSCALE), "--eval-mode", "all",
    "--orientation-method", "none", "--center-method", "none", "--auto-scale-poses", "False",
    "--assume-colmap-world-coordinate-convention", "False",
]
print(" ".join(CMD))
subprocess.run(CMD, check=True)
FINISHED = datetime.datetime.now(datetime.timezone.utc).isoformat()
print(f"Training took {(time.time() - t0) / 60:.1f} minutes")
"""),
        code("""
# 7. Export, then bring the splat back into the reconstruction's frame and check it
import glob
cfg = sorted(glob.glob("/content/outputs/**/config.yml", recursive=True), key=os.path.getmtime)[-1]
EXPORT = Path("/content/export")
shutil.rmtree(EXPORT, ignore_errors=True)
subprocess.run(["ns-export", "gaussian-splat", "--load-config", cfg, "--output-dir", str(EXPORT)], check=True)
raw = sorted(EXPORT.glob("*.ply"))[0]
data, _ = read_ply(raw)
data, dropped = drop_non_finite(data)
tr = load_dataparser_transform(Path(cfg).parent / "dataparser_transforms.json")
if tr is not None and not is_identity(*tr):
    print(f"nerfstudio changed the frame (scale {tr[2]:.4f}); converting back to the reconstruction's frame")
    data = to_capture_frame(data, *tr)
elif not KEEP_VIEW_DEPENDENT_COLOUR:
    data = drop_view_dependent_colour(data)
check = alignment_check(positions(data), read_points3d(ROOT / "colmap/sparse/0/points3D.txt"))
print(f"{len(data):,} Gaussians ({dropped} invalid removed).")
print("Alignment with the reconstruction:", check)
if not check["passed"]:
    raise SystemExit("The splat does not line up with the reconstruction, and Parallel would refuse it. "
                     "Check that the training command in step 6 ran unchanged and that the package was not edited.")
print("PASS: the splat lines up with the reconstruction.")
"""),
        code("""
# 8. Save and download: upload BOTH files to Parallel with "Import trained splat"
import json
OUTDIR = Path("/content/parallel_splat")
shutil.rmtree(OUTDIR, ignore_errors=True)
OUTDIR.mkdir()
write_ply(OUTDIR / "splat.ply", data, ["Gaussian splat trained with nerfstudio Splatfacto for a Parallel training package"])
info = {
    "format": "parallel-splat-training-info/1",
    "job_id": manifest["job_id"], "package_manifest_sha256": manifest["manifest_sha256"],
    "trainer": "nerfstudio splatfacto", "trainer_version": md_.version("nerfstudio"), "gpu": GPU_NAME,
    "iterations": ITERATIONS, "downscale_factor": DOWNSCALE, "started_at": STARTED, "finished_at": FINISHED,
    "view_dependent_colour": bool(KEEP_VIEW_DEPENDENT_COLOUR), "command": " ".join(CMD),
    "alignment_check": check,
}
(OUTDIR / "training_info.json").write_text(json.dumps(info, indent=1))
print("splat.ply:", round((OUTDIR / "splat.ply").stat().st_size / 1e6, 1), "MB")
try:
    from google.colab import files
    files.download(str(OUTDIR / "splat.ply"))
    files.download(str(OUTDIR / "training_info.json"))
except ImportError:
    print("Download both files from", OUTDIR)
"""),
        md("""
## Back in Parallel

Open the project's **Captures** page, find the reconstruction job, choose **Import trained splat**, and select
`splat.ply` together with `training_info.json`. Parallel checks the file, records who imported it and from what
training, and adds it to the scene as a new revision. Your original untrained visual stays in the scene.
"""),
    ]


def notebook() -> str:
    nb = {
        "nbformat": 4, "nbformat_minor": 4,
        "metadata": {"accelerator": "GPU", "colab": {"provenance": [], "gpuType": "T4"},
                     "kernelspec": {"display_name": "Python 3", "name": "python3"}, "language_info": {"name": "python"}},
        "cells": cells(),
    }
    return json.dumps(nb, indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    text = notebook()
    if "--check" in sys.argv:
        sys.exit(0 if OUT.exists() and OUT.read_text() == text else 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT} ({len(text)} bytes)")
