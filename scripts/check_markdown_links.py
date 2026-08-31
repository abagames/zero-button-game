#!/usr/bin/env python3
"""Check repository-local Markdown links with the Python standard library."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


INLINE_LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
REFERENCE_LINK = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)")
INLINE_CODE = re.compile(r"`+[^`]*`+")
FENCE = re.compile(r"^\s*(```|~~~)")
IGNORED_DIRECTORIES = {".git", ".pytest_cache", "__pycache__"}
RETIRED_ROOT_TARGETS = {
    "PUZZLE_GIF_ENVIRONMENT_PLAN.md",
    "THREE_PUZZLE_SEQUENCE_PROPOSAL.md",
}


def markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if not any(part in IGNORED_DIRECTORIES for part in path.relative_to(root).parts)
    )


def destinations(path: Path) -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []
    fenced = False
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        visible = INLINE_CODE.sub("", line)
        for match in INLINE_LINK.finditer(visible):
            results.append((line_number, match.group(1).strip()))
        reference = REFERENCE_LINK.match(visible)
        if reference:
            results.append((line_number, reference.group(1).strip()))
    return results


def destination_path(raw: str) -> str:
    if raw.startswith("<") and ">" in raw:
        return raw[1:raw.index(">")]
    return raw.split(maxsplit=1)[0]


def check(root: Path) -> dict[str, object]:
    root = root.resolve()
    missing: list[dict[str, object]] = []
    retired: list[dict[str, object]] = []
    checked = 0
    files = markdown_files(root)
    retired_paths = {(root / name).resolve() for name in RETIRED_ROOT_TARGETS}

    for source in files:
        for line, raw in destinations(source):
            value = destination_path(raw)
            if not value or value.startswith("#"):
                continue
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc:
                continue
            local = unquote(parsed.path)
            if not local:
                continue
            target = Path(local)
            resolved = (target if target.is_absolute() else source.parent / target).resolve()
            checked += 1
            item = {
                "source": source.relative_to(root).as_posix(),
                "line": line,
                "target": value,
            }
            if not resolved.exists():
                missing.append(item)
            if resolved in retired_paths:
                retired.append(item)

    return {
        "status": "passed" if not missing and not retired else "failed",
        "markdown_files": len(files),
        "local_links_checked": checked,
        "missing_local_targets": missing,
        "retired_root_links": retired,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    report = check(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
