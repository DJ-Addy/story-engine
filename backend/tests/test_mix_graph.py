"""Tests for ffmpeg command assembly: scene mix filter graph and ambience looping."""

import pytest

from app.render.audio.mix import (
    build_loop_extend_command,
    build_scene_mix_command,
    extra_loop_count,
)


def filter_graph_of(argv: list[str]) -> str:
    return argv[argv.index("-filter_complex") + 1]


def test_standard_preset_sidechain_parameters() -> None:
    argv = build_scene_mix_command("speech.wav", "amb.wav", "out.wav")
    graph = filter_graph_of(argv)
    assert (
        "sidechaincompress=threshold=0.035:ratio=6:attack=12:release=380:makeup=1" in graph
    )


def test_subtle_preset_sidechain_parameters() -> None:
    argv = build_scene_mix_command("speech.wav", "amb.wav", "out.wav", preset="subtle")
    graph = filter_graph_of(argv)
    assert (
        "sidechaincompress=threshold=0.035:ratio=3:attack=12:release=500:makeup=1" in graph
    )


def test_cinematic_preset_sidechain_parameters() -> None:
    argv = build_scene_mix_command("speech.wav", "amb.wav", "out.wav", preset="cinematic")
    graph = filter_graph_of(argv)
    assert (
        "sidechaincompress=threshold=0.035:ratio=8:attack=12:release=300:makeup=1" in graph
    )


def test_filter_graph_structure_matches_prd() -> None:
    argv = build_scene_mix_command("speech.wav", "amb.wav", "out.wav")
    graph = filter_graph_of(argv)
    assert graph.startswith("[1:a]volume=0.5[amb];")
    assert "[amb][0:a]sidechaincompress=" in graph
    assert "[0:a][ducked]amix=inputs=2:duration=first:normalize=0[mixed]" in graph
    assert graph.endswith("[mixed]loudnorm=I=-18:TP=-1.5:LRA=9[out]")


def test_loudnorm_targets_present() -> None:
    argv = build_scene_mix_command("speech.wav", "amb.wav", "out.wav")
    graph = filter_graph_of(argv)
    assert "loudnorm=I=-18:TP=-1.5:LRA=9" in graph


def test_scene_mix_io_mapping_and_codec() -> None:
    argv = build_scene_mix_command("speech.wav", "amb.wav", "out.wav")
    assert argv[0] == "ffmpeg"
    # Speech is input 0, ambience is input 1.
    i_positions = [i for i, a in enumerate(argv) if a == "-i"]
    assert argv[i_positions[0] + 1] == "speech.wav"
    assert argv[i_positions[1] + 1] == "amb.wav"
    assert argv[argv.index("-map") + 1] == "[out]"
    assert argv[argv.index("-c:a") + 1] == "pcm_s24le"
    assert argv[-1] == "out.wav"


@pytest.mark.parametrize(
    ("target_ms", "expected_extra_loops"),
    [
        (30_000, 0),  # exactly one bed length: no extra loops
        (30_001, 1),  # just over: one extra loop
        (95_000, 3),  # 95 s on a 30 s bed: ceil(95/30) - 1 = 3
        (60_000, 1),
        (15_000, 0),
        (0, 0),
    ],
)
def test_extra_loop_count(target_ms: int, expected_extra_loops: int) -> None:
    assert extra_loop_count(target_ms) == expected_extra_loops


def test_loop_extend_command_argv() -> None:
    argv = build_loop_extend_command("bed.wav", 95_000, "extended.wav")
    assert argv[0] == "ffmpeg"
    assert argv[argv.index("-stream_loop") + 1] == "3"
    assert argv[argv.index("-i") + 1] == "bed.wav"
    assert argv[argv.index("-t") + 1] == "95.000"
    assert argv[-1] == "extended.wav"


def test_loop_extend_command_exact_bed_length_needs_no_extra_loop() -> None:
    argv = build_loop_extend_command("bed.wav", 30_000, "extended.wav")
    assert argv[argv.index("-stream_loop") + 1] == "0"
    assert argv[argv.index("-t") + 1] == "30.000"
