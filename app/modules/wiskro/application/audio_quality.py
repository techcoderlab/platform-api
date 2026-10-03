# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.application.audio_quality
# Layer    : Application
# Pillar   : P4 Performance (vectorised NumPy, bounded FFT work),
#            P6 Resilience (reject noise before paying for inference)
# Complexity: O(n) framing + O(k · f log f) FFT on at most _MAX_FFT_FRAMES frames
# ─────────────────────────────────────────────────────
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.modules.wiskro.domain.models import AudioQuality, RejectionReason

_FRAME_S = 0.03                  # 30 ms analysis frames
_SPEECH_PERCENTILE = 95          # loudness of the speech frames
_NOISE_PERCENTILE = 10           # loudness of the quietest frames (noise floor)
_LOUD_FRAME_PERCENTILE = 70      # frames used for spectral analysis
_MAX_FFT_FRAMES = 2000           # caps FFT memory/CPU on long clips
_SPEECH_BAND_HZ = (300.0, 3400.0)
_MIN_ANALYSIS_HZ = 50.0          # ignore DC and rumble in the total
_EPS = 1e-10


@dataclass(frozen=True)
class QualityThresholds:
    min_duration_s:        float
    min_speech_rms:        float
    min_snr_db:            float
    min_speech_band_ratio: float


def _dbfs(value: float) -> float:
    return round(float(20.0 * np.log10(value + _EPS)), 2)


def _speech_band_ratio(frames: np.ndarray, frame_rms: np.ndarray, sample_rate: int) -> float:
    """Share of spectral energy in the telephone speech band, over the loudest frames."""
    loud = frames[frame_rms >= np.percentile(frame_rms, _LOUD_FRAME_PERCENTILE)]
    if len(loud) > _MAX_FFT_FRAMES:
        loud = loud[np.linspace(0, len(loud) - 1, _MAX_FFT_FRAMES).astype(int)]

    window = np.hanning(frames.shape[1]).astype(np.float32)
    power = np.abs(np.fft.rfft(loud * window, axis=1)) ** 2
    spectrum = power.sum(axis=0)
    freqs = np.fft.rfftfreq(frames.shape[1], d=1.0 / sample_rate)

    total = spectrum[freqs >= _MIN_ANALYSIS_HZ].sum()
    low, high = _SPEECH_BAND_HZ
    band = spectrum[(freqs >= low) & (freqs <= high)].sum()
    return float(band / max(total, _EPS))


def analyze_audio(samples: np.ndarray, sample_rate: int, thresholds: QualityThresholds) -> AudioQuality:
    """Measure loudness, SNR and speech-band energy, and decide whether to reject the clip.

    Levels are taken from frame-RMS percentiles rather than whole-clip RMS, so long
    recordings with natural pauses are not mistaken for silence, and the noise floor
    is the quiet frames between words instead of near-zero individual samples.
    """
    duration_s = len(samples) / sample_rate
    frame_len = int(sample_rate * _FRAME_S)
    n_frames = len(samples) // frame_len

    if n_frames == 0:
        return AudioQuality(
            duration_s=round(duration_s, 3), speech_level_dbfs=_dbfs(0.0), noise_floor_dbfs=_dbfs(0.0),
            snr_db=0.0, speech_band_ratio=0.0, rejection=RejectionReason.TOO_SHORT,
        )

    frames = samples[: n_frames * frame_len].reshape(n_frames, frame_len)
    frame_rms = np.sqrt(np.mean(np.square(frames, dtype=np.float32), axis=1))
    speech_level = float(np.percentile(frame_rms, _SPEECH_PERCENTILE))
    noise_floor = float(np.percentile(frame_rms, _NOISE_PERCENTILE))
    snr_db = round(float(20.0 * np.log10((speech_level + _EPS) / (noise_floor + _EPS))), 2)
    band_ratio = round(_speech_band_ratio(frames, frame_rms, sample_rate), 3)

    rejection: RejectionReason | None = None
    if duration_s < thresholds.min_duration_s:
        rejection = RejectionReason.TOO_SHORT
    elif speech_level < thresholds.min_speech_rms:
        rejection = RejectionReason.SILENCE
    elif snr_db < thresholds.min_snr_db:
        rejection = RejectionReason.LOW_SNR
    elif band_ratio < thresholds.min_speech_band_ratio:
        rejection = RejectionReason.LOW_SPEECH_BAND_ENERGY

    return AudioQuality(
        duration_s=round(duration_s, 3),
        speech_level_dbfs=_dbfs(speech_level),
        noise_floor_dbfs=_dbfs(noise_floor),
        snr_db=snr_db,
        speech_band_ratio=band_ratio,
        rejection=rejection,
    )
