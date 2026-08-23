"""Rule-based emotion classifier for screenplay parentheticals.

Screenwriters encode delivery in parentheticals under a character cue::

    MARA
    (whispering, afraid)
    They're still out there.

``emotion_from_parenthetical`` maps that wording to one label from a small
canonical vocabulary (``EMOTIONS``). The normalizer attaches the label to the
dialogue the parenthetical governs (``lines.emotion`` in the PRD schema), and
TTS adapters turn it into prosody.

The table is data, not code (mirrors ``app.nlp.ambience``). Only *emotional*
parentheticals resolve; stage directions with no delivery cue — ``(beat)``,
``(cont'd)``, ``(to Mara)``, ``(V.O.)`` — return ``None`` and the line stays
neutral.
"""

from __future__ import annotations

import re

# Canonical emotion vocabulary. TTS adapters map each of these to prosody;
# keep this set small and provider-agnostic.
EMOTIONS: frozenset[str] = frozenset(
    {
        "angry",
        "happy",
        "sad",
        "afraid",
        "excited",
        "calm",
        "whispering",
        "shouting",
        "urgent",
        "sarcastic",
        "surprised",
        "serious",
    }
)

# Canonical emotion -> the parenthetical words that signal it. Checked in this
# order, so more specific / audible cues (whispering, shouting) win over the
# general tone buckets when a parenthetical names both.
_EMOTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "whispering": ("whisper", "whispers", "whispering", "whispered", "hushed", "sotto", "murmuring", "under breath"),
    "shouting": ("shout", "shouts", "shouting", "yell", "yells", "yelling", "scream", "screams", "screaming", "bellowing", "roaring", "loudly"),
    "afraid": ("afraid", "fearful", "fearfully", "scared", "terrified", "frightened", "nervous", "nervously", "anxious", "anxiously", "trembling", "panicked"),
    "angry": ("angry", "angrily", "furious", "furiously", "enraged", "irate", "seething", "snarling", "sharply"),
    "sad": ("sad", "sadly", "sorrowful", "sorrowfully", "mournfully", "grieving", "tearful", "tearfully", "despondent", "forlorn", "dejected", "heartbroken"),
    "excited": ("excited", "excitedly", "eager", "eagerly", "thrilled", "enthusiastic", "enthusiastically", "giddy"),
    "surprised": ("surprised", "astonished", "amazed", "incredulous", "incredulously", "stunned", "shocked", "startled"),
    "sarcastic": ("sarcastic", "sarcastically", "dryly", "wryly", "mocking", "mockingly", "sardonic", "ironic", "ironically"),
    "urgent": ("urgent", "urgently", "hurried", "hurriedly", "insistent", "insistently", "pressing", "rushed", "breathless"),
    "happy": ("happy", "happily", "cheerful", "cheerfully", "delighted", "gleeful", "brightly", "warmly", "smiling", "grinning", "pleased", "joyful", "joyfully", "laughing"),
    "serious": ("serious", "seriously", "sternly", "stern", "coldly", "flatly", "firmly", "grimly", "gravely", "darkly", "icy", "cold"),
    "calm": ("calm", "calmly", "gently", "gentle", "softly", "soft", "soothing", "serene", "tenderly", "tender", "quietly", "reassuring"),
}

_TOKEN_RE = re.compile(r"[a-z]+")


def emotion_from_parenthetical(text: str) -> str | None:
    """Return a canonical emotion for a parenthetical, or ``None``.

    Matching is word-level (so ``coldly`` matches but ``scold`` would not),
    case-insensitive, and tolerant of the surrounding parentheses and
    punctuation. Multi-word cues like ``under breath`` are matched as phrases.
    """
    if not text:
        return None
    lowered = text.lower()
    tokens = set(_TOKEN_RE.findall(lowered))
    for emotion, keywords in _EMOTION_KEYWORDS.items():
        for keyword in keywords:
            if " " in keyword:
                # Multi-word idiom: all its words must appear, but interposed
                # words are fine ("under breath" matches "under her breath").
                if all(word in tokens for word in keyword.split()):
                    return emotion
            elif keyword in tokens:
                return emotion
    return None
