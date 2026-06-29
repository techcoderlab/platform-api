# app/services/processors/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import io


@dataclass
class OptimizationConfig:
    quality: Optional[int] = None  # Range 0-100
    size_kb: Optional[int] = None  # File size limit in KB


class BaseFormatHandler(ABC):
    """Abstract base for all format-specific handlers."""

    @staticmethod
    @abstractmethod
    def can_handle(format: str) -> bool:
        """Return True if this handler supports the given format."""
        pass

    @abstractmethod
    def process(self, image):
        """Perform format-specific processing."""
        pass

    @abstractmethod
    def save(self, image, buffer: io.BytesIO):
        """Save the processed image into a buffer."""
        pass

    # Optional future method for optimization
    # def optimize(self, image, config: OptimizationConfig) -> None:
    #     pass
