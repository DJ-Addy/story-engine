"""Tests for animatic assembly (PRD §4.7): ffconcat manifest and ffmpeg argv."""

from app.render.animatic import (
    build_animatic_command,
    build_concat_manifest,
    default_shot_duration_ms,
)
from app.render.audio.durations import shot_duration_ms


class TestConcatManifest:
    def test_golden_three_boards_with_repeated_last_entry(self):
        boards = [
            ("boards/001.png", 1000),
            ("boards/002.png", 2500),
            ("boards/003.png", 750),
        ]
        assert build_concat_manifest(boards) == (
            "ffconcat version 1.0\n"
            "file 'boards/001.png'\n"
            "duration 1.000\n"
            "file 'boards/002.png'\n"
            "duration 2.500\n"
            "file 'boards/003.png'\n"
            "duration 0.750\n"
            "file 'boards/003.png'\n"
        )

    def test_durations_are_ms_to_seconds_three_decimals(self):
        manifest = build_concat_manifest([("a.png", 33)])
        assert "duration 0.033\n" in manifest

    def test_apostrophe_paths_escaped(self):
        # ffconcat single-quote escaping: close quote, escaped quote, reopen.
        manifest = build_concat_manifest([("it's here.png", 1000)])
        assert "file 'it'\\''s here.png'\n" in manifest
        # Repeated last line uses the same escaping.
        assert manifest.count("file 'it'\\''s here.png'\n") == 2


class TestAnimaticCommand:
    def test_exact_argv(self):
        assert build_animatic_command("scene.ffconcat", "scene_mix.wav", "animatic.mp4") == [
            "ffmpeg",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            "scene.ffconcat",
            "-i",
            "scene_mix.wav",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "24",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "animatic.mp4",
        ]


class TestDefaultShotDuration:
    def test_delegates_to_durations_module(self):
        texts = ["A short line.", "Another line with rather more words in it."]
        assert default_shot_duration_ms(texts) == shot_duration_ms(texts, boundary_gaps_ms=0)

    def test_minimum_floor_applies(self):
        # One tiny line: durations module floors each line at 800 ms.
        assert default_shot_duration_ms(["Hi."]) == 800
