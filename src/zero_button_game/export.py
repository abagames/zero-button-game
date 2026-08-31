from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .core import sha256_file
from .render import build_contact_ppm


class ExportError(RuntimeError):
    pass


def tool_version(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        return "missing"
    result = subprocess.run([path, "-version"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return result.stdout.splitlines()[0] if result.stdout else "unknown"


def require_tools() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe", "gifsicle") if shutil.which(name) is None]
    if missing:
        raise ExportError("missing required tools: " + ", ".join(missing))


def _run(command: list[str]) -> None:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        tail = "\n".join(result.stderr.splitlines()[-12:])
        raise ExportError(f"command failed ({result.returncode}): {' '.join(command[:5])}\n{tail}")


def probe_media(path: Path) -> dict:
    command = [
        "ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name,pix_fmt,r_frame_rate,avg_frame_rate,nb_frames,nb_read_frames,duration:format=duration,size",
        "-of", "json", str(path),
    ]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise ExportError(f"ffprobe failed for {path}: {result.stderr}")
    raw = json.loads(result.stdout)
    stream = raw["streams"][0]
    rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
    numerator, denominator = map(int, rate.split("/"))
    fps = numerator / denominator if denominator else 0.0
    frames = int(stream.get("nb_read_frames") or stream.get("nb_frames") or 0)
    duration = float(stream.get("duration") or raw.get("format", {}).get("duration") or 0.0)
    return {
        "width": int(stream["width"]), "height": int(stream["height"]), "fps": fps,
        "frames": frames, "duration_ms": int(round(duration * 1000)), "codec": stream.get("codec_name"),
        "pixel_format": stream.get("pix_fmt"), "bytes": path.stat().st_size, "sha256": sha256_file(path),
    }


SUPPORTED_FORMATS = ("gif", "mp4")
DEFAULT_FORMATS = ("gif", "mp4")


def parse_formats(value: str) -> tuple[str, ...]:
    """Parse a ``--format`` value such as ``gif``, ``mp4`` or ``gif,mp4``.

    The result is normalized to the canonical ``(gif, mp4)`` ordering so that
    ``mp4,gif`` and ``gif,mp4`` produce byte-identical works.
    """
    names = [item.strip().lower() for item in str(value).split(",") if item.strip()]
    if not names:
        raise ValueError("format must name at least one of: gif, mp4")
    unknown = [name for name in names if name not in SUPPORTED_FORMATS]
    if unknown:
        raise ValueError("unsupported format(s): " + ", ".join(unknown) + " (choose from gif, mp4)")
    if len(set(names)) != len(names):
        raise ValueError("format names must not repeat")
    return tuple(name for name in SUPPORTED_FORMATS if name in names)


def export_media(frames_dir: Path, output_dir: Path, fps: int, frame_count: int, formats: tuple[str, ...] = DEFAULT_FORMATS) -> list[dict]:
    """Encode the requested delivery formats and return their probes.

    The probes come back in the canonical ``gif`` then ``mp4`` order, with the
    formats that were not requested simply absent.
    """
    formats = parse_formats(",".join(formats)) if not isinstance(formats, str) else parse_formats(formats)
    require_tools()
    pattern = str(frames_dir / "frame_%04d.ppm")
    palette = output_dir / "palette.png"
    gif_path = output_dir / "animation.gif"
    raw_gif = output_dir / "animation.ffmpeg.gif"
    mp4_path = output_dir / "preview.mp4"
    common = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(fps), "-start_number", "0", "-i", pattern, "-frames:v", str(frame_count)]
    probes: list[dict] = []
    if "gif" in formats:
        _run(common + ["-vf", "palettegen=max_colors=128:stats_mode=full:reserve_transparent=0", str(palette)])
        _run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(fps), "-start_number", "0", "-i", pattern,
            "-i", str(palette), "-lavfi", "paletteuse=dither=sierra2_4a", "-frames:v", str(frame_count), "-loop", "0",
            "-gifflags", "-offsetting-transdiff", str(raw_gif),
        ])
        # FFmpeg 6 may attach a local table to frame zero even with a supplied global
        # palette. Gifsicle normalizes the already-quantized stream to one global table.
        _run(["gifsicle", "--colors", "128", "--no-dither", "-o", str(gif_path), str(raw_gif)])
        raw_gif.unlink(missing_ok=True)
        palette.unlink(missing_ok=True)
        info = gifsicle_info(gif_path)
        palette_match = re.search(r"global color table \[(\d+)\]", info)
        actual_colors = int(palette_match.group(1)) if palette_match else 128
        gif = probe_media(gif_path)
        gif.update({"kind": "gif", "path": "animation.gif", "colors": actual_colors, "dither": "sierra2_4a", "loop_count": 0})
        probes.append(gif)
    if "mp4" in formats:
        _run(common + [
            "-c:v", "libx264", "-profile:v", "high", "-crf", "20", "-pix_fmt", "yuv420p", "-vsync", "cfr",
            "-movflags", "+faststart", "-metadata", "creation_time=1970-01-01T00:00:00Z", str(mp4_path),
        ])
        mp4 = probe_media(mp4_path)
        mp4.update({"kind": "mp4", "path": "preview.mp4"})
        probes.append(mp4)
    return probes


def export_keyframes_and_contact(frames: list[Path], output_dir: Path, timeline: dict[str, int], fps: int = 20) -> dict[str, str]:
    """Write keyframes and both contact sheets.

    Naming contract: any artifact whose name carries the ``_full`` marker is
    review/investigation only because it shows solved-board pixels. Everything
    else is safe to distribute. Pre-reveal keyframes stay in ``keyframes/``;
    reveal-onward keyframes live in ``keyframes_full/``.
    """
    reveal = timeline["reveal_start"]
    last = len(frames) - 1
    clamp = lambda index: min(last, max(0, index))
    indices = [
        clamp(timeline["appearance"]),
        clamp(timeline["appearance"] + timeline["thinking"] // 2),
        clamp(reveal),
        clamp(reveal + (timeline["solve"] * 3) // 4),
        clamp(timeline.get("goal_keyframe", timeline["solve_end"] + timeline["result"] // 2)),
    ]
    names = ["problem_ready", "thinking_mid", "reveal_start", "solve_75", "goal_reached"]
    hashes: dict[str, str] = {}
    for name, index in zip(names, indices):
        # A frame at or after reveal_start already draws solution pixels.
        directory = output_dir / ("keyframes" if index < reveal else "keyframes_full")
        directory.mkdir(exist_ok=True)
        destination = directory / f"{name}.png"
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(frames[index]), "-frames:v", "1", str(destination)])
        hashes[name] = sha256_file(destination)
    _write_contact(frames, indices, names, output_dir / "contact_sheet_full.png", fps)
    public_indices = [clamp(index) for index in (timeline["appearance"], timeline["appearance"] + timeline["thinking"] // 2, reveal - 1)]
    public_names = ["problem_ready", "thinking_mid", "thinking_end"]
    _write_contact(frames, public_indices, public_names, output_dir / "contact_sheet.png", fps)
    return hashes


def _write_contact(frames: list[Path], indices: list[int], names: list[str], destination: Path, fps: int) -> None:
    ppm = destination.with_suffix(".ppm")
    labels = [f"{name.upper()} {index / fps:.2f}S" for name, index in zip(names, indices)]
    build_contact_ppm([frames[index] for index in indices], labels, ppm)
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(ppm), "-frames:v", "1", str(destination)])
    ppm.unlink(missing_ok=True)


def decode_check(path: Path) -> None:
    _run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"])


def gifsicle_info(path: Path) -> str:
    result = subprocess.run(["gifsicle", "--info", str(path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise ExportError(f"gifsicle failed: {result.stdout}")
    return result.stdout
