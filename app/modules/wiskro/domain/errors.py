# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.domain.errors
# Layer    : Domain
# Pillar   : P6 Resilience (typed errors, mapped to HTTP by the global handler)
# ─────────────────────────────────────────────────────
from app.core.errors import (
    PayloadTooLargeError,
    ServiceUnavailableError,
    ValidationError,
)


class AudioDecodeError(ValidationError):
    """Uploaded bytes are not decodable audio."""


class AudioTooLongError(PayloadTooLargeError):
    """Decoded audio exceeds the configured duration limit."""


class InvalidVoiceError(ValidationError):
    """Unknown voice, malformed blend spec, or unsupported language."""


class EngineUnavailableError(ServiceUnavailableError):
    """A model could not be fetched or loaded; the request may be retried later."""
