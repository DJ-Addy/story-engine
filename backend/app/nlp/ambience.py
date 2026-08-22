"""Rule-based scene ambience tagger.

Maps scene metadata (location, time of day, interior flag, weather, action
text) to ambience tags that feed the audio bed generation. The keyword table
is a module-level dict so mappings stay data, not code.
"""

from __future__ import annotations

# Substring keyword (uppercase) -> ambience tags for scene locations.
LOCATION_KEYWORD_TAGS: dict[str, list[str]] = {
    "HARBOR": ["water_lapping", "gulls", "rope_creak"],
    "DOCK": ["water_lapping", "gulls", "rope_creak"],
    "PORT": ["water_lapping", "gulls", "rope_creak"],
    "TAVERN": ["crowd_murmur", "glassware"],
    "BAR": ["crowd_murmur", "glassware"],
    "PUB": ["crowd_murmur", "glassware"],
    "FOREST": ["wind_trees", "birds"],
    "WOODS": ["wind_trees", "birds"],
    "STREET": ["traffic", "city_hum"],
    "CITY": ["traffic", "city_hum"],
    "LIGHTHOUSE": ["wind", "waves_distant"],
    "CLIFF": ["wind", "waves_distant"],
}

# Substring keyword (uppercase) -> tags, matched against weather + action text.
WEATHER_KEYWORD_TAGS: dict[str, list[str]] = {
    "RAIN": ["rain", "thunder_distant"],
    "STORM": ["rain", "thunder_distant"],
    "THUNDER": ["rain", "thunder_distant"],
}

_FALLBACK_TAGS = ["room_tone"]


def ambience_tags(
    location: str | None,
    time_of_day: str | None,
    interior: bool | None,
    weather: str | None = None,
    action_text: str = "",
) -> list[str]:
    """Return sorted, deduped ambience tags for a scene."""
    tags: set[str] = set()

    if location:
        upper_location = location.upper()
        for keyword, keyword_tags in LOCATION_KEYWORD_TAGS.items():
            if keyword in upper_location:
                tags.update(keyword_tags)

    if interior is True:
        tags.add("room_tone")
    elif interior is False:
        tags.add("outdoor_air")

    if time_of_day and interior is False:
        upper_time = time_of_day.upper()
        if "NIGHT" in upper_time:
            tags.add("night_crickets")
        elif "DAY" in upper_time:
            tags.add("daytime_ambience")

    conditions = f"{weather or ''} {action_text}".upper()
    for keyword, keyword_tags in WEATHER_KEYWORD_TAGS.items():
        if keyword in conditions:
            tags.update(keyword_tags)

    if not tags:
        return list(_FALLBACK_TAGS)
    return sorted(tags)
