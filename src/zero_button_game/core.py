from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def derive_seed(master_seed: int, puzzle_type: str, candidate_index: int, purpose: str, algorithm_version: int = 1) -> int:
    if not 0 <= master_seed < 2**64:
        raise ValueError("master_seed must be an unsigned 64-bit integer")
    material = f"{master_seed}|{puzzle_type}|{candidate_index}|{purpose}|{algorithm_version}".encode("ascii")
    return int.from_bytes(hashlib.sha256(material).digest()[:16], "big")


class StableRng:
    """Small deterministic RNG with an explicit, stable algorithm."""

    def __init__(self, seed: int):
        self.state = seed & ((1 << 64) - 1)

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & ((1 << 64) - 1)
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & ((1 << 64) - 1)
        return z ^ (z >> 31)

    def randbelow(self, stop: int) -> int:
        if stop <= 0:
            raise ValueError("stop must be positive")
        limit = (1 << 64) - ((1 << 64) % stop)
        while True:
            value = self.next_u64()
            if value < limit:
                return value % stop

    def shuffle(self, values: list[Any]) -> None:
        for i in range(len(values) - 1, 0, -1):
            j = self.randbelow(i + 1)
            values[i], values[j] = values[j], values[i]
