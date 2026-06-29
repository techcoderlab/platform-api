import cv2
import numpy as np
from PIL import Image
from .format_handler import BaseFormatHandler
from app.core.logging import get_logger
logger = get_logger(__name__)


class AVIFHandler(BaseFormatHandler):
    _QUALITY_RANGE = (10, 80)
    _LOSSLESS_QUALITY = 100  # Special value for lossless compression
    
    @property
    def quality_range(self) -> tuple[int, int]:
        return self._QUALITY_RANGE

    @property
    def lossless_compression(self) -> int:
        return self._LOSSLESS_QUALITY

    
    @staticmethod
    def can_handle(format):
        return format.upper() == 'AVIF'

    def process(self, processor):
        arr = processor.image_array
        
        if arr.dtype != np.uint8:
            arr = (arr * 255).astype(np.uint8)

        # AVIF supports alpha - maintain channel count
        if arr.shape[2] == 4:
            processor.image_array = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGRA)
        elif arr.shape[2] == 3:
            processor.image_array = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        else:
            processor.image_array = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)

    def save(self, processor, buffer):
        try:
            quality = int(processor.quality)
            
            # Determine lossless mode and final quality value
            lossless = quality >= self._LOSSLESS_QUALITY
            final_quality = None if lossless else quality
            print("Actual Quality in Save:", final_quality)

            # OpenCV doesn't support AVIF encoding, use Pillow
            if processor.image_array.shape[2] == 4:
                rgb_array = cv2.cvtColor(processor.image_array, cv2.COLOR_BGRA2RGBA)
            else:
                rgb_array = cv2.cvtColor(processor.image_array, cv2.COLOR_BGR2RGB)
            
            pil_image = Image.fromarray(rgb_array)
            save_args = {
                'format': 'AVIF',
                'quality': final_quality,
                'lossless': lossless,
                'icc_profile': processor.icc_profile
            }

            pil_image.save(buffer, **save_args)
            processor.output_buffer = buffer.getvalue()
            
        except Exception as e:
            logger.error("AVIF save failed: %s", str(e))
            raise


