# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.infrastructure.kokoro_engine
# Layer    : Infrastructure
# Pillar   : P4 Performance (Kokoro-82M int8 ONNX on CPU, no PyTorch),
#            P6 Resilience (portable espeak-ng resolution, validated voices)
# ─────────────────────────────────────────────────────
from __future__ import annotations

import ctypes.util
import glob
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from app.modules.wiskro.domain.errors import InvalidVoiceError
from app.modules.wiskro.domain.models import VoiceBlend
from app.modules.wiskro.domain.ports import TextToSpeechEngine
from app.modules.wiskro.infrastructure.lazy_model import LazyModel
from app.modules.wiskro.infrastructure.model_store import ModelStore, RemoteAsset

if TYPE_CHECKING:
    from kokoro_onnx import Kokoro
    from kokoro_onnx.config import EspeakConfig

# Kokoro voice ids are "<lang><gender>_<name>"; the first letter selects the phonemiser language
LANG_BY_VOICE_PREFIX = {
    "a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
    "i": "it", "j": "ja", "p": "pt-br", "z": "cmn",
}
SUPPORTED_LANGS = frozenset(LANG_BY_VOICE_PREFIX.values())

_ESPEAK_DATA_GLOBS = (
    "/usr/lib/*/espeak-ng-data",          # Debian/Ubuntu multiarch (libespeak-ng1)
    "/usr/share/espeak-ng-data",
    "/usr/local/share/espeak-ng-data",
    "/opt/homebrew/share/espeak-ng-data",  # macOS (brew install espeak-ng)
)
_MAX_CACHED_STYLES = 16

# phonemizer resets its logger's level/handlers on every call but never `propagate`,
# so cut propagation to keep per-sentence "words count mismatch" noise out of app logs
logging.getLogger("phonemizer").propagate = False
logging.getLogger("kokoro_onnx").setLevel(logging.WARNING)


def _find_espeak_data(library: str | None) -> str | None:
    candidates: list[Path] = []
    if library and Path(library).is_absolute():
        lib_dir = Path(library).resolve().parent
        candidates += [lib_dir / "espeak-ng-data", lib_dir.parent / "share" / "espeak-ng-data"]
    for pattern in _ESPEAK_DATA_GLOBS:
        candidates += [Path(p) for p in sorted(glob.glob(pattern))]
    return next((str(c) for c in candidates if (c / "phontab").is_file()), None)


class KokoroOnnxEngine(LazyModel["Kokoro"], TextToSpeechEngine):
    """kokoro-onnx adapter for the TTS port."""

    sample_rate = 24000

    def __init__(
        self,
        store: ModelStore,
        model_asset: RemoteAsset,
        voices_asset: RemoteAsset,
        cpu_threads: int,
        espeak_library: str = "",
        espeak_data_path: str = "",
    ) -> None:
        super().__init__(name=f"kokoro:{model_asset.filename}")
        self._store = store
        self._model_asset = model_asset
        self._voices_asset = voices_asset
        self._cpu_threads = cpu_threads
        self._espeak_library = espeak_library
        self._espeak_data_path = espeak_data_path
        self._model_path: Path | None = None
        self._voices_path: Path | None = None
        self._voice_names: frozenset[str] = frozenset()
        self._styles: dict[str, np.ndarray] = {}

    # ── LazyModel hooks ───────────────────────────────────────────────────────

    def _ensure_assets(self) -> None:
        self._model_path = self._store.ensure(self._model_asset, "kokoro")
        self._voices_path = self._store.ensure(self._voices_asset, "kokoro")
        with np.load(self._voices_path) as voices:   # reads the zip index only
            self._voice_names = frozenset(voices.files)

    def _load(self) -> "Kokoro":
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        options = ort.SessionOptions()
        options.intra_op_num_threads = self._cpu_threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.enable_cpu_mem_arena = False   # release activation memory after each call (4 GB host)
        options.log_severity_level = 3
        session = ort.InferenceSession(str(self._model_path), sess_options=options, providers=["CPUExecutionProvider"])
        return Kokoro.from_session(session, str(self._voices_path), espeak_config=self._espeak_config())

    def _on_unload(self) -> None:
        self._styles.clear()

    # ── Port implementation ───────────────────────────────────────────────────

    def resolve_lang(self, voice: VoiceBlend, lang: str | None) -> str:
        self.ensure_assets()
        unknown = [name for name in voice.names if name not in self._voice_names]
        if unknown:
            raise InvalidVoiceError(
                f"Unknown voice(s): {', '.join(unknown)}. Available: {', '.join(sorted(self._voice_names))}."
            )
        resolved = (lang or LANG_BY_VOICE_PREFIX.get(voice.names[0][0], "")).lower()
        if resolved not in SUPPORTED_LANGS:
            raise InvalidVoiceError(f"Unsupported language {resolved!r}. Supported: {', '.join(sorted(SUPPORTED_LANGS))}.")
        return resolved

    def synthesize(self, text: str, voice: VoiceBlend, lang: str, speed: float) -> np.ndarray:
        with self.use() as kokoro:
            audio, _ = kokoro.create(text, voice=self._style(kokoro, voice), speed=speed, lang=lang, trim=True)
        return np.asarray(audio, dtype=np.float32)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _style(self, kokoro: "Kokoro", voice: VoiceBlend) -> np.ndarray:
        """Weighted blend of voice style tensors, memoised per canonical spec."""
        key = voice.canonical
        style = self._styles.get(key)
        if style is None:
            style = sum(
                (kokoro.get_voice_style(name).astype(np.float32) * np.float32(weight) for name, weight in voice.components),
                start=np.float32(0),
            )
            if len(self._styles) >= _MAX_CACHED_STYLES:
                self._styles.pop(next(iter(self._styles)))
            self._styles[key] = style
        return style

    def _espeak_config(self) -> "EspeakConfig":
        """Prefer an explicit or system espeak-ng (matching lib + data), else the bundled loader."""
        from kokoro_onnx.config import EspeakConfig

        library = self._espeak_library or ctypes.util.find_library("espeak-ng")
        data_path = self._espeak_data_path or _find_espeak_data(library)
        if library and data_path:
            return EspeakConfig(lib_path=library, data_path=data_path)
        return EspeakConfig(data_path=self._espeak_data_path or None)
