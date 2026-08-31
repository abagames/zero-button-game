import tempfile
import unittest
from pathlib import Path

from zero_button_game.cli import build_parser
from zero_button_game.core import read_json, sha256_file
from zero_button_game.render import ACTOR, FONT, GOAL, WHITE
from zero_button_game.sequence import (
    AUDIO_PRESET, CARD_FRAMES, COUNTDOWN_STATE_FRAMES, PRESENTATION_PRESET, SEQUENCE_BANDS,
    SEQUENCE_LABELS, SEQUENCE_TYPES, TIMELINE_PRESET, TITLE_FRAMES, TITLE_PROGRESSION,
    TITLE_SECONDARY, TITLE_SPECS, TITLE_SAFE_AREA, TICK_FRAME_OFFSETS, TRANSITION_FRAME_OFFSET,
    SequenceRequest, _band_seed, _card, _overlay_badge, _text_width, _title_card,
    generate_sequence, representative_seed, synthesize_sequence_audio, validate_sequence,
)


class SequenceUnitTests(unittest.TestCase):
    def test_cli_accepts_every_plugin_through_one_interface(self):
        parser = build_parser()
        for puzzle_type in SEQUENCE_TYPES:
            args = parser.parse_args([
                "generate-sequence", "--type", puzzle_type, "--seed", "42", "--output", "out",
            ])
            self.assertEqual(args.type, puzzle_type)
            self.assertEqual(args.audio, "off")
            enabled = parser.parse_args([
                "generate-sequence", "--type", puzzle_type, "--seed", "42", "--output", "out", "--audio", "on",
            ])
            self.assertEqual(enabled.audio, "on")

    def test_band_seed_derivation_is_stable_and_distinct(self):
        first = [_band_seed(42, "maze", index, band) for index, band in enumerate(SEQUENCE_BANDS)]
        second = [_band_seed(42, "maze", index, band) for index, band in enumerate(SEQUENCE_BANDS)]
        self.assertEqual(first, second)
        self.assertEqual(len(set(first)), 3)
        self.assertTrue(all(0 <= seed < 2**64 for seed in first))
        self.assertNotEqual(representative_seed(42, "maze"), representative_seed(42, "pipes"))

    def test_badge_and_cards_have_machine_readable_markers(self):
        background = bytes((16, 21, 29)) * 720 * 720
        for label, accent in zip(SEQUENCE_LABELS, (ACTOR, GOAL, WHITE)):
            frame = _overlay_badge(background, label, accent)
            offset = (30 * 720 + 483) * 3
            self.assertEqual(tuple(frame[offset:offset + 3]), accent)
            self.assertNotEqual(_card(label, accent), background)
        final = _card("", WHITE, final=True)
        offset = (300 * 720 + 78) * 3
        self.assertEqual(tuple(final[offset:offset + 3]), WHITE)
        self.assertNotEqual(_card("1/3 EASY", ACTOR, count=3), _card("1/3 EASY", ACTOR, count=2))
        self.assertEqual(SEQUENCE_LABELS[-1], "FINAL HARD")

    def test_title_copy_is_canonical_font_safe_and_inside_safe_area(self):
        safe_width = TITLE_SAFE_AREA[2] - TITLE_SAFE_AREA[0]
        self.assertEqual(TITLE_FRAMES, 30)
        self.assertEqual(CARD_FRAMES, 18)
        self.assertEqual(COUNTDOWN_STATE_FRAMES, 6)
        self.assertEqual(TICK_FRAME_OFFSETS, (0, 6, 12))
        supported = set(FONT)
        for puzzle_type, spec in TITLE_SPECS.items():
            lines = ((spec["name"], 8), (TITLE_SECONDARY, 5), (spec["rule"], 3), (TITLE_PROGRESSION, 3))
            self.assertTrue(all(set(text) <= supported for text, _ in lines), puzzle_type)
            self.assertTrue(all(_text_width(text, scale) <= safe_width for text, scale in lines), puzzle_type)
            title = _title_card(puzzle_type)
            self.assertEqual(len(title), 720 * 720 * 3)
            marker = (106 * 720 + 66) * 3
            self.assertNotEqual(tuple(title[marker:marker + 3]), (16, 21, 29))

    def test_four_audio_layers_synthesize_deterministically_without_clipping(self):
        cues = [
            {"cue_type": "transition_low", "layer": "problem_transition", "sample_offset": 0, "duration_ms": 35, "sound_profile": "low-transition-180hz"},
            {"cue_type": "count_tick", "layer": "countdown", "sample_offset": 2400, "duration_ms": 32, "frequency_hz": 880},
            {"cue_type": "action", "layer": "operation", "sample_offset": 4800, "duration_ms": 45, "sound_profile": "quarter-turn-mechanical"},
            {"cue_type": "goal_chime", "layer": "completion", "sample_offset": 9600, "duration_ms": 170, "sound_profile": "shared-rising-two-note"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            first = synthesize_sequence_audio(Path(directory) / "first.wav", 20, cues)
            second = synthesize_sequence_audio(Path(directory) / "second.wav", 20, cues)
            self.assertEqual(first, second)
            self.assertEqual((Path(directory) / "first.wav").read_bytes(), (Path(directory) / "second.wav").read_bytes())
            self.assertLessEqual(first["source_peak_dbfs"], -12.0)


class SequenceIntegrationTests(unittest.TestCase):
    def test_maze_sequence_is_strict_and_byte_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            roots = (Path(directory) / "first", Path(directory) / "second")
            results = [
                generate_sequence(SequenceRequest("maze", 20260827, root, max_candidates=300))
                for root in roots
            ]
            for result in results:
                report = validate_sequence(result.sequence, strict=True)
                self.assertEqual(report["status"], "passed")
                metadata = read_json(result.sequence / "sequence.json")
                self.assertEqual([item["band"] for item in metadata["bands"]], list(SEQUENCE_BANDS))
                self.assertEqual(metadata["artifact"]["codec"], "h264")
                self.assertEqual(metadata["artifact"]["pixel_format"], "yuv420p")
                self.assertEqual(metadata["artifact"]["audio_streams"], 0)
                self.assertEqual(metadata["presentation"]["preset"], PRESENTATION_PRESET)
                self.assertEqual(metadata["timeline"]["preset"], TIMELINE_PRESET)
                self.assertEqual(metadata["timeline"]["title_frames"], 30)
                self.assertEqual(metadata["timeline"]["card_frames_each"], 18)
                self.assertEqual([item["audience_label"] for item in metadata["bands"]], ["EASY", "MEDIUM", "HARD"])
                self.assertEqual(metadata["bands"][2]["position_label"], "FINAL HARD")
                for check in (
                    "presentation_v2_metadata", "audience_target_to_hard_mapping",
                    "audience_badges_easy_medium_hard", "title_card_30_frames",
                    "title_frame_zero_marker", "countdown_three_states_six_frames",
                ):
                    self.assertIn(check, report["checks_passed"])
            self.assertEqual(
                (results[0].sequence / "sequence.json").read_bytes(),
                (results[1].sequence / "sequence.json").read_bytes(),
            )
            self.assertEqual(
                sha256_file(results[0].sequence / "sequence.mp4"),
                sha256_file(results[1].sequence / "sequence.mp4"),
            )

    def test_maze_audio_sequence_is_strict_and_byte_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            roots = (Path(directory) / "first", Path(directory) / "second")
            results = [
                generate_sequence(SequenceRequest("maze", 20260827, root, max_candidates=300, audio_enabled=True))
                for root in roots
            ]
            for result in results:
                report = validate_sequence(result.sequence, strict=True)
                self.assertEqual(report["status"], "passed")
                for check in (
                    "audio_aac_48khz_stereo", "audio_action_mapping", "audio_cue_timeline",
                    "audio_duration_sync", "audio_four_layers", "audio_peak_safe",
                    "audio_probe_and_hashes", "audio_source_deterministic",
                    "audio_start_end_sync", "audio_three_tick_visual_sync",
                ):
                    self.assertIn(check, report["checks_passed"])
                metadata = read_json(result.sequence / "sequence.json")
                self.assertTrue(metadata["audio"]["enabled"])
                self.assertEqual(metadata["audio"]["preset"], AUDIO_PRESET)
                self.assertEqual(metadata["artifact"]["audio_streams"], 1)
                self.assertEqual(metadata["audio"]["encoded"]["codec"], "aac")
                self.assertEqual(metadata["audio"]["encoded"]["sample_rate"], 48000)
                for item in metadata["bands"]:
                    ticks = [cue for cue in metadata["audio"]["cues"] if cue["ordinal"] == item["ordinal"] and cue["cue_type"] == "count_tick"]
                    self.assertEqual([cue["frame"] - item["card_start_frame"] for cue in ticks], [0, 6, 12])
                    if item["ordinal"] > 1:
                        transition = next(cue for cue in metadata["audio"]["cues"] if cue["ordinal"] == item["ordinal"] and cue["cue_type"] == "transition_low")
                        self.assertEqual(transition["frame"] - item["card_start_frame"], TRANSITION_FRAME_OFFSET)
            self.assertEqual(
                (results[0].sequence / "sequence.json").read_bytes(),
                (results[1].sequence / "sequence.json").read_bytes(),
            )
            self.assertEqual(
                sha256_file(results[0].sequence / "sequence.mp4"),
                sha256_file(results[1].sequence / "sequence.mp4"),
            )


if __name__ == "__main__":
    unittest.main()
