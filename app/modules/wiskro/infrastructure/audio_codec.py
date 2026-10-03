# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.infrastructure.audio_codec
# Layer    : Infrastructure
# Pillar   : P2 Security (untrusted media: no network/file indirection),
#            P4 Performance (in-process FFmpeg via PyAV — no subprocess, no disk),
#            P6 Resilience (tolerates corrupt packets in browser recordings)
# ─────────────────────────────────────────────────────
from __future__ import annotations

import io
import wave
from dataclasses import dataclass

import numpy as np

from app.modules.wiskro.domain.errors import AudioDecodeError, AudioTooLongError
from app.modules.wiskro.domain.models import AudioFormat
from app.modules.wiskro.domain.ports import AudioCodecPort

# Demuxers that reference other resources (URLs/files) instead of carrying audio
_INDIRECT_DEMUXERS = frozenset({"hls", "applehttp", "dash", "concat", "ffconcat", "tee", "image2", "lavfi", "sdp", "rtsp", "rtp"})
_PLAYLIST_SIGNATURES = (b"#EXTM3U", b"ffconcat", b"<?xml", b"<MPD", b"[playlist]")
# Nested opens may only use the in-memory stream itself
_DECODE_OPTIONS = {"protocol_whitelist": "pipe"}
_MAX_CORRUPT_PACKETS = 25


@dataclass(frozen=True)
class _EncoderSpec:
    container:   str
    codec:       str
    sample_rate: int | None = None   # None = keep source rate
    bit_rate:    int | None = None


class PyAVAudioCodec(AudioCodecPort):
    """Decode any FFmpeg-supported upload; encode MP3/Opus/FLAC/WAV — all in RAM."""

    def __init__(self, mp3_bitrate_kbps: int) -> None:
        self._encoders: dict[AudioFormat, _EncoderSpec] = {
            AudioFormat.MP3:  _EncoderSpec("mp3", "libmp3lame", bit_rate=mp3_bitrate_kbps * 1000),
            AudioFormat.OPUS: _EncoderSpec("ogg", "libopus", sample_rate=48000, bit_rate=48000),
            AudioFormat.FLAC: _EncoderSpec("flac", "flac"),
        }

    # ── Decode ────────────────────────────────────────────────────────────────

    def decode(self, data: bytes, sample_rate: int, max_seconds: float) -> np.ndarray:
        import av   # lazy: keeps FFmpeg out of processes that never decode

        if not data:
            raise AudioDecodeError("Audio file is empty.")
        head = data[:64].lstrip()
        if any(head.startswith(signature) for signature in _PLAYLIST_SIGNATURES):
            raise AudioDecodeError("Playlists and manifests are not accepted; upload the audio itself.")

        max_samples = int(max_seconds * sample_rate)
        chunks: list[np.ndarray] = []
        total = 0
        try:
            with av.open(io.BytesIO(data), mode="r", options=_DECODE_OPTIONS) as container:
                if container.format.name in _INDIRECT_DEMUXERS:
                    raise AudioDecodeError(f"Container format {container.format.name!r} is not accepted.")
                if not container.streams.audio:
                    raise AudioDecodeError("File contains no audio stream.")
                stream = container.streams.audio[0]
                resampler = av.AudioResampler(format="flt", layout="mono", rate=sample_rate)
                corrupt = 0

                for packet in container.demux(stream):
                    try:
                        frames = packet.decode()
                    except av.error.InvalidDataError:
                        corrupt += 1
                        if corrupt > _MAX_CORRUPT_PACKETS:
                            raise AudioDecodeError("Audio stream is corrupt.") from None
                        continue
                    for frame in frames:
                        for resampled in resampler.resample(frame):
                            chunk = resampled.to_ndarray().reshape(-1)
                            total += chunk.size
                            if total > max_samples:
                                raise AudioTooLongError(f"Audio exceeds the {max_seconds:g}s limit.")
                            chunks.append(chunk)
                for resampled in resampler.resample(None):
                    chunks.append(resampled.to_ndarray().reshape(-1))
        except (AudioDecodeError, AudioTooLongError):
            raise
        except av.error.FFmpegError as exc:
            raise AudioDecodeError("Unsupported or corrupt audio file.") from exc

        if not chunks:
            raise AudioDecodeError("Audio file decoded to no samples.")
        return np.concatenate(chunks).astype(np.float32, copy=False)[:max_samples]

    # ── Encode ────────────────────────────────────────────────────────────────

    def encode(self, samples: np.ndarray, sample_rate: int, audio_format: AudioFormat) -> bytes:
        samples = np.clip(samples, -1.0, 1.0).astype(np.float32, copy=False)
        if audio_format is AudioFormat.WAV:
            return self._encode_wav(samples, sample_rate)
        return self._encode_ffmpeg(samples, sample_rate, self._encoders[audio_format])

    @staticmethod
    def _encode_wav(samples: np.ndarray, sample_rate: int) -> bytes:
        pcm = (samples * 32767.0).astype("<i2")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm.tobytes())
        return buffer.getvalue()

    @staticmethod
    def _encode_ffmpeg(samples: np.ndarray, sample_rate: int, spec: _EncoderSpec) -> bytes:
        import av

        buffer = io.BytesIO()
        with av.open(buffer, mode="w", format=spec.container) as container:
            stream = container.add_stream(spec.codec, rate=spec.sample_rate or sample_rate)
            stream.layout = "mono"
            if spec.bit_rate:
                stream.bit_rate = spec.bit_rate
            frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="flt", layout="mono")
            frame.sample_rate = sample_rate
            for packet in stream.encode(frame):   # PyAV resamples and re-frames to the codec's needs
                container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
        return buffer.getvalue()
