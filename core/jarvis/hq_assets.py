"""Free (CC0) 3D asset packs for the HQ: fetched on demand into C:/JarvisMedia/hq-assets (or brain/files/hq/assets), never into the repo.

The 3D HQ draws everything itself until these are present; with them it uses real animated people (Quaternius),
trees, plants and furniture (Kenney, Poly Haven). Each machine fetches its own copy (`bin/jarvis hq assets fetch`, or
the button in the HQ's Settings), so the public repository stays small and the laptop gets the same look.

Every pack is public domain (Creative Commons Zero): no attribution required, commercial use allowed. Sources and
checksums are pinned here; a changed archive is refused and reported rather than installed.
"""
from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import os
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import paths

UA = {"User-Agent": "JARVIS-HQ/1.0 (+https://github.com/marley-gpt/jarvis; CC0 asset fetch)"}
POLYHAVEN = "https://api.polyhaven.com/files/"

# Archives: which members to keep (fnmatch on the full member path) and how many leading folders to drop.
PACKS = {
    "quaternius-characters": {
        "title": "Quaternius: Ultimate Animated Character Pack (Nov 2019)",
        "what": "52 rigged, animated low-poly people (suits, casual, workers, doctors); FBX",
        "source": "https://quaternius.com (mirror: https://opengameart.org/content/animated-characters-pack)",
        "url": "https://opengameart.org/sites/default/files/ultimate_animated_character_pack_by_quaternius.zip",
        "sha256": "0295062cbc72da52ee804f39ac38694901a40458011f9b2638fc7ef13ed509da",
        "size_mb": 48, "keep": ["*/FBX/*.fbx"], "strip": 2,
    },
    "kenney-nature": {
        "title": "Kenney: Nature Kit 2.1",
        "what": "330 trees (palms, pines, oaks), bushes, flowers, rocks, paths; GLB",
        "source": "https://kenney.nl/assets/nature-kit (mirror: https://opengameart.org/content/nature-kit)",
        "url": "https://opengameart.org/sites/default/files/Nature%20Kit%20%282.1%29.zip",
        "sha256": "fa7974a0d342bfe63c38664ba9f8ec1a4aab8ea25f099bdc56870e33588c4d9d",
        "size_mb": 11, "keep": ["*Models/GLTF format/*.glb", "*License.txt"], "strip": 2,
    },
}
# Poly Haven models (photographed, physically based; CC0), fetched one by one through their API at 1k textures.
POLYHAVEN_MODELS = ["metal_office_desk", "modern_arm_chair_01", "sofa_02", "modern_coffee_table_01", "coffee_table_round_01",
                    "potted_plant_01", "potted_plant_02", "potted_plant_04", "classic_laptop", "painted_wooden_bench",
                    "modular_street_seating", "Sofa_01", "ArmChair_01"]
POLYHAVEN_RES = "1k"

_lock = threading.Lock()
_progress: dict = {"running": False, "log": [], "started": None, "finished": None, "error": None}


def root() -> Path:
    """Where the packs live: brain/config.json `hq_assets_dir`, else C:/JarvisMedia/hq-assets when that big-media folder
    exists (the owner keeps large files out of OneDrive), else brain/files/hq/assets."""
    cfg = paths.config().get("hq_assets_dir")
    if cfg:
        return Path(cfg)
    media = Path(os.environ.get("JARVIS_MEDIA") or r"C:\JarvisMedia")
    if media.is_dir():
        return media / "hq-assets"
    return paths.home() / "files" / "hq" / "assets"


def _manifest_path() -> Path:
    return root() / "manifest.json"


def manifest() -> dict:
    try:
        return json.loads(_manifest_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_manifest(m: dict) -> None:
    root().mkdir(parents=True, exist_ok=True)
    _manifest_path().write_text(json.dumps(m, indent=1, sort_keys=True), encoding="utf-8")


def ready() -> dict:
    """What the page can use: pack ids that are installed, and which Poly Haven models are present."""
    m = manifest()
    packs = [k for k in PACKS if k in m and (root() / k).is_dir()]
    models = [k for k in POLYHAVEN_MODELS if (root() / "polyhaven" / k / f"{k}_{POLYHAVEN_RES}.gltf").is_file()]
    return {"packs": packs, "polyhaven": models, "base": "/hq-assets/"}


def status() -> dict:
    m = manifest()
    out = {"root": str(root()), "packs": {}, "polyhaven": {}, "progress": dict(_progress, log=_progress["log"][-12:])}
    for k, p in PACKS.items():
        out["packs"][k] = {"title": p["title"], "what": p["what"], "size_mb": p["size_mb"], "licence": "CC0",
                           "ready": k in m and (root() / k).is_dir(), "files": m.get(k, {}).get("files")}
    for k in POLYHAVEN_MODELS:
        out["polyhaven"][k] = (root() / "polyhaven" / k / f"{k}_{POLYHAVEN_RES}.gltf").is_file()
    out["ready"] = all(v["ready"] for v in out["packs"].values()) and all(out["polyhaven"].values())
    return out


def _log(line: str, log=None) -> None:
    _progress["log"].append(f"{time.strftime('%H:%M:%S')} {line}")
    if log:
        log(line)


def _get(url: str, log=None) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 (pinned, public, checksummed below)
        total = int(r.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        while True:
            chunk = r.read(1 << 18)
            if not chunk:
                break
            buf.write(chunk)
            if total and buf.tell() % (1 << 22) < (1 << 18):
                _log(f"  {buf.tell() / 1e6:.0f} of {total / 1e6:.0f} MB", None)
        return buf.getvalue()


def _safe_target(base: Path, rel: str) -> Path | None:
    target = (base / rel).resolve()
    return target if str(target).startswith(str(base.resolve())) else None


def fetch_pack(key: str, log=None) -> dict:
    p = PACKS[key]
    _log(f"{p['title']}: downloading ({p['size_mb']} MB)", log)
    data = _get(p["url"])
    digest = hashlib.sha256(data).hexdigest()
    if digest != p["sha256"]:
        raise ValueError(f"{key}: the archive changed (sha256 {digest[:12]}…, expected {p['sha256'][:12]}…); not installed")
    dest = root() / key
    dest.mkdir(parents=True, exist_ok=True)
    kept = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            if info.is_dir() or not any(fnmatch.fnmatch(info.filename, pat) for pat in p["keep"]):
                continue
            parts = info.filename.split("/")[p["strip"]:] or [info.filename.rsplit("/", 1)[-1]]   # a licence above the kept folders lands at the pack root
            if any(x in ("..", "") for x in parts[:-1]):
                continue
            target = _safe_target(dest, "/".join(parts))
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
            kept += 1
    m = manifest()
    m[key] = {"title": p["title"], "licence": "CC0", "source": p["source"], "sha256": digest, "files": kept,
              "fetched": time.strftime("%Y-%m-%d %H:%M")}
    _save_manifest(m)
    _log(f"{p['title']}: {kept} files ready", log)
    return m[key]


def fetch_polyhaven(model: str, log=None) -> dict:
    """One Poly Haven model: the glTF at POLYHAVEN_RES with its buffer and textures, laid out as the glTF expects."""
    info = json.loads(_get(POLYHAVEN + model).decode("utf-8"))
    entry = info["gltf"][POLYHAVEN_RES]["gltf"]
    dest = root() / "polyhaven" / model
    dest.mkdir(parents=True, exist_ok=True)
    files = {f"{model}_{POLYHAVEN_RES}.gltf": entry["url"]}
    for rel, inc in (entry.get("include") or {}).items():
        files[rel] = inc["url"]
    _log(f"Poly Haven {model}: {len(files)} files", log)
    n = 0
    for rel, url in files.items():
        target = _safe_target(dest, rel)
        if target is None:
            continue
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_get(url))
        n += 1
    m = manifest()
    m.setdefault("polyhaven", {})[model] = {"licence": "CC0", "source": f"https://polyhaven.com/a/{model}", "files": n,
                                           "fetched": time.strftime("%Y-%m-%d %H:%M")}
    _save_manifest(m)
    return m["polyhaven"][model]


def fetch(only: list[str] | None = None, log=None) -> dict:
    """Fetch everything missing (or just `only`: pack ids and/or Poly Haven model ids). Sequential, to keep the PC responsive."""
    with _lock:
        _progress.update(running=True, started=time.time(), finished=None, error=None, log=[])
    try:
        st = status()
        packs = [k for k in PACKS if (not only or k in only) and not st["packs"][k]["ready"]]
        models = [k for k in POLYHAVEN_MODELS if (not only or k in only) and not st["polyhaven"][k]]
        for k in packs:
            fetch_pack(k, log)
        for k in models:
            fetch_polyhaven(k, log)
        _log("All asset packs are in place." if not (packs or models) else f"Done: {len(packs)} packs, {len(models)} models.", log)
    except Exception as exc:  # the page shows it; the command prints it
        _progress["error"] = str(exc)
        _log(f"Stopped: {exc}", log)
        raise
    finally:
        _progress.update(running=False, finished=time.time())
    return status()


def fetch_in_background(only: list[str] | None = None) -> bool:
    """Start a fetch thread for the dashboard; False if one is already running."""
    if _progress["running"]:
        return False
    threading.Thread(target=lambda: _try(fetch, only), daemon=True, name="hq-assets").start()
    return True


def _try(fn, *a):
    try:
        fn(*a)
    except Exception:
        pass


# Served to the page: only these types, only from under root().
TYPES = {".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".bin": "application/octet-stream", ".fbx": "application/octet-stream",
         ".obj": "text/plain; charset=utf-8", ".mtl": "text/plain; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
         ".jpeg": "image/jpeg", ".webp": "image/webp", ".txt": "text/plain; charset=utf-8", ".json": "application/json"}


def serve(rel: str) -> tuple[bytes, str] | None:
    rel = rel.lstrip("/")
    if not rel or "\\" in rel:
        return None
    target = _safe_target(root(), rel)
    if target is None or not target.is_file():
        return None
    ctype = TYPES.get(target.suffix.lower())
    return (target.read_bytes(), ctype) if ctype else None
