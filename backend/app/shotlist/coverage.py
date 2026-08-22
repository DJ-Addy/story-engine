"""Post-hoc dialogue-coverage checking for shot lists.

Per the PRD, when coverage gaps are found we regenerate ONLY the gap, never
the whole scene — so gaps are reported as inclusive ordinal ranges suitable
for targeted regeneration prompts.
"""

from app.shotlist.schema import SceneShotList


def uncovered_lines(shot_list: SceneShotList, dialogue_line_ordinals: list[int]) -> list[int]:
    """Return dialogue line ordinals not covered by any shot, sorted ascending."""
    covered: set[int] = set()
    for shot in shot_list.shots:
        covered.update(shot.covers_lines)
    return sorted(line for line in set(dialogue_line_ordinals) if line not in covered)


def coverage_gaps(
    shot_list: SceneShotList, dialogue_line_ordinals: list[int]
) -> list[tuple[int, int]]:
    """Group uncovered line ordinals into inclusive (start, end) ranges.

    Example: uncovered [3, 4, 5, 9] -> [(3, 5), (9, 9)].
    """
    missing = uncovered_lines(shot_list, dialogue_line_ordinals)
    gaps: list[tuple[int, int]] = []
    for line in missing:
        if gaps and line == gaps[-1][1] + 1:
            gaps[-1] = (gaps[-1][0], line)
        else:
            gaps.append((line, line))
    return gaps
