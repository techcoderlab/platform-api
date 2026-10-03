# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.application.audio_mastering
# Layer    : Application
# Pillar   : P4 Performance (in-memory NumPy DSP, no extra encode passes)
# Complexity: O(n)
# ─────────────────────────────────────────────────────
from __future__ import annotations

import numpy as np

_FADE_S = 0.005             # de-click ramps at every join
_TARGET_RMS_DBFS = -20.0    # consistent loudness across voices
_PEAK_CEILING_DBFS = -1.0   # headroom for lossy encoders (no inter-sample clipping)
_MAX_GAIN_DB = 12.0         # never amplify near-silence into noise
_ACTIVE_THRESHOLD = 1e-3    # samples counted towards loudness


def _db_to_linear(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def silence(seconds: float, sample_rate: int) -> np.ndarray:
    return np.zeros(max(0, int(round(seconds * sample_rate))), dtype=np.float32)


def apply_fades(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """Short linear fade-in/out so concatenated sentences never click."""
    n = min(int(sample_rate * _FADE_S), len(audio) // 2)
    if n == 0:
        return audio
    audio = audio.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    audio[:n] *= ramp
    audio[-n:] *= ramp[::-1]
    return audio


def master(audio: np.ndarray) -> np.ndarray:
    """DC removal, loudness normalisation to a target RMS, then a hard peak ceiling.

    Replaces the original "normalise to 0.98 then +1 dB gain" chain, which pushed
    peaks past full scale and clipped.
    """
    if audio.size == 0:
        return audio
    audio = audio.astype(np.float32, copy=True)
    audio -= np.float32(audio.mean())

    active = audio[np.abs(audio) > _ACTIVE_THRESHOLD]
    if active.size:
        rms = float(np.sqrt(np.mean(np.square(active))))
        gain = min(_db_to_linear(_TARGET_RMS_DBFS) / max(rms, 1e-9), _db_to_linear(_MAX_GAIN_DB))
        audio *= np.float32(gain)

    peak = float(np.max(np.abs(audio)))
    ceiling = _db_to_linear(_PEAK_CEILING_DBFS)
    if peak > ceiling:
        audio *= np.float32(ceiling / peak)
    return audio
