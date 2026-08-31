from __future__ import annotations

import unittest
from dataclasses import dataclass

from zero_button_game.validation import pre_reveal_neutrality_failure


TIMELINE = {"appearance": 6, "thinking": 44, "reveal_start": 50, "solve": 40, "solve_end": 90, "total": 120}


@dataclass(frozen=True)
class FakeScene:
    tag: bytes


class FakePlugin:
    @staticmethod
    def alternate_scene(scene: FakeScene) -> FakeScene:
        return FakeScene(b"alternate")


class FakeRenderer:
    """Neutral before reveal_start, solution dependent from reveal_start on."""

    def __init__(self, leak_frames: frozenset[int] = frozenset(), leak_from_reveal: bool = True) -> None:
        self.leak_frames = leak_frames
        self.leak_from_reveal = leak_from_reveal

    def render_frame(self, scene: FakeScene, frame: int) -> bytes:
        leaks = frame in self.leak_frames or (self.leak_from_reveal and frame >= TIMELINE["reveal_start"])
        return f"{frame}".encode() + (scene.tag if leaks else b"")


class NeutralityOracleTest(unittest.TestCase):
    def test_clean_renderer_passes(self):
        self.assertIsNone(pre_reveal_neutrality_failure(FakePlugin, FakeRenderer(), FakeScene(b"scene"), TIMELINE))

    def test_every_pre_reveal_index_is_checked(self):
        contact_sheet_indices = [
            0,
            TIMELINE["appearance"],
            TIMELINE["appearance"] + TIMELINE["thinking"] // 2,
            TIMELINE["reveal_start"] - 1,
        ]
        for frame in list(range(TIMELINE["reveal_start"])):
            with self.subTest(frame=frame):
                failure = pre_reveal_neutrality_failure(
                    FakePlugin, FakeRenderer(leak_frames=frozenset({frame})), FakeScene(b"scene"), TIMELINE
                )
                self.assertIsNotNone(failure)
                self.assertIn(f"frame {frame}", failure)
        for frame in contact_sheet_indices:
            self.assertIn(frame, range(TIMELINE["reveal_start"]))

    def test_late_reveal_boundary_is_detected(self):
        failure = pre_reveal_neutrality_failure(
            FakePlugin, FakeRenderer(leak_from_reveal=False), FakeScene(b"scene"), TIMELINE
        )
        self.assertIsNotNone(failure)
        self.assertIn("reveal boundary is late", failure)


if __name__ == "__main__":
    unittest.main()
