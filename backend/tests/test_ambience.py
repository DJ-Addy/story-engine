"""Tests for the rule-based scene ambience tagger."""

import pytest

from app.nlp.ambience import ambience_tags


class TestLocationMappings:
    @pytest.mark.parametrize("location", ["HARBOR", "THE OLD DOCK", "PORT ROYAL"])
    def test_waterfront(self, location: str) -> None:
        tags = ambience_tags(location, None, None)
        for expected in ["water_lapping", "gulls", "rope_creak"]:
            assert expected in tags

    @pytest.mark.parametrize("location", ["TAVERN", "DIVE BAR", "THE KINGS PUB"])
    def test_tavern(self, location: str) -> None:
        tags = ambience_tags(location, None, None)
        for expected in ["crowd_murmur", "glassware"]:
            assert expected in tags

    @pytest.mark.parametrize("location", ["FOREST", "DEEP WOODS"])
    def test_forest(self, location: str) -> None:
        tags = ambience_tags(location, None, None)
        for expected in ["wind_trees", "birds"]:
            assert expected in tags

    @pytest.mark.parametrize("location", ["MAIN STREET", "CITY CENTER"])
    def test_street(self, location: str) -> None:
        tags = ambience_tags(location, None, None)
        for expected in ["traffic", "city_hum"]:
            assert expected in tags

    @pytest.mark.parametrize("location", ["LIGHTHOUSE", "CLIFF EDGE"])
    def test_lighthouse(self, location: str) -> None:
        tags = ambience_tags(location, None, None)
        for expected in ["wind", "waves_distant"]:
            assert expected in tags

    def test_lowercase_location_matches(self) -> None:
        assert "water_lapping" in ambience_tags("harbor", None, None)


class TestInteriorExterior:
    def test_interior_adds_room_tone(self) -> None:
        assert "room_tone" in ambience_tags("TAVERN", None, True)

    def test_exterior_adds_outdoor_air(self) -> None:
        tags = ambience_tags("FOREST", None, False)
        assert "outdoor_air" in tags
        assert "room_tone" not in tags

    def test_unknown_interior_adds_neither(self) -> None:
        tags = ambience_tags("FOREST", None, None)
        assert "room_tone" not in tags
        assert "outdoor_air" not in tags


class TestTimeOfDay:
    def test_night_exterior_adds_crickets(self) -> None:
        assert "night_crickets" in ambience_tags("FOREST", "NIGHT", False)

    def test_night_interior_no_crickets(self) -> None:
        assert "night_crickets" not in ambience_tags("TAVERN", "NIGHT", True)

    def test_night_unknown_interior_no_crickets(self) -> None:
        assert "night_crickets" not in ambience_tags("FOREST", "NIGHT", None)

    def test_day_exterior_adds_daytime_ambience(self) -> None:
        assert "daytime_ambience" in ambience_tags("FOREST", "DAY", False)

    def test_day_interior_no_daytime_ambience(self) -> None:
        assert "daytime_ambience" not in ambience_tags("TAVERN", "DAY", True)


class TestWeather:
    @pytest.mark.parametrize("weather", ["RAIN", "STORM", "THUNDERSTORM", "rain"])
    def test_weather_keywords(self, weather: str) -> None:
        tags = ambience_tags("STREET", None, None, weather=weather)
        assert "rain" in tags
        assert "thunder_distant" in tags

    def test_action_text_storm(self) -> None:
        tags = ambience_tags(
            "FOREST", None, None, action_text="A storm rolls in from the west."
        )
        assert "rain" in tags
        assert "thunder_distant" in tags

    def test_clear_weather_adds_nothing(self) -> None:
        tags = ambience_tags("FOREST", None, None, weather="CLEAR")
        assert "rain" not in tags


class TestCombination:
    def test_ext_harbor_night_with_storm(self) -> None:
        tags = ambience_tags(
            "HARBOR",
            "NIGHT",
            False,
            action_text="A storm batters the pier as thunder cracks.",
        )
        assert tags == sorted(
            {
                "water_lapping",
                "gulls",
                "rope_creak",
                "outdoor_air",
                "night_crickets",
                "rain",
                "thunder_distant",
            }
        )


class TestFallbackAndShape:
    def test_empty_inputs_fallback_to_room_tone(self) -> None:
        assert ambience_tags(None, None, None) == ["room_tone"]

    def test_unmatched_location_falls_back(self) -> None:
        assert ambience_tags("MOON BASE", None, None) == ["room_tone"]

    def test_output_sorted_and_deduped(self) -> None:
        # LIGHTHOUSE and CLIFF both map to the same tags; result must dedupe.
        tags = ambience_tags("LIGHTHOUSE CLIFF", "DAY", False)
        assert tags == sorted(set(tags))
        assert len(tags) == len(set(tags))
