import cv2
import numpy as np
from PIL import Image
from .format_handler import BaseFormatHandler
from app.core.logging import get_logger
logger = get_logger(__name__)


class JPEGHandler(BaseFormatHandler):
    _QUALITY_RANGE = (0, 100)
    
    @property
    def quality_range(self) -> tuple[int, int]:
        return self._QUALITY_RANGE
    
    @staticmethod
    def can_handle(format):
        return format.upper() in ('JPEG', 'JPG')

    def process(self, processor):
        arr = processor.image_array
        
        # Convert to uint8 if needed
        if arr.dtype != np.uint8:
            arr = (arr * 255).astype(np.uint8) if arr.dtype == np.float32 else arr.astype(np.uint8)

        # Handle alpha channel with white background
        if processor.has_transparency and arr.shape[2] == 4:
            # Split channels
            b, g, r, a = cv2.split(arr)
            # Normalize alpha to 0-1 range
            a = a.astype(np.float32) / 255.0
            # Composite with white background
            r = (r * a + 255 * (1 - a)).astype(np.uint8)
            g = (g * a + 255 * (1 - a)).astype(np.uint8)
            b = (b * a + 255 * (1 - a)).astype(np.uint8)
            # Merge back to RGB
            arr = cv2.merge([b, g, r])
        elif arr.ndim == 2 or arr.shape[2] == 1:  # Grayscale
            arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
        elif arr.shape[2] == 3:  # Ensure RGB format
            arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB) if processor.color_space == 'BGR' else arr

        processor.image_array = arr
        processor.color_space = 'RGB'  # Update color space metadata

    def save(self, processor, buffer):
        try:
            quality = int(processor.quality)
            print("Actual Quality in Save:", quality)


            # Always use Pillow for JPEG encoding to maintain proper color handling
            pil_image = Image.fromarray(processor.image_array)
            
            save_args = {
                'format': 'JPEG',
                'quality': quality,
                'subsampling': 0,  # 4:4:4 chroma
            }
            
            if processor.icc_profile:
                save_args['icc_profile'] = processor.icc_profile

            pil_image.save(buffer, **save_args)
            processor.output_buffer = buffer.getvalue()

        except Exception as e:
            logger.error("JPEG save failed: %s", str(e))
            raise