from __future__ import annotations

import os
import hashlib
import math
import shutil
import subprocess
import sys
import tempfile
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from .core import derive_seed, read_json, sha256_file, sha256_value, write_json
from .export import decode_check, export_media, probe_media
from .pipeline import GenerationRequest, generate
from .preset_loader import PresetRoots, use_preset_root
from .registry import get_plugin
from .render import ACTOR, BACKGROUND, FONT, GOAL, MUTED, PANEL, WHITE, _rect, _text, read_ppm
from .validation import timing_calibration_status_matches, validate_instance


SEQUENCE_BANDS = ("easy", "medium", "target")
SEQUENCE_AUDIENCE_BANDS = ("EASY", "MEDIUM", "HARD")
SEQUENCE_LABELS = ("1/3 EASY", "2/3 MEDIUM", "FINAL HARD")
SEQUENCE_ACCENTS = (ACTOR, GOAL, WHITE)
SEQUENCE_FPS = 20
PRESENTATION_PRESET = "three-puzzle-presentation-v2"
TIMELINE_PRESET = "three-puzzle-timeline-v2"
TITLE_FRAMES = 30
CARD_FRAMES = 18
COUNTDOWN_STATE_FRAMES = 6
FINAL_FRAMES = 20
SEQUENCE_TYPES = ("maze", "pipes", "parking", "packing", "lights", "fold", "mosaic")
TITLE_SAFE_AREA = (60, 100, 660, 620)
TITLE_SPECS = {
    "maze": {"name": "MAZE", "rule": "FIND THE PATH TO THE GOAL"},
    "pipes": {"name": "PIPES", "rule": "ROTATE PIPES TO REACH THE GOAL"},
    "parking": {"name": "PARKING", "rule": "SLIDE THE RED CAR TO THE EXIT"},
    "packing": {"name": "PACKING", "rule": "FIT ALL PIECES INTO THE SPACE"},
    "lights": {"name": "LIGHTS", "rule": "TOGGLE LIGHTS UNTIL ALL ARE ON"},
    "fold": {"name": "FOLD", "rule": "FOLD PAPER INTO THE TARGET"},
    "mosaic": {"name": "MOSAIC SHIFT", "rule": "SHIFT LINES TO RESTORE THE EMBLEM"},
}
TITLE_SECONDARY = "3 PUZZLES"
TITLE_PROGRESSION = "EASY -> MEDIUM -> HARD"
AUDIO_PRESET = "sequence-four-layer-audio-v1"
AUDIO_PRESET_VERSION = 1
AUDIO_SAMPLE_RATE = 48_000
AUDIO_CHANNELS = 2
AUDIO_CHANNEL_LAYOUT = "stereo"
AUDIO_BITRATE = "96k"
TICK_FRAME_OFFSETS = (0, 6, 12)
TRANSITION_FRAME_OFFSET = 1

LEGACY_CARD_FRAMES = 10
LEGACY_TICK_FRAME_OFFSETS = (1, 4, 7)
LEGACY_SEQUENCE_LABELS = ("1/3 EASY", "2/3 MEDIUM", "FINAL TARGET")


@dataclass(frozen=True)
class SequenceRequest:
    puzzle_type: str
    master_seed: int
    output: Path
    max_candidates: int | None = None
    audio_enabled: bool = False
    preset_root: Path | PresetRoots | None = None


@dataclass(frozen=True)
class SequenceResult:
    sequence: Path
    instances: tuple[Path, ...]


def _band_seed(master_seed: int, puzzle_type: str, ordinal: int, band: str) -> int:
    return derive_seed(master_seed, puzzle_type, ordinal, f"sequence-{band}") & ((1 << 64) - 1)


def representative_seed(collection_seed: int, puzzle_type: str) -> int:
    return derive_seed(collection_seed, puzzle_type, 0, "representative-sequence") & ((1 << 64) - 1)


def _ppm_bytes(rgb: bytes, width: int = 720, height: int = 720) -> bytes:
    return f"P6\n{width} {height}\n255\n".encode("ascii") + rgb


def _text_width(text: str, scale: int) -> int:
    return len(text) * 6 * scale - scale


def _title_card(puzzle_type: str) -> bytes:
    spec = TITLE_SPECS[puzzle_type]
    accent = SEQUENCE_ACCENTS[SEQUENCE_TYPES.index(puzzle_type) % len(SEQUENCE_ACCENTS)]
    buf = bytearray(bytes(BACKGROUND) * 720 * 720)
    _rect(buf, 720, 720, 60, 100, 660, 620, PANEL)
    _rect(buf, 720, 720, 60, 100, 660, 114, accent)
    _rect(buf, 720, 720, 60, 100, 74, 620, accent)
    lines = (
        (spec["name"], 8, 178, accent),
        (TITLE_SECONDARY, 5, 282, WHITE),
        (spec["rule"], 3, 400, WHITE),
        (TITLE_PROGRESSION, 3, 500, GOAL),
    )
    for text, scale, y, color in lines:
        _text(buf, 720, 720, (720 - _text_width(text, scale)) // 2, y, text, color, scale)
    return bytes(buf)


def _card(label: str, accent: tuple[int, int, int], final: bool = False, count: int | None = None) -> bytes:
    buf = bytearray(bytes(BACKGROUND) * 720 * 720)
    _rect(buf, 720, 720, 72, 210, 648, 510, PANEL)
    _rect(buf, 720, 720, 72, 210, 84, 510, accent)
    heading = "3/3 COMPLETE" if final else label
    subheading = "END" if final else "NEXT PUZZLE"
    heading_width = len(heading) * 36 - 6
    sub_width = len(subheading) * 24 - 4
    heading_y = 335 if count is not None else 315
    _text(buf, 720, 720, (720 - heading_width) // 2, heading_y, heading, accent, 6)
    _text(buf, 720, 720, (720 - sub_width) // 2, 425 if count is not None else 405, subheading, MUTED, 4)
    if count is not None:
        _text(buf, 720, 720, 330, 245, str(count), WHITE, 10)
    return bytes(buf)


def _overlay_badge(rgb: bytes, label: str, accent: tuple[int, int, int]) -> bytes:
    if len(rgb) != 720 * 720 * 3:
        raise ValueError("sequence component frame must be 720x720 RGB")
    buf = bytearray(rgb)
    _rect(buf, 720, 720, 476, 10, 710, 68, BACKGROUND)
    _rect(buf, 720, 720, 480, 14, 706, 64, PANEL)
    _rect(buf, 720, 720, 480, 14, 488, 64, accent)
    _text(buf, 720, 720, 500, 25, label, WHITE, 3)
    return bytes(buf)


def _write_sequence_frames(puzzle_type: str, instances: list[Path], frames_dir: Path) -> tuple[list[dict], int]:
    frames_dir.mkdir(parents=True)
    segments: list[dict] = []
    cursor = 0
    title_rgb = _title_card(puzzle_type)
    for _ in range(TITLE_FRAMES):
        (frames_dir / f"frame_{cursor:04d}.ppm").write_bytes(_ppm_bytes(title_rgb))
        cursor += 1
    for ordinal, (band, audience_band, label, accent, instance) in enumerate(
        zip(SEQUENCE_BANDS, SEQUENCE_AUDIENCE_BANDS, SEQUENCE_LABELS, SEQUENCE_ACCENTS, instances), start=1
    ):
        card_start = cursor
        for card_frame in range(CARD_FRAMES):
            count = 3 - card_frame // COUNTDOWN_STATE_FRAMES
            card_rgb = _card(label, accent, count=count)
            (frames_dir / f"frame_{cursor:04d}.ppm").write_bytes(_ppm_bytes(card_rgb))
            cursor += 1
        content_start = cursor
        source_frames = sorted((instance / "frames").glob("frame_*.ppm"))
        if not source_frames:
            raise ValueError(f"component has no canonical frames: {instance}")
        for source in source_frames:
            width, height, rgb = read_ppm(source)
            if (width, height) != (720, 720):
                raise ValueError(f"component frame has wrong dimensions: {source}")
            overlaid = _overlay_badge(rgb, label, accent)
            (frames_dir / f"frame_{cursor:04d}.ppm").write_bytes(_ppm_bytes(overlaid))
            cursor += 1
        segments.append({
            "ordinal": ordinal,
            "band": band,
            "audience_label": audience_band,
            "position_label": label,
            "card_start_frame": card_start,
            "card_frames": CARD_FRAMES,
            "countdown_state_frames": COUNTDOWN_STATE_FRAMES,
            "content_start_frame": content_start,
            "content_frames": len(source_frames),
            "end_frame_exclusive": cursor,
            "duration_ms": (CARD_FRAMES + len(source_frames)) * 1000 // SEQUENCE_FPS,
            "accent_rgb": list(accent),
        })
    final_start = cursor
    final_rgb = _card("", WHITE, final=True)
    for _ in range(FINAL_FRAMES):
        (frames_dir / f"frame_{cursor:04d}.ppm").write_bytes(_ppm_bytes(final_rgb))
        cursor += 1
    return segments, final_start


def audio_cues(
    segments: list[dict], instances: list[Path], puzzle_type: str,
    tick_frame_offsets: tuple[int, int, int] = TICK_FRAME_OFFSETS,
    transition_frame_offset: int = TRANSITION_FRAME_OFFSET,
) -> list[dict]:
    """Return frame/sample-aligned cues for the four-layer audio language."""
    cues = []
    profiles = {
        "maze": "light-progress-click",
        "pipes": "quarter-turn-mechanical",
        "parking": "short-slide",
        "packing": "piece-landing",
        "lights": "switch-toggle",
        "fold": "soft-paper-fold",
        "mosaic": "cyclic-tile-slide",
    }
    for item, instance in zip(segments, instances):
        if item["ordinal"] > 1:
            frame = item["card_start_frame"] + transition_frame_offset
            cues.append({
                "ordinal": item["ordinal"], "band": item["band"], "cue_type": "transition_low",
                "layer": "problem_transition", "frame": frame, "time_ms": frame * 1000 // SEQUENCE_FPS,
                "sample_offset": frame * AUDIO_SAMPLE_RATE // SEQUENCE_FPS,
                "duration_ms": 35, "sound_profile": "low-transition-180hz",
            })
        for count, (frame_offset, frequency) in enumerate(zip(tick_frame_offsets, (880, 880, 1250)), start=1):
            frame = item["card_start_frame"] + frame_offset
            cues.append({
                "ordinal": item["ordinal"], "band": item["band"], "cue_type": "count_tick",
                "layer": "countdown",
                "count": count, "frame": frame, "time_ms": frame * 1000 // SEQUENCE_FPS,
                "sample_offset": frame * AUDIO_SAMPLE_RATE // SEQUENCE_FPS,
                "duration_ms": 32, "frequency_hz": frequency,
                "visual_count": 4 - count,
                "start_cue_role": count == 3,
            })
        component = read_json(instance / "metadata.json")
        solution = read_json(instance / "solution.json")
        actions = solution["actions"]
        reveal = component["timeline"]["reveal_start_frame"]
        solve_frames = component["timeline"]["solve_frames"]
        for action_index, action in enumerate(actions):
            component_frame = reveal + action_index * solve_frames // len(actions)
            frame = item["content_start_frame"] + component_frame
            cues.append({
                "ordinal": item["ordinal"], "band": item["band"], "cue_type": "action",
                "layer": "operation", "sound_profile": profiles[puzzle_type],
                "action_index": action_index, "action_kind": action["kind"], "actor_id": action["actor_id"],
                "component_frame": component_frame, "frame": frame,
                "time_ms": frame * 1000 // SEQUENCE_FPS,
                "sample_offset": frame * AUDIO_SAMPLE_RATE // SEQUENCE_FPS,
                "duration_ms": 18 if puzzle_type == "maze" else 45 if puzzle_type in {"pipes", "lights"} else 65,
            })
        component_frame = reveal + solve_frames
        frame = item["content_start_frame"] + component_frame
        cues.append({
            "ordinal": item["ordinal"], "band": item["band"], "cue_type": "goal_chime",
            "layer": "completion", "sound_profile": "shared-rising-two-note",
            "component_frame": component_frame,
            "frame": frame, "time_ms": frame * 1000 // SEQUENCE_FPS,
            "sample_offset": frame * AUDIO_SAMPLE_RATE // SEQUENCE_FPS,
            "duration_ms": 170, "notes_hz": [660, 990],
        })
    return sorted(cues, key=lambda cue: (cue["frame"], cue["layer"], cue.get("action_index", -1)))


def _add_cue(pcm: array, cue: dict) -> None:
    duration = cue["duration_ms"] * AUDIO_SAMPLE_RATE // 1000
    attack = max(1, 4 * AUDIO_SAMPLE_RATE // 1000)
    for index in range(duration):
        release = max(0.0, (duration - index) / max(1, duration - attack))
        envelope = min(1.0, index / attack, release)
        seconds = index / AUDIO_SAMPLE_RATE
        cue_type = cue["cue_type"]
        if cue_type == "count_tick":
            sample = int(math.sin(2 * math.pi * cue["frequency_hz"] * seconds) * 2300 * envelope)
        elif cue_type == "transition_low":
            sample = int(math.sin(2 * math.pi * 180 * seconds) * 1700 * envelope)
        elif cue_type == "goal_chime":
            split = 80 * AUDIO_SAMPLE_RATE // 1000
            frequency = 660 if index < split else 990
            note_index = index if index < split else index - split
            sample = int(math.sin(2 * math.pi * frequency * note_index / AUDIO_SAMPLE_RATE) * 2500 * envelope)
        else:
            profile = cue["sound_profile"]
            if profile == "light-progress-click":
                sample = int(math.sin(2 * math.pi * 1600 * seconds) * 1700 * envelope)
            elif profile == "quarter-turn-mechanical":
                sample = int((math.sin(2 * math.pi * 360 * seconds) * 1500 + math.sin(2 * math.pi * 720 * seconds) * 800) * envelope)
            elif profile == "short-slide":
                progress = index / max(1, duration - 1)
                frequency = 520 - 260 * progress
                sample = int(math.sin(2 * math.pi * frequency * seconds) * 1800 * envelope)
            elif profile == "piece-landing":
                sample = int((math.sin(2 * math.pi * 190 * seconds) * 1800 + math.sin(2 * math.pi * 380 * seconds) * 700) * envelope)
            elif profile == "switch-toggle":
                sample = int((math.sin(2 * math.pi * 1200 * seconds) * 1600 + math.sin(2 * math.pi * 600 * seconds) * 700) * envelope)
            elif profile == "soft-paper-fold":
                noise = (((index * 1103515245 + 12345) >> 16) & 0x7FFF) - 16384
                sample = int((math.sin(2 * math.pi * 280 * seconds) * 900 + noise * 0.035) * envelope)
            elif profile == "cyclic-tile-slide":
                progress = index / max(1, duration - 1)
                frequency = 440 - 120 * progress
                sample = int((math.sin(2 * math.pi * frequency * seconds) * 1400 + math.sin(2 * math.pi * 880 * seconds) * 500) * envelope)
            else:
                raise ValueError(f"unknown operation sound profile: {profile}")
        position = (cue["sample_offset"] + index) * AUDIO_CHANNELS
        for channel in range(AUDIO_CHANNELS):
            mixed = pcm[position + channel] + sample
            pcm[position + channel] = max(-32768, min(32767, mixed))


def synthesize_sequence_audio(destination: Path, total_frames: int, cues: list[dict]) -> dict:
    samples_per_channel = total_frames * AUDIO_SAMPLE_RATE // SEQUENCE_FPS
    pcm = array("h", [0]) * (samples_per_channel * AUDIO_CHANNELS)
    for cue in cues:
        _add_cue(pcm, cue)
    encoded = array("h", pcm)
    if sys.byteorder != "little":
        encoded.byteswap()
    with wave.open(str(destination), "wb") as output:
        output.setnchannels(AUDIO_CHANNELS)
        output.setsampwidth(2)
        output.setframerate(AUDIO_SAMPLE_RATE)
        output.writeframes(encoded.tobytes())
    peak = max(abs(sample) for sample in pcm) if pcm else 0
    peak_dbfs = -120.0 if peak == 0 else 20 * math.log10(peak / 32768)
    return {
        "samples_per_channel": samples_per_channel,
        "source_pcm_sha256": "sha256:" + hashlib.sha256(encoded.tobytes()).hexdigest(),
        "source_peak_dbfs": round(peak_dbfs, 3),
    }


def _run_audio_command(command: list[str]) -> bytes:
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        tail = b"\n".join(result.stderr.splitlines()[-12:]).decode("utf-8", "replace")
        raise ValueError(f"audio command failed ({result.returncode}): {tail}")
    return result.stdout


def mux_sequence_audio(video: Path, wav_path: Path, duration_ms: int) -> None:
    destination = video.with_name(".sequence-with-audio.mp4")
    _run_audio_command([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video), "-i", str(wav_path),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(AUDIO_SAMPLE_RATE), "-ac", str(AUDIO_CHANNELS),
        "-t", f"{duration_ms / 1000:.3f}", "-movflags", "+faststart",
        "-metadata", "creation_time=1970-01-01T00:00:00Z",
        "-metadata:s:a:0", f"title={AUDIO_PRESET}", str(destination),
    ])
    os.replace(destination, video)


def audio_probe(path: Path) -> dict:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels,channel_layout,duration:format=duration",
        "-of", "json", str(path),
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise ValueError(f"audio ffprobe failed: {result.stderr}")
    import json
    raw = json.loads(result.stdout)
    if not raw.get("streams"):
        raise ValueError("audio stream is missing")
    stream = raw["streams"][0]
    duration = float(stream.get("duration") or raw.get("format", {}).get("duration") or 0)
    adts = _run_audio_command([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-map", "0:a:0", "-c", "copy", "-f", "adts", "-",
    ])
    decoded = _run_audio_command([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-map", "0:a:0", "-f", "s16le", "-acodec", "pcm_s16le",
        "-ar", str(AUDIO_SAMPLE_RATE), "-ac", str(AUDIO_CHANNELS), "-",
    ])
    samples = array("h")
    samples.frombytes(decoded)
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max((abs(sample) for sample in samples), default=0)
    mean_square = sum(sample * sample for sample in samples) / max(1, len(samples))
    peak_dbfs = -120.0 if peak == 0 else 20 * math.log10(peak / 32768)
    rms_dbfs = -120.0 if mean_square == 0 else 10 * math.log10(mean_square / (32768 * 32768))
    return {
        "codec": stream.get("codec_name"),
        "sample_rate": int(stream.get("sample_rate") or 0),
        "channels": int(stream.get("channels") or 0),
        "channel_layout": stream.get("channel_layout"),
        "duration_ms": int(round(duration * 1000)),
        "stream_sha256": "sha256:" + hashlib.sha256(adts).hexdigest(),
        "decoded_pcm_sha256": "sha256:" + hashlib.sha256(decoded).hexdigest(),
        "decoded_samples_per_channel": len(samples) // AUDIO_CHANNELS,
        "peak_dbfs": round(peak_dbfs, 3),
        "rms_dbfs": round(rms_dbfs, 3),
    }


def av_sync_probe(path: Path) -> dict:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,start_time,duration",
        "-of", "json", str(path),
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise ValueError(f"A/V sync ffprobe failed: {result.stderr}")
    import json
    streams = json.loads(result.stdout).get("streams", [])
    by_type = {stream["codec_type"]: stream for stream in streams}
    return {
        kind: {
            "start_ms": int(round(float(by_type[kind].get("start_time") or 0) * 1000)),
            "duration_ms": int(round(float(by_type[kind].get("duration") or 0) * 1000)),
        }
        for kind in ("video", "audio") if kind in by_type
    }


def _component_entry(sequence_root: Path, instance: Path, segment: dict, band_seed: int) -> dict:
    metadata = read_json(instance / "metadata.json")
    timing = metadata.get("timing_calibration", {})
    mp4 = next(item for item in metadata["artifacts"] if item["kind"] == "mp4")
    return {
        **segment,
        "master_seed": band_seed,
        "candidate_index": metadata["provenance"]["candidate_index"],
        "generation_seed_hex": metadata["provenance"]["generation_seed_hex"],
        "instance_id": metadata["instance_id"],
        "instance_path": str(instance.relative_to(sequence_root)),
        "problem_sha256": metadata["puzzle"]["problem_sha256"],
        "solution_sha256": metadata["solution"]["solution_sha256"],
        "preset": metadata["timeline"]["preset"],
        "thinking_time_seconds": metadata["timeline"]["problem_to_reveal_seconds"],
        "quality_preset": metadata["difficulty"]["quality_preset"],
        "quality_preset_sha256": metadata["difficulty"]["quality_preset_sha256"],
        "quality_preset_source": metadata["difficulty"].get("quality_preset_source"),
        "structural_calibration_status": metadata["difficulty"]["human"]["status"],
        "timing_calibration_status": timing.get("timing_status", "not-declared"),
        "component_frames": metadata["timeline"]["total_frames"],
        "component_duration_ms": mp4["duration_ms"],
        "component_mp4_sha256": mp4["sha256"],
    }


def sequence_component_requests(request: SequenceRequest, staging: Path) -> tuple[GenerationRequest, ...]:
    """Build all three requests through the sequence's injected preset root."""
    return tuple(
        GenerationRequest(
            request.puzzle_type, 1, band,
            _band_seed(request.master_seed, request.puzzle_type, ordinal, band),
            staging / "components" / band,
            max_candidates=request.max_candidates,
            keep_frames=True,
            formats=("mp4",),
            preset_root=request.preset_root,
        )
        for ordinal, band in enumerate(SEQUENCE_BANDS)
    )


def _generate_sequence(request: SequenceRequest) -> SequenceResult:
    if request.puzzle_type not in SEQUENCE_TYPES:
        raise ValueError(f"unknown puzzle type: {request.puzzle_type}")
    if not 0 <= request.master_seed < 2**64:
        raise ValueError("master_seed must be an unsigned 64-bit integer")
    request.output.mkdir(parents=True, exist_ok=True)
    plugin = get_plugin(request.puzzle_type)
    staging = Path(tempfile.mkdtemp(prefix=f".{request.puzzle_type}-sequence-", dir=request.output))
    instances: list[Path] = []
    seeds: list[int] = []
    try:
        for component_request in sequence_component_requests(request, staging):
            seed = component_request.master_seed
            seeds.append(seed)
            result = generate(component_request)
            instance = result.instances[0]
            validate_instance(instance, strict=True)
            instances.append(instance)
        frames_dir = staging / ".sequence-frames"
        segments, final_start = _write_sequence_frames(request.puzzle_type, instances, frames_dir)
        total_frames = final_start + FINAL_FRAMES
        export_media(frames_dir, staging, SEQUENCE_FPS, total_frames, ("mp4",))
        (staging / "preview.mp4").rename(staging / "sequence.mp4")
        cues = audio_cues(segments, instances, request.puzzle_type) if request.audio_enabled else []
        source_audio = None
        if request.audio_enabled:
            wav_path = staging / ".sequence-audio.wav"
            source_audio = synthesize_sequence_audio(wav_path, total_frames, cues)
            mux_sequence_audio(staging / "sequence.mp4", wav_path, total_frames * 1000 // SEQUENCE_FPS)
            wav_path.unlink()
        sequence_probe = probe_media(staging / "sequence.mp4")
        encoded_audio = audio_probe(staging / "sequence.mp4") if request.audio_enabled else None
        identity = {
            "plugin": request.puzzle_type,
            "master_seed": request.master_seed,
            "instances": [read_json(path / "metadata.json")["instance_id"] for path in instances],
            "layout_version": TIMELINE_PRESET,
            "presentation_preset": PRESENTATION_PRESET,
            "audio_preset": AUDIO_PRESET if request.audio_enabled else "none",
        }
        audio_label = "-audio" if request.audio_enabled else ""
        sequence_id = f"{request.puzzle_type}-sequence{audio_label}-{request.master_seed:06d}-{sha256_value(identity)[-8:]}"
        components = [
            _component_entry(staging, instance, segment, seed)
            for instance, segment, seed in zip(instances, segments, seeds)
        ]
        metadata = {
            "schema_version": "1.2.0",
            "sequence_id": sequence_id,
            "sequence_type": "easy-medium-target-presentation-v2",
            "plugin": {
                "type": request.puzzle_type,
                "plugin_version": plugin.plugin_version,
                "protocol": "plugin-protocol-v1-unchanged",
            },
            "provenance": {
                "master_seed": request.master_seed,
                "band_seed_derivation": "derive_seed(master_seed, plugin, zero_based_ordinal, sequence-<band>, v1) low64",
                "runtime": read_json(instances[0] / "metadata.json")["provenance"]["runtime"],
            },
            "bands": components,
            "timeline": {
                "preset": TIMELINE_PRESET,
                "fps": SEQUENCE_FPS,
                "width": 720,
                "height": 720,
                "title_frames": TITLE_FRAMES,
                "card_frames_each": CARD_FRAMES,
                "countdown_state_frames": COUNTDOWN_STATE_FRAMES,
                "final_start_frame": final_start,
                "final_frames": FINAL_FRAMES,
                "total_frames": total_frames,
                "duration_ms": total_frames * 1000 // SEQUENCE_FPS,
            },
            "presentation": {
                "preset": PRESENTATION_PRESET,
                "title_card": {
                    "plugin_name": TITLE_SPECS[request.puzzle_type]["name"],
                    "secondary": TITLE_SECONDARY,
                    "rule": TITLE_SPECS[request.puzzle_type]["rule"],
                    "progression": TITLE_PROGRESSION,
                    "start_frame": 0,
                    "frames": TITLE_FRAMES,
                    "duration_ms": TITLE_FRAMES * 1000 // SEQUENCE_FPS,
                    "safe_area": list(TITLE_SAFE_AREA),
                    "answer_content": "none",
                },
                "audience_band_mapping": {
                    "easy": "EASY",
                    "medium": "MEDIUM",
                    "target": "HARD",
                },
                "position_policy": "persistent 1/3 EASY, 2/3 MEDIUM, FINAL HARD badge plus a 0.9s 3-2-1 card",
                "countdown_policy": "3, 2 and 1 are each held for 6 frames; ticks start at card frames 0, 6 and 12",
                "completion_card": "3/3 COMPLETE / END",
                "end_policy": "hold completion card for 1.0s, then end; MP4 does not declare a loop",
            },
            "calibration": {
                "component_timing": "current per-plugin per-band standard",
                "sequence_experience": "uncalibrated",
                "scope_note": "structural difficulty and whole-sequence suitability are not population-calibrated",
            },
            "audio": {
                "enabled": request.audio_enabled,
                "preset": AUDIO_PRESET if request.audio_enabled else None,
                "preset_version": AUDIO_PRESET_VERSION if request.audio_enabled else None,
                "purpose": "four-layer language: three countdown ticks, plugin-specific Action cues, shared two-note goal chime and low problem-transition cue; no BGM or wrong-answer cue" if request.audio_enabled else "silent backward-compatible sequence",
                "layers": {
                    "countdown": "three ticks per problem at the 3, 2 and 1 interval starts; third is higher and serves as the start cue",
                    "operation": "one cue per Action with a plugin-specific sound profile",
                    "completion": "shared rising two-note goal chime",
                    "problem_transition": "short low cue before problems 2 and 3",
                } if request.audio_enabled else {},
                "sample_rate": AUDIO_SAMPLE_RATE if request.audio_enabled else None,
                "channels": AUDIO_CHANNELS if request.audio_enabled else 0,
                "channel_layout": AUDIO_CHANNEL_LAYOUT if request.audio_enabled else None,
                "cues": cues,
                "source": source_audio,
                "encoded": encoded_audio,
                "comfort_evaluation": "not evaluated with general users" if request.audio_enabled else "not applicable",
            },
            "artifact": {
                "path": "sequence.mp4",
                "sha256": sequence_probe["sha256"],
                "bytes": sequence_probe["bytes"],
                "width": sequence_probe["width"],
                "height": sequence_probe["height"],
                "fps": sequence_probe["fps"],
                "frames": sequence_probe["frames"],
                "duration_ms": sequence_probe["duration_ms"],
                "codec": sequence_probe["codec"],
                "pixel_format": sequence_probe["pixel_format"],
                "audio_streams": 1 if request.audio_enabled else 0,
            },
            "reproducibility": {
                "claim": "byte-for-byte within the recorded runtime and encoder version",
                "content_hash_excludes": ["validation.json"],
            },
        }
        write_json(staging / "sequence.json", metadata)
        write_json(staging / "validation.json", {"status": "pending"})
        report = validate_sequence(staging, strict=True)
        write_json(staging / "validation.json", report)
        for instance in instances:
            shutil.rmtree(instance / "frames")
        shutil.rmtree(frames_dir)
        destination = request.output / request.puzzle_type / sequence_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"OUTPUT_CONFLICT: {destination} exists")
        os.replace(staging, destination)
        relocated = tuple(destination / path.relative_to(staging) for path in instances)
        return SequenceResult(destination, relocated)
    except Exception:
        # As with the single-artifact pipeline, staging is retained as diagnostic evidence.
        raise


def generate_sequence(request: SequenceRequest) -> SequenceResult:
    with use_preset_root(request.preset_root):
        return _generate_sequence(request)


def _stream_counts(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise ValueError(f"ffprobe stream inspection failed: {result.stderr}")
    types = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return types.count("video"), types.count("audio")


def _sample_rgb(path: Path, frame: int, x: int, y: int) -> tuple[int, int, int]:
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vf", f"select=eq(n\\,{frame}),crop=2:2:{x}:{y}", "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode or len(result.stdout) != 12:
        raise ValueError(f"could not sample sequence frame {frame}")
    return tuple(sum(result.stdout[channel::3]) // 4 for channel in range(3))  # type: ignore[return-value]


def _near(actual: tuple[int, int, int], expected: tuple[int, int, int], tolerance: int = 28) -> bool:
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


def _frames_rgb(path: Path, frames: list[int]) -> dict[int, bytes]:
    requested = sorted(set(frames))
    expression = "+".join(f"eq(n\\,{frame})" for frame in requested)
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vf", f"select={expression}", "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    frame_bytes = 720 * 720 * 3
    if result.returncode or len(result.stdout) != frame_bytes * len(requested):
        raise ValueError(f"could not decode requested sequence frames: {requested}")
    return {
        frame: result.stdout[index * frame_bytes:(index + 1) * frame_bytes]
        for index, frame in enumerate(requested)
    }


def _crop_mae(actual: bytes, expected: bytes, crop: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = crop
    total = 0
    samples = 0
    for y in range(y0, y1):
        start = (y * 720 + x0) * 3
        end = (y * 720 + x1) * 3
        total += sum(abs(a - b) for a, b in zip(actual[start:end], expected[start:end]))
        samples += end - start
    return total / max(1, samples)


def validate_sequence(sequence: Path, strict: bool = True) -> dict:
    metadata = read_json(sequence / "sequence.json")
    checks_passed: list[str] = []
    checks_failed: list[str] = []
    plugin_type = metadata.get("plugin", {}).get("type")
    bands = metadata.get("bands", [])
    timeline = metadata.get("timeline", {})
    presentation = metadata.get("presentation", {})
    timeline_preset = timeline.get("preset")
    is_v2 = timeline_preset == TIMELINE_PRESET
    is_legacy = timeline_preset is None and timeline.get("title_frames", 0) in {0, None}
    expected_labels = SEQUENCE_LABELS if is_v2 else LEGACY_SEQUENCE_LABELS
    title_frames = TITLE_FRAMES if is_v2 else 0
    card_frames = CARD_FRAMES if is_v2 else LEGACY_CARD_FRAMES
    tick_offsets = TICK_FRAME_OFFSETS if is_v2 else LEGACY_TICK_FRAME_OFFSETS
    transition_offset = TRANSITION_FRAME_OFFSET if is_v2 else 0
    resolved_instances: list[Path] = []
    component_paths_safe = True
    if plugin_type in SEQUENCE_TYPES and [item.get("band") for item in bands] == list(SEQUENCE_BANDS):
        checks_passed.append("plugin_and_band_order")
    else:
        checks_failed.append("plugin or easy/medium/target order mismatch")
    if not (is_v2 or is_legacy):
        checks_failed.append("unknown sequence presentation/timeline preset")
    if is_v2:
        title = presentation.get("title_card", {})
        presentation_ok = (
            presentation.get("preset") == PRESENTATION_PRESET
            and timeline.get("title_frames") == TITLE_FRAMES
            and timeline.get("card_frames_each") == CARD_FRAMES
            and timeline.get("countdown_state_frames") == COUNTDOWN_STATE_FRAMES
            and title.get("plugin_name") == TITLE_SPECS.get(plugin_type, {}).get("name")
            and title.get("secondary") == TITLE_SECONDARY
            and title.get("rule") == TITLE_SPECS.get(plugin_type, {}).get("rule")
            and title.get("progression") == TITLE_PROGRESSION
            and title.get("start_frame") == 0
            and title.get("frames") == TITLE_FRAMES
            and title.get("duration_ms") == 1500
            and title.get("safe_area") == list(TITLE_SAFE_AREA)
            and title.get("answer_content") == "none"
        )
        if presentation_ok:
            checks_passed.append("presentation_v2_metadata")
        else:
            checks_failed.append("presentation v2 title/timeline metadata mismatch")
        if presentation.get("audience_band_mapping") == {"easy": "EASY", "medium": "MEDIUM", "target": "HARD"}:
            checks_passed.append("audience_target_to_hard_mapping")
        else:
            checks_failed.append("audience band mapping mismatch")
    cursor = title_frames
    for ordinal, (expected_band, expected_label, expected_accent) in enumerate(
        zip(SEQUENCE_BANDS, expected_labels, SEQUENCE_ACCENTS), start=1
    ):
        if ordinal > len(bands):
            break
        item = bands[ordinal - 1]
        instance = (sequence / item["instance_path"]).resolve()
        resolved_instances.append(instance)
        try:
            instance.relative_to(sequence.resolve())
        except ValueError:
            component_paths_safe = False
            checks_failed.append(f"{expected_band}: instance path escapes sequence")
            continue
        try:
            report = validate_instance(instance, strict=True)
            component = read_json(instance / "metadata.json")
            component_mp4 = probe_media(instance / "preview.mp4")
            expected_seed = _band_seed(metadata["provenance"]["master_seed"], plugin_type, ordinal - 1, expected_band)
            timing_calibration = component.get("timing_calibration", {})
            declared_standard_timing = (
                timing_calibration.get("timing_status") != "comparison-override-not-standard"
                and timing_calibration_status_matches(
                    timing_calibration, item["thinking_time_seconds"], plugin_type, expected_band,
                )
            )
            component_ok = (
                report["status"] == "passed"
                and component["puzzle"]["type"] == plugin_type
                and component["difficulty"]["accepted_band"] == expected_band
                and component["provenance"]["master_seed"] == item["master_seed"] == expected_seed
                and component["provenance"]["candidate_index"] == item["candidate_index"]
                and component["instance_id"] == item["instance_id"]
                and component["timeline"]["preset"] == item["preset"]
                and component["timeline"]["problem_to_reveal_seconds"] == item["thinking_time_seconds"]
                and item.get("timing_calibration_status") == timing_calibration.get("timing_status")
                and declared_standard_timing
                and component["timeline"]["total_frames"] == item["content_frames"] == item["component_frames"]
                and component_mp4["sha256"] == item["component_mp4_sha256"]
            )
            if component_ok:
                checks_passed.append(f"{expected_band}_component_strict")
            else:
                checks_failed.append(f"{expected_band}: component metadata mismatch")
        except Exception as error:
            checks_failed.append(f"{expected_band}: component validation failed: {error}")
        segment_ok = (
            item.get("ordinal") == ordinal
            and item.get("position_label") == expected_label
            and item.get("card_start_frame") == cursor
            and item.get("card_frames") == card_frames
            and item.get("content_start_frame") == cursor + card_frames
            and item.get("end_frame_exclusive") == cursor + card_frames + item.get("content_frames", -1)
            and item.get("accent_rgb") == list(expected_accent)
            and (not is_v2 or item.get("audience_label") == SEQUENCE_AUDIENCE_BANDS[ordinal - 1])
            and (not is_v2 or item.get("countdown_state_frames") == COUNTDOWN_STATE_FRAMES)
        )
        if segment_ok:
            checks_passed.append(f"{expected_band}_segment_timeline")
            cursor = item["end_frame_exclusive"]
        else:
            checks_failed.append(f"{expected_band}: segment timeline mismatch")
    audio_metadata = metadata.get("audio", {"enabled": False})
    audio_enabled = audio_metadata.get("enabled") is True
    if cursor == timeline.get("final_start_frame") and cursor + FINAL_FRAMES == timeline.get("total_frames"):
        checks_passed.append("contiguous_total_timeline")
    else:
        checks_failed.append("sequence total timeline mismatch")
    media = sequence / "sequence.mp4"
    try:
        decode_check(media)
        probe = probe_media(media)
        video_streams, audio_streams = _stream_counts(media)
        artifact = metadata.get("artifact", {})
        media_ok = (
            (probe["width"], probe["height"]) == (720, 720)
            and abs(probe["fps"] - SEQUENCE_FPS) <= 0.01
            and probe["frames"] == timeline.get("total_frames")
            and abs(probe["duration_ms"] - timeline.get("duration_ms", -9999)) <= 51
            and probe["codec"] == "h264"
            and probe["pixel_format"] == "yuv420p"
            and video_streams == 1
            and audio_streams == (1 if audio_enabled else 0)
            and artifact.get("audio_streams") == audio_streams
            and probe["sha256"] == artifact.get("sha256")
            and probe["bytes"] == artifact.get("bytes")
        )
        if media_ok:
            checks_passed.extend(["mp4_decode", "mp4_h264_yuv420p", "mp4_dimensions_fps_frames_duration", "mp4_sha256"])
        else:
            checks_failed.append("sequence MP4 probe or metadata mismatch")
        if audio_enabled:
            expected_cues = audio_cues(
                bands, resolved_instances, plugin_type, tick_offsets, transition_offset,
            ) if component_paths_safe and len(resolved_instances) == 3 else []
            layers = {cue["layer"] for cue in expected_cues}
            non_overlapping = all(
                left["sample_offset"] + left["duration_ms"] * AUDIO_SAMPLE_RATE // 1000 <= right["sample_offset"]
                for left, right in zip(expected_cues, expected_cues[1:])
            )
            frame_synced = all(
                cue["sample_offset"] == cue["frame"] * AUDIO_SAMPLE_RATE // SEQUENCE_FPS
                and cue["time_ms"] == cue["frame"] * 1000 // SEQUENCE_FPS
                for cue in expected_cues
            )
            if (
                audio_metadata.get("preset") == AUDIO_PRESET
                and audio_metadata.get("preset_version") == AUDIO_PRESET_VERSION
                and audio_metadata.get("cues") == expected_cues
                and layers == {"countdown", "operation", "completion", "problem_transition"}
                and non_overlapping
                and frame_synced
            ):
                checks_passed.extend(["audio_cue_timeline", "audio_four_layers", "audio_cues_non_overlapping"])
                operations = [cue for cue in expected_cues if cue["cue_type"] == "action"]
                declared_operations = [cue for cue in audio_metadata["cues"] if cue["cue_type"] == "action"]
                if operations == declared_operations:
                    checks_passed.append("audio_action_mapping")
                countdowns = [cue for cue in expected_cues if cue["cue_type"] == "count_tick"]
                if (
                    len(countdowns) == 9
                    and all(countdowns[index]["frequency_hz"] < countdowns[index + 2]["frequency_hz"] for index in (0, 3, 6))
                    and all(cue["visual_count"] == 4 - cue["count"] for cue in countdowns)
                ):
                    checks_passed.append("audio_three_tick_visual_sync")
            else:
                checks_failed.append("audio cue timeline or preset mismatch")
            actual_audio = audio_probe(media)
            declared_audio = audio_metadata.get("encoded", {})
            format_ok = (
                actual_audio["codec"] == "aac"
                and actual_audio["sample_rate"] == AUDIO_SAMPLE_RATE
                and actual_audio["channels"] == AUDIO_CHANNELS
                and actual_audio["channel_layout"] == AUDIO_CHANNEL_LAYOUT
            )
            if format_ok:
                checks_passed.append("audio_aac_48khz_stereo")
            else:
                checks_failed.append("audio codec, sample rate or channel layout mismatch")
            if abs(actual_audio["duration_ms"] - timeline.get("duration_ms", -9999)) <= 25:
                checks_passed.append("audio_duration_sync")
            else:
                checks_failed.append("audio duration differs from video timeline")
            av_sync = av_sync_probe(media)
            if (
                set(av_sync) == {"video", "audio"}
                and abs(av_sync["video"]["start_ms"] - av_sync["audio"]["start_ms"]) <= 1
                and av_sync["video"]["start_ms"] == 0
                and abs(av_sync["video"]["duration_ms"] - av_sync["audio"]["duration_ms"]) <= 25
                and abs(av_sync["audio"]["duration_ms"] - timeline.get("duration_ms", -9999)) <= 25
            ):
                checks_passed.append("audio_start_end_sync")
            else:
                checks_failed.append("audio and video start/end are not synchronized")
            if -30.0 <= actual_audio["peak_dbfs"] <= -12.0:
                checks_passed.append("audio_peak_safe")
            else:
                checks_failed.append("audio peak is inaudible, too loud or may clip")
            if actual_audio == declared_audio:
                checks_passed.append("audio_probe_and_hashes")
            else:
                checks_failed.append("audio probe or hash metadata mismatch")
            with tempfile.TemporaryDirectory() as audio_temp:
                source = synthesize_sequence_audio(Path(audio_temp) / "expected.wav", timeline["total_frames"], expected_cues)
            if source == audio_metadata.get("source"):
                checks_passed.append("audio_source_deterministic")
            else:
                checks_failed.append("audio source synthesis hash mismatch")
        elif audio_streams == 0 and audio_metadata.get("enabled") in {False, None}:
            checks_passed.append("no_audio")
        else:
            checks_failed.append("silent sequence unexpectedly contains audio")
        visual_frames = [item["content_start_frame"] for item in bands]
        if is_v2:
            visual_frames.extend([0, TITLE_FRAMES - 1])
            for item in bands:
                visual_frames.extend(
                    item["card_start_frame"] + offset
                    for offset in (0, 5, 6, 11, 12, 17)
                )
        decoded_frames = _frames_rgb(media, visual_frames)
        badge_visuals_ok = True
        for item, label, accent in zip(bands, expected_labels, SEQUENCE_ACCENTS):
            actual_frame = decoded_frames[item["content_start_frame"]]
            sample_offset = (30 * 720 + 483) * 3
            sample = tuple(actual_frame[sample_offset + channel] for channel in range(3))
            expected_badge = _overlay_badge(bytes(BACKGROUND) * 720 * 720, label, accent)
            badge_error = _crop_mae(actual_frame, expected_badge, (476, 10, 710, 68))
            if _near(sample, accent) and badge_error <= 12.0:
                checks_passed.append(f"{item['band']}_visible_position_marker")
            else:
                badge_visuals_ok = False
                checks_failed.append(f"{item['band']}: visible position badge mismatch")
        if is_v2 and badge_visuals_ok:
            target_actual = decoded_frames[bands[2]["content_start_frame"]]
            hard_expected = _overlay_badge(bytes(BACKGROUND) * 720 * 720, "FINAL HARD", WHITE)
            target_expected = _overlay_badge(bytes(BACKGROUND) * 720 * 720, "FINAL TARGET", WHITE)
            hard_error = _crop_mae(target_actual, hard_expected, (476, 10, 710, 68))
            target_error = _crop_mae(target_actual, target_expected, (476, 10, 710, 68))
            if hard_error < target_error:
                checks_passed.append("audience_badges_easy_medium_hard")
            else:
                checks_failed.append("third problem badge does not visibly say FINAL HARD")
        if is_v2:
            expected_title = _title_card(plugin_type)
            title_start_error = _crop_mae(decoded_frames[0], expected_title, TITLE_SAFE_AREA)
            title_end_error = _crop_mae(decoded_frames[TITLE_FRAMES - 1], expected_title, TITLE_SAFE_AREA)
            title_marker_offset = (106 * 720 + 66) * 3
            title_marker = tuple(decoded_frames[0][title_marker_offset + channel] for channel in range(3))
            title_accent = SEQUENCE_ACCENTS[SEQUENCE_TYPES.index(plugin_type) % len(SEQUENCE_ACCENTS)]
            if title_start_error <= 12.0 and title_end_error <= 12.0 and _near(title_marker, title_accent):
                checks_passed.extend(["title_card_30_frames", "title_frame_zero_marker"])
            else:
                checks_failed.append("title card frame 0/29 pixels or marker mismatch")
            countdown_ok = True
            for item, label, accent in zip(bands, SEQUENCE_LABELS, SEQUENCE_ACCENTS):
                expected_cards = {count: _card(label, accent, count=count) for count in (3, 2, 1)}
                for count, offsets in ((3, (0, 5)), (2, (6, 11)), (1, (12, 17))):
                    for offset in offsets:
                        actual = decoded_frames[item["card_start_frame"] + offset]
                        errors = {
                            candidate: _crop_mae(actual, expected, (325, 238, 390, 320))
                            for candidate, expected in expected_cards.items()
                        }
                        if errors[count] > 12.0 or min(errors, key=errors.get) != count:
                            countdown_ok = False
            if countdown_ok:
                checks_passed.append("countdown_three_states_six_frames")
            else:
                checks_failed.append("countdown 3/2/1 visual hold mismatch")
        final_sample = _sample_rgb(media, timeline["final_start_frame"], 78, 300)
        if _near(final_sample, WHITE):
            checks_passed.append("final_completion_card")
        else:
            checks_failed.append("final completion card marker mismatch")
    except Exception as error:
        checks_failed.append(f"sequence media: {error}")
    report = {
        "schema_version": "1.0.0",
        "status": "passed" if not checks_failed else "failed",
        "checks_passed": sorted(set(checks_passed)),
        "checks_failed": checks_failed,
    }
    if strict and checks_failed:
        raise ValueError("sequence validation failed: " + "; ".join(checks_failed))
    return report


def _manifest_entry(result: SequenceResult, output: Path) -> dict:
    metadata = read_json(result.sequence / "sequence.json")
    validation = read_json(result.sequence / "validation.json")
    audio = metadata["audio"]
    encoded = audio.get("encoded") or {}
    return {
        "plugin": result.sequence.parent.name,
        "path": str(result.sequence.relative_to(output)),
        "sequence_seed": metadata["provenance"]["master_seed"],
        "presentation_preset": metadata["presentation"].get("preset"),
        "timeline_preset": metadata["timeline"].get("preset"),
        "duration_ms": metadata["artifact"]["duration_ms"],
        "frames": metadata["artifact"]["frames"],
        "bytes": (result.sequence / "sequence.mp4").stat().st_size,
        "mp4_sha256": sha256_file(result.sequence / "sequence.mp4"),
        "audio": {
            "enabled": audio["enabled"],
            "preset": audio["preset"],
            "codec": encoded.get("codec"),
            "sample_rate": encoded.get("sample_rate"),
            "channels": encoded.get("channels", 0),
            "duration_ms": encoded.get("duration_ms"),
            "peak_dbfs": encoded.get("peak_dbfs"),
            "stream_sha256": encoded.get("stream_sha256"),
            "decoded_pcm_sha256": encoded.get("decoded_pcm_sha256"),
        },
        "validation_status": validation["status"],
    }


def generate_representatives(
    collection_seed: int, output: Path, max_candidates: int | None = None,
    audio_enabled: bool = False,
) -> tuple[SequenceResult, ...]:
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for puzzle_type in SEQUENCE_TYPES:
        results.append(generate_sequence(SequenceRequest(
            puzzle_type, representative_seed(collection_seed, puzzle_type), output, max_candidates, audio_enabled,
        )))
    manifest = {
        "schema_version": "1.1.0",
        "kind": "seven-plugin-three-band-representative-sequences",
        "collection_seed": collection_seed,
        "presentation_preset": PRESENTATION_PRESET,
        "timeline_preset": TIMELINE_PRESET,
        "audio_enabled": audio_enabled,
        "audio_preset": AUDIO_PRESET if audio_enabled else None,
        "sequences": [_manifest_entry(result, output) for result in results],
    }
    write_json(output / "manifest.json", manifest)
    return tuple(results)
