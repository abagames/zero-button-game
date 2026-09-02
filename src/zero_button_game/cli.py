from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit_quality import QualityAuditRequest, audit_quality
from .core import read_json, write_json
from .export import export_keyframes_and_contact, parse_formats
from .pipeline import GenerationExhausted, GenerationRequest, generate
from .sequence import (
    SEQUENCE_TYPES, SequenceRequest, generate_representatives, generate_sequence, validate_sequence,
)
from .validation import validate_instance
from .registry import registered_puzzle_types


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _uint64(value: str) -> int:
    parsed = int(value)
    if not 0 <= parsed < 2**64:
        raise argparse.ArgumentTypeError("must be an unsigned 64-bit integer")
    return parsed


def _format_argument(value: str) -> tuple[str, ...]:
    """argparse ``type`` for ``--format``.

    A plain ``choices`` list cannot express a comma-separated value cleanly, so
    the value is parsed here and any unknown name becomes a normal argparse
    usage error (exit code 2).
    """
    try:
        return parse_formats(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _generation_parser(subparsers, name: str):
    parser = subparsers.add_parser(name, help=f"{name} deterministic puzzle works")
    parser.add_argument("--type", default="maze", choices=registered_puzzle_types())
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument(
        "--difficulty", default="medium",
        choices=["easy", "medium", "target"],
    )
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-candidates", type=int)
    parser.add_argument("--theme", default="minimal-v1", choices=["minimal-v1"])
    parser.add_argument("--timeline", default="standard", choices=["standard"])
    parser.add_argument(
        "--thinking-time", type=float,
        help="thinking seconds from frame zero to reveal_start (2.5-20.0, on the 20fps grid; accepted for every plugin, recorded as a non-standard comparison condition unless it matches a calibrated standard)",
    )
    parser.add_argument("--timing-variant", help="label stored in timing_calibration metadata")
    parser.add_argument(
        "--format", dest="formats", default="gif,mp4", type=_format_argument,
        metavar="{gif,mp4,\"gif,mp4\"}",
        help="delivery formats to encode: gif, mp4, or gif,mp4 (default: gif,mp4)",
    )
    parser.add_argument("--keep-frames", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m zero_button_game")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _generation_parser(subparsers, "generate")
    _generation_parser(subparsers, "batch")
    audit = subparsers.add_parser("audit-quality", help="audit deterministic candidate quality without media or files")
    audit.add_argument("--type", required=True, choices=registered_puzzle_types())
    audit.add_argument("--difficulty", default="medium", choices=("easy", "medium", "target"))
    audit.add_argument("--seed", type=_uint64, required=True)
    audit.add_argument("--candidates", type=_positive_int, default=20)
    validate = subparsers.add_parser("validate", help="validate an instance or run directory")
    validate.add_argument("path", type=Path)
    validate.add_argument("--strict", action="store_true")
    contact = subparsers.add_parser("contact-sheet", help="report an existing contact sheet")
    contact.add_argument("instance", type=Path)
    sequence = subparsers.add_parser("generate-sequence", help="generate an Easy -> Medium -> Target MP4")
    sequence.add_argument("--type", required=True, choices=SEQUENCE_TYPES)
    sequence.add_argument("--seed", type=int, required=True)
    sequence.add_argument("--output", type=Path, required=True)
    sequence.add_argument("--max-candidates", type=int)
    sequence.add_argument("--audio", choices=("off", "on"), default="off", help="add deterministic four-layer cues (default: off)")
    representatives = subparsers.add_parser("generate-representatives", help="generate the seven flagship three-puzzle MP4s")
    representatives.add_argument("--seed", type=int, required=True)
    representatives.add_argument("--output", type=Path, required=True)
    representatives.add_argument("--max-candidates", type=int)
    representatives.add_argument("--audio", choices=("off", "on"), default="off", help="add deterministic four-layer cues (default: off)")
    validate_sequence_parser = subparsers.add_parser("validate-sequence", help="strictly validate a sequence or collection")
    validate_sequence_parser.add_argument("path", type=Path)
    return parser


def _find_instances(path: Path) -> list[Path]:
    if (path / "problem.json").exists():
        return [path]
    return sorted(item.parent for item in path.rglob("problem.json") if not any(part.startswith(".") for part in item.parts))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "audit-quality":
            report = audit_quality(QualityAuditRequest(
                puzzle_type=args.type,
                difficulty_band=args.difficulty,
                master_seed=args.seed,
                candidate_count=args.candidates,
            ))
            print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.command in {"generate", "batch"}:
            result = generate(GenerationRequest(
                puzzle_type=args.type, count=args.count, difficulty_band=args.difficulty,
                master_seed=args.seed, output=args.output, max_candidates=args.max_candidates,
                keep_frames=args.keep_frames, resume=args.resume, force=args.force,
                thinking_time_seconds=args.thinking_time, timing_variant=args.timing_variant,
                formats=args.formats,
            ))
            print(json.dumps({"status": "passed", "instances": [str(path) for path in result.instances], "candidates": result.candidate_count, "rejections": result.rejection_count}, ensure_ascii=False))
            return 0
        if args.command == "validate":
            instances = _find_instances(args.path)
            if not instances:
                print(json.dumps({"status": "failed", "error": "no instances found"}))
                return 6
            reports = []
            for instance in instances:
                report = validate_instance(instance, strict=args.strict)
                write_json(instance / "validation.json", report)
                reports.append({"instance": str(instance), "status": report["status"], "checks": len(report["checks_passed"])})
            failed = any(item["status"] != "passed" for item in reports)
            print(json.dumps({"status": "failed" if failed else "passed", "instances": reports}, ensure_ascii=False))
            return 6 if failed else 0
        if args.command == "contact-sheet":
            path = args.instance / "contact_sheet.png"
            if not path.exists():
                print(json.dumps({"status": "failed", "error": f"missing {path}"}))
                return 6
            print(json.dumps({"status": "passed", "contact_sheet": str(path)}))
            return 0
        if args.command == "generate-sequence":
            result = generate_sequence(SequenceRequest(args.type, args.seed, args.output, args.max_candidates, args.audio == "on"))
            print(json.dumps({"status": "passed", "sequence": str(result.sequence), "instances": [str(path) for path in result.instances]}, ensure_ascii=False))
            return 0
        if args.command == "generate-representatives":
            results = generate_representatives(args.seed, args.output, args.max_candidates, args.audio == "on")
            print(json.dumps({"status": "passed", "sequences": [str(result.sequence) for result in results], "manifest": str(args.output / "manifest.json")}, ensure_ascii=False))
            return 0
        if args.command == "validate-sequence":
            sequences = [args.path] if (args.path / "sequence.json").exists() else sorted(path.parent for path in args.path.rglob("sequence.json"))
            if not sequences:
                raise ValueError("no sequences found")
            reports = []
            for path in sequences:
                report = validate_sequence(path, strict=True)
                write_json(path / "validation.json", report)
                reports.append({"sequence": str(path), "status": report["status"], "checks": len(report["checks_passed"])})
            print(json.dumps({"status": "passed", "sequences": reports}, ensure_ascii=False))
            return 0
    except GenerationExhausted as error:
        print(json.dumps({"status": "failed", "category": "generation_exhausted", "error": str(error)}), file=sys.stderr)
        return 3
    except FileExistsError as error:
        print(json.dumps({"status": "failed", "category": "output_conflict", "error": str(error)}), file=sys.stderr)
        return 7
    except ValueError as error:
        print(json.dumps({"status": "failed", "category": "validation_or_config", "error": str(error)}), file=sys.stderr)
        return 6 if args.command in {"validate", "validate-sequence"} else 2
    except Exception as error:
        print(json.dumps({"status": "failed", "category": "render_export", "error": str(error)}), file=sys.stderr)
        return 5
    return 2
