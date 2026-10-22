"""Upload verification and media inspection (spec section 13.3, stages 1-2).

Uploads are untrusted: file types are whitelisted by extension and confirmed by
content sniffing; parsing is limited to headers/metadata.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from parallel_scene import ply

KINDS: dict[str, str] = {
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".tif": "terrain",
    ".tiff": "terrain",
    ".mp4": "video",
    ".mov": "video",
    ".m4v": "video",
    ".mkv": "video",
    ".avi": "video",
    ".ply": "ply",
    ".splat": "gaussian",
    ".spz": "gaussian",
    ".ksplat": "gaussian",
    ".las": "point_cloud",
    ".laz": "point_cloud",
    ".obj": "mesh",
    ".glb": "mesh",
    ".gltf": "mesh",
    ".stl": "mesh",
    ".epw": "weather",
    ".inp": "model",
    ".json": "model",
    ".geojson": "model",
    ".csv": "model",
    ".xml": "model",
    ".dxf": "model",
    ".zip": "package",
    ".pdf": "document",
    ".txt": "document",
    ".md": "document",
    ".sql": "result",
    ".err": "result",
    ".out": "result",
}

MAGIC = {
    "image": [b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"II*\x00", b"MM\x00*"],
    "terrain": [b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"],
    "ply": [b"ply"],
    "package": [b"PK\x03\x04", b"PK\x05\x06"],
    "document": [b"%PDF", b""],
    "mesh": [b"glTF", b"", b"solid", b"v ", b"#", b"o "],
}


class RejectedUpload(ValueError):
    pass


def classify(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".net.xml") or name.endswith(".rou.xml"):
        return "model"
    ext = Path(name).suffix
    if ext not in KINDS:
        raise RejectedUpload(f"File type '{ext or filename}' is not supported")
    return KINDS[ext]


def _sniff(path: Path, kind: str) -> None:
    head = path.read_bytes()[:16] if path.stat().st_size < 16 else open(path, "rb").read(16)
    sigs = MAGIC.get(kind)
    if sigs is None:
        return
    if b"" in sigs:
        return
    if not any(head.startswith(s) for s in sigs):
        raise RejectedUpload(f"File content does not match its {kind} extension")


def inspect(path: Path, filename: str) -> tuple[str, dict[str, Any]]:
    kind = classify(filename)
    _sniff(path, kind)
    info: dict[str, Any] = {"verified": True}
    if kind == "image":
        info.update(_image(path))
    elif kind == "video":
        info.update(_video(path))
    elif kind == "ply":
        h = ply.read_header(path)
        kind = "gaussian" if h.is_gaussian_splat else "point_cloud"
        info.update(
            {
                "vertex_count": h.vertex_count,
                "format": h.format,
                "properties": [p for p, _ in h.properties][:40],
                "gaussian_splat": h.is_gaussian_splat,
                "has_colour": h.has_colour,
            }
        )
    elif kind == "terrain":
        info.update(_raster(path))
        if not info.get("crs"):
            kind = "image"
    elif kind == "weather":
        first = path.read_text(errors="replace").splitlines()[0] if path.stat().st_size else ""
        if not first.startswith("LOCATION"):
            raise RejectedUpload("EPW files must start with a LOCATION record")
        info["location"] = first.split(",")[1:4]
    elif kind == "package":
        import zipfile

        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
        except zipfile.BadZipFile as exc:
            raise RejectedUpload("Invalid zip archive") from exc
        info.update({"members": names[:200], "member_count": len(names)})
    return kind, info


def _image(path: Path) -> dict[str, Any]:
    from PIL import ExifTags, Image

    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            out: dict[str, Any] = {
                "width": im.width,
                "height": im.height,
                "mode": im.mode,
                "format": im.format,
            }
            exif = im.getexif()
            tags = {ExifTags.TAGS.get(k, str(k)): v for k, v in exif.items()} if exif else {}
            for k in ("Make", "Model", "DateTime", "Software"):
                if k in tags:
                    out[f"exif_{k.lower()}"] = str(tags[k])[:100]
            out["exif_has_gps"] = 34853 in exif if exif else False
            return out
    except Exception as exc:  # Pillow raises many types for corrupt files
        raise RejectedUpload(f"Unreadable image: {exc}") from exc


def _video(path: Path) -> dict[str, Any]:
    cp = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if cp.returncode != 0:
        raise RejectedUpload("ffprobe could not read the video")
    data = json.loads(cp.stdout)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if v is None:
        raise RejectedUpload("No video stream found")
    num, _, den = (v.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {
        "duration_s": float(data.get("format", {}).get("duration", 0) or 0),
        "width": v.get("width"),
        "height": v.get("height"),
        "codec": v.get("codec_name"),
        "fps": round(fps, 3),
        "frames": int(v.get("nb_frames", 0) or 0),
    }


def _raster(path: Path) -> dict[str, Any]:
    try:
        import rasterio

        with rasterio.open(path) as ds:
            return {
                "crs": ds.crs.to_string() if ds.crs else None,
                "width": ds.width,
                "height": ds.height,
                "bounds": list(ds.bounds),
                "resolution": list(ds.res),
                "bands": ds.count,
            }
    except Exception:
        return {"crs": None}
