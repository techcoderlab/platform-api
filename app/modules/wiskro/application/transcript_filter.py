# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.application.transcript_filter
# Layer    : Application
# Pillar   : P6 Resilience (suppresses Whisper hallucinations and noise transcripts)
# Complexity: O(n) in transcript length
# ─────────────────────────────────────────────────────
from __future__ import annotations

import re
import string
import unicodedata
from functools import lru_cache

from app.modules.wiskro.domain.models import TranscribedSegment

# Phrases Whisper emits on silence/music because of its subtitle training data.
# Matched against the whole segment only, so real speech containing them survives.
_HALLUCINATIONS = frozenset({
    "thanks for watching", "thank you for watching", "thank you so much for watching",
    "thanks for watching and see you next time", "please subscribe", "like and subscribe",
    "please like and subscribe", "dont forget to like and subscribe", "subscribe to my channel",
    "subtitles by the amaraorg community", "subtitles by", "transcribed by", "captions by",
    "translated by", "amaraorg", "see you in the next video", "music", "applause",
})

# Short replies that are meaningful despite failing the length/word-count heuristics
_COMMON_SHORT_REPLIES = frozenset({
    "yes", "yeah", "yep", "ok", "okay", "sure", "no", "nope", "nah", "thanks", "thank you",
    "hi", "hello", "hey", "yo", "goodbye", "bye", "wow", "oh", "oops", "whoa", "hm", "hmm",
    "uh", "eh", "wait", "really", "stop", "go", "right", "correct", "exactly", "great",
    "perfect", "sounds good", "got it", "understood", "i see", "all right", "alright", "noted",
})

_MIN_TEXT_CHARS = 6
_MIN_WORDS = 2
_MIN_ALNUM_RATIO = 0.8
_MIN_LATIN_RATIO = 0.8
_MIN_DICTIONARY_RATIO = 0.6
_MIN_ZIPF_FREQUENCY = 2.0

_REPEATED_WORD = re.compile(r"\b(\w+)(?:[\s,]+\1\b){2,}", re.IGNORECASE)
_REPEATED_PHRASE = re.compile(r"\b((?:\w+\W+){1,5}?\w+)(?:\W+\1\b){2,}", re.IGNORECASE)
_WORD = re.compile(r"\b[\w']+\b")


def _canonical(text: str) -> str:
    return " ".join(text.lower().translate(str.maketrans("", "", string.punctuation)).split())


def collapse_repetitions(text: str) -> str:
    """Collapse Whisper decoding loops ("the the the", "I mean I mean I mean")."""
    text = _REPEATED_PHRASE.sub(r"\1", text)
    text = _REPEATED_WORD.sub(r"\1", text)
    return " ".join(text.split())


def is_hallucination(text: str) -> bool:
    return _canonical(text) in _HALLUCINATIONS


def clean_segments(segments: tuple[TranscribedSegment, ...]) -> tuple[TranscribedSegment, ...]:
    """Drop hallucinated segments and collapse repetition loops inside the rest."""
    cleaned: list[TranscribedSegment] = []
    for segment in segments:
        text = collapse_repetitions(segment.text.strip())
        if not text or is_hallucination(text):
            continue
        if text != segment.text:
            segment = TranscribedSegment(
                start=segment.start, end=segment.end, text=text,
                avg_logprob=segment.avg_logprob, no_speech_prob=segment.no_speech_prob,
                compression_ratio=segment.compression_ratio, words=segment.words,
            )
        cleaned.append(segment)
    return tuple(cleaned)


@lru_cache(maxsize=4096)
def _is_dictionary_word(word: str) -> bool:
    from wordfreq import zipf_frequency   # lazy: loads frequency tables on first use
    return word.isdigit() or zipf_frequency(word, "en") > _MIN_ZIPF_FREQUENCY


def _is_mostly_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    latin = sum(1 for c in letters if "LATIN" in unicodedata.name(c, ""))
    return latin / len(letters) >= _MIN_LATIN_RATIO


def is_meaningful(text: str, language: str | None) -> bool:
    """Heuristic filter for noise-born transcripts (stutters, hums, gibberish).

    English gets the full check (script + dictionary ratio); other languages only
    the language-agnostic structure checks, so valid non-English speech is kept.
    """
    if not text:
        return False
    canonical = _canonical(text)
    if canonical in _COMMON_SHORT_REPLIES:
        return True
    if len(canonical) < _MIN_TEXT_CHARS or len(canonical.split()) < _MIN_WORDS:
        return False

    compact = text.replace(" ", "")
    alnum = sum(1 for c in compact if c.isalnum())
    if alnum / max(len(compact), 1) < _MIN_ALNUM_RATIO:
        return False

    if (language or "en") != "en":
        return True
    if not _is_mostly_latin(text):
        return False
    words = _WORD.findall(text.lower())
    known = sum(1 for w in words if _is_dictionary_word(w))
    return known / max(len(words), 1) >= _MIN_DICTIONARY_RATIO
