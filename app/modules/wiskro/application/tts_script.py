# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.application.tts_script
# Layer    : Application
# Pillar   : P2 Security (sanitises LLM/markdown output before phonemisation),
#            P8 Code Quality (pure functions, no I/O)
# Complexity: O(n) in text length
# ─────────────────────────────────────────────────────
"""Turn free text (often LLM output) into a speakable script of sentences and pauses.

Script markup (see the module README for the LLM prompt that produces it):
    [PAUSE]            0.5 s of silence
    [PAUSE 1.5]        custom silence in seconds ("[PAUSE:1.5s]" and "[PAUSE 800ms]" also work)
    ...                mid-sentence micro-pause (left to the model, never a sentence break)
    !                  energetic sentence, spoken faster
    blank line         paragraph break, short pause
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

from app.modules.wiskro.domain.models import PausePart, ScriptPart, SpeechPart

DEFAULT_PAUSE_S = 0.5
PARAGRAPH_PAUSE_S = 0.35
MAX_PAUSE_S = 5.0
MAX_SENTENCE_CHARS = 400   # keeps each inference inside Kokoro's 510-phoneme window

# Prosody multipliers, applied on top of the requested speed. Boosts are gentler than
# the original 1.3/1.1: Kokoro starts swallowing word onsets above ~1.2x.
EXCLAMATION_SPEED = 1.15
SHORT_SENTENCE_SPEED = 1.05
LONG_SENTENCE_SPEED = 0.9
DEFAULT_SENTENCE_SPEED = 0.95
FINAL_SENTENCE_SPEED = 0.92
SHORT_SENTENCE_CHARS = 50
LONG_SENTENCE_CHARS = 150

_PAUSE = re.compile(r"\[\s*pause(?:\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(ms|s)?)?\s*\]", re.IGNORECASE)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])(?<!\.\.\.)([\"'”’)\]]*)\s+")
_CLAUSE_END = re.compile(r"(?<=[,;:])\s+")
_ABBREVIATIONS = re.compile(r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|No|Inc|Ltd|Co|e\.g|i\.e)\.", re.IGNORECASE)
_ABBREVIATION_DOT = "․"   # one-dot leader: survives sentence splitting, restored afterwards

_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)\s]+)\)")
_MD_EMPHASIS = re.compile(r"(\*{1,3}|_{2,3})(\S(?:.*?\S)?)\1")
_MD_LINE_PREFIX = re.compile(r"^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+•]\s+)")
_TERMINAL_PUNCTUATION = ".!?;:,"
_URL = re.compile(r"https?://[^\s)\]]+")
_LEFTOVER_MARKUP = re.compile(r"[*`~^|<>{}]")
_DROPPED_CATEGORIES = frozenset({"So", "Cs", "Co", "Cn", "Cc"})   # emoji/pictographs, control chars
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠︎️"))
_TYPOGRAPHY = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"',
    "–": ", ", "—": ", ", "…": "...", " ": " ",
})


def _speakable_url(match: re.Match[str]) -> str:
    host = urlparse(match.group(0)).netloc
    return host.removeprefix("www.") or "link"


def normalize_text(text: str) -> str:
    """Strip markdown, emoji and control characters; keep paragraph breaks and pause markers."""
    text = unicodedata.normalize("NFKC", text).translate(_TYPOGRAPHY).translate(_ZERO_WIDTH)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in _DROPPED_CATEGORIES)
    text = _URL.sub(_speakable_url, text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_EMPHASIS.sub(r"\2", text)
    text = "\n".join(_strip_line_markup(line) for line in text.split("\n"))
    text = _LEFTOVER_MARKUP.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def _strip_line_markup(line: str) -> str:
    """Drop heading/bullet/quote prefixes; such lines become their own sentence."""
    stripped, prefixed = _MD_LINE_PREFIX.subn("", line)
    stripped = stripped.strip()
    if prefixed and stripped and stripped[-1] not in _TERMINAL_PUNCTUATION:
        stripped += "."
    return stripped


def _pause_seconds(value: str | None, unit: str | None) -> float:
    if value is None:
        return DEFAULT_PAUSE_S
    seconds = float(value) / 1000.0 if (unit or "").lower() == "ms" else float(value)
    return min(max(seconds, 0.0), MAX_PAUSE_S)


def _split_sentences(paragraph: str) -> list[str]:
    protected = _ABBREVIATIONS.sub(lambda m: m.group(0)[:-1] + _ABBREVIATION_DOT, paragraph)
    sentences, start = [], 0
    for match in _SENTENCE_END.finditer(protected):
        sentences.append(protected[start:match.end(1)])
        start = match.end()
    sentences.append(protected[start:])
    return [s.replace(_ABBREVIATION_DOT, ".").strip() for s in sentences if s.strip()]


def _pack(pieces: list[str], separator: str, limit: int) -> list[str]:
    """Greedily join pieces into chunks of at most `limit` characters."""
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}{separator}{piece}" if current else piece
        if len(candidate) <= limit or not current:
            current = candidate
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def _split_long(sentence: str, limit: int = MAX_SENTENCE_CHARS) -> list[str]:
    """Split an over-long sentence at clause punctuation, then at word boundaries."""
    if len(sentence) <= limit:
        return [sentence]
    chunks: list[str] = []
    for clause in _pack(_CLAUSE_END.split(sentence), " ", limit):
        chunks.extend(_pack(clause.split(), " ", limit) if len(clause) > limit else [clause])
    return chunks


def _speed_factor(sentence: str, is_final: bool) -> float:
    if sentence.endswith("!"):
        return EXCLAMATION_SPEED
    if is_final:
        return FINAL_SENTENCE_SPEED
    if len(sentence) < SHORT_SENTENCE_CHARS:
        return SHORT_SENTENCE_SPEED
    if len(sentence) > LONG_SENTENCE_CHARS:
        return LONG_SENTENCE_SPEED
    return DEFAULT_SENTENCE_SPEED


def _append_pause(script: list[ScriptPart], seconds: float) -> None:
    """Merge consecutive pauses so markup never stacks into dead air."""
    if seconds <= 0:
        return
    if script and isinstance(script[-1], PausePart):
        script[-1] = PausePart(min(script[-1].seconds + seconds, MAX_PAUSE_S))
    else:
        script.append(PausePart(seconds))


def _append_block(script: list[ScriptPart], block: str, prosody: bool) -> None:
    paragraphs = [p for p in _PARAGRAPH_BREAK.split(block) if p.strip()]
    for p_index, paragraph in enumerate(paragraphs):
        if p_index:
            _append_pause(script, PARAGRAPH_PAUSE_S)
        chunks = [c for s in _split_sentences(" ".join(paragraph.split())) for c in _split_long(s)]
        for c_index, chunk in enumerate(chunks):
            is_final = c_index == len(chunks) - 1
            factor = _speed_factor(chunk, is_final) if prosody else 1.0
            script.append(SpeechPart(text=chunk, speed_factor=factor))


def build_script(text: str, *, prosody: bool = True) -> list[ScriptPart]:
    """Normalise `text` and split it into sentences and pauses, ready for synthesis."""
    normalized = normalize_text(text)
    script: list[ScriptPart] = []
    cursor = 0
    for match in _PAUSE.finditer(normalized):
        _append_block(script, normalized[cursor:match.start()], prosody)
        _append_pause(script, _pause_seconds(match.group(1), match.group(2)))
        cursor = match.end()
    _append_block(script, normalized[cursor:], prosody)
    return script
