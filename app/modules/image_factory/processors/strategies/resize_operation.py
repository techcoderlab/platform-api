from .image_operation import ConversionStrategy
from ..core.image_processor import ImageProcessor
from PIL import Image
import numpy as np
import cv2

class ResizeOperator(ConversionStrategy):
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        
    def execute(self, processor: ImageProcessor):
        self._resize_optimized(processor)

    def _resize_optimized(self, processor: ImageProcessor):
        """Color-preserving resize implementation with proper color space handling"""
        if not (self.width or self.height):
            raise ValueError("At least one dimension required")

        # Use array instead of Pillow image to preserve color space
        arr = processor.image_array
        original_height, original_width = arr.shape[:2]
        
        # Calculate target dimensions
        if self.width and self.height:
            new_w, new_h = self.width, self.height
        else:
            ratio = original_width / original_height
            if self.width:
                new_w = self.width
                new_h = int(self.width / ratio)
            else:
                new_h = self.height
                new_w = int(self.height * ratio)

        if (new_w, new_h) == (original_width, original_height):
            return  # Skip resize if dimensions match

        # Preserve original color space and alpha
        interpolation = cv2.INTER_AREA if (new_w < original_width or new_h < original_height) \
                        else cv2.INTER_LANCZOS4

        # Handle different channel counts
        channels = arr.shape[2] if arr.ndim == 3 else 1
        processor.image_array = cv2.resize(
            arr,
            (new_w, new_h),
            interpolation=interpolation
        )

        # Update Pillow image reference to match array state
        processor.image = Image.fromarray(
            cv2.cvtColor(processor.image_array, cv2.COLOR_BGR2RGB) if channels in {3,4} 
            else processor.image_array
        )