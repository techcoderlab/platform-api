from .image_operation import ConversionStrategy
from ..core.image_processor import ImageProcessor

import numpy as np


class QualityOptimizer(ConversionStrategy):
    def __init__(self, quality: int):
        self.quality_percentage = quality
        
    def execute(self, processor: ImageProcessor):
        self.adjust_quality(processor=processor)
        
    def adjust_quality(self, processor: ImageProcessor):
        """Direct quality adjustment method"""
        
        image_handler = processor._current_handler
        
        if image_handler is None:
            raise ValueError("Output image handler is not set, FormatConverter must be executed first.")
        
        # Convert quality percent to value
        quality_value = self.percentage_to_quality(
            self.quality_percentage, *image_handler.quality_range)
        
        # Set processor quality or compression level
        if processor.output_format == "PNG":
            processor.compression_level = quality_value
        else:
            processor.quality = quality_value
                      
    def percentage_to_quality(self, percentage, min_val, max_val):
        """Calculates a value within a given range based on the quality percentage, handling reversed ranges."""

        if min_val < max_val:
            range_size = max_val - min_val
            result = min_val + (range_size * (percentage / 100.0))
        else:  # Handle reversed range (max_val < min_val)
            range_size = min_val - max_val
            result = min_val - (range_size * (percentage / 100.0))

        return np.clip(result, min(min_val, max_val), max(min_val, max_val)) #correctly clips regardless of min/max order.
            
            

class SizeOptimizer(ConversionStrategy):
    def __init__(self, target_kb: int, format: str):
        self.target_kb = target_kb
        self.format = format.upper()
        
    def execute(self, processor: ImageProcessor):
        raise NotImplementedError("SizeOptimizer is not yet implemented")
        # processor._current_format = self.format
        # processor.reduce_size(self.target_kb)
        
    # def reduce_size(self, target_kb: int):
    #     """Size-based optimization with quality adaptation"""
    #     # Set binary search bounds correctly
    #     if self._current_format == 'PNG':
    #         low, high = 1, 9  # PNG compression levels (1 = least, 9 = max compression)
    #     else:
    #         low, high = 30, 100  # JPEG/WebP quality levels

    #     best = high  # Start with the least lossy option

    #     for _ in range(8):  # Limited iterations for efficiency
    #         mid = (low + high) // 2
    #         self.adjust_quality(mid)
    #         self.save()
    #         size_kb = len(self.output_buffer) / 1024

    #         if size_kb <= target_kb:
    #             best = mid  # Update best to the highest valid compression/quality
    #             if self._current_format == 'PNG':
    #                 low = mid + 1  # PNG: Try a lower quality (higher compression)
    #             else:
    #                 low = mid + 1  # JPEG/WebP: Try a higher quality
    #         else:
    #             high = mid - 1  # Reduce search space if it's still too large

    #         print(f"Mid: {mid}, Best: {best}, Size: {size_kb:.2f} KB")

    #     # Apply the best found compression/quality level
    #     self.adjust_quality(best)
        
        

