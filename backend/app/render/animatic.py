"""Animatic assembly (PRD §4.7): ffconcat manifest + ffmpeg command, as data."""

from __future__ import annotations

from app.render.audio.durations import shot_duration_ms


def _escape_concat_path(path: str) -> str:
    """ffconcat single-quote escaping: close the quote, emit an escaped quote,
    reopen — i.e. ' becomes '\\''."""
    return path.replace("'", "'\\''")


def build_concat_manifest(boards: list[tuple[str, int]]) -> str:
    """ffconcat v1.0 manifest from (image_path, duration_ms) pairs.

    Per the ffmpeg concat-demuxer convention, the last file is repeated once
    more without a duration so the final frame is held for its full length.
    """
    out = ["ffconcat version 1.0\n"]
    for path, duration_ms in boards:
        out.append(f"file '{_escape_concat_path(path)}'\n")
        out.append(f"duration {duration_ms / 1000:.3f}\n")
    if boards:
        out.append(f"file '{_escape_concat_path(boards[-1][0])}'\n")
    return "".join(out)


def build_animatic_command(concat_path: str, audio_path: str, out_path: str) -> list[str]:
    """argv rendering the board manifest against the scene mix."""
    return [
        "ffmpeg",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-i",
        str(audio_path),
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
        str(out_path),
    ]


def default_shot_duration_ms(covered_line_texts: list[str]) -> int:
    """Pre-audio shot duration: the durations-module estimate with no gap
    budget (gaps are only known once the speech bus is planned)."""
    return shot_duration_ms(covered_line_texts, boundary_gaps_ms=0)
