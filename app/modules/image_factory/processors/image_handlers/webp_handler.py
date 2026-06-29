import cv2
import numpy as np
from PIL import Image
from .format_handler import BaseFormatHandler
from app.core.logs import logger


class WEBPHandler(BaseFormatHandler):
    _QUALITY_RANGE = (1, 90)
    
    @property
    def quality_range(self) -> tuple[int, int]:
        return self._QUALITY_RANGE
    
    @staticmethod
    def can_handle(format):
        return format.upper() == 'WEBP'

    def process(self, processor):
        arr = processor.image_array
        
        if arr.dtype != np.uint8:
            arr = (arr * 255).astype(np.uint8)

        # Handle alpha channel
        has_alpha = processor.has_transparency and arr.shape[2] == 4
        if has_alpha:
            target = cv2.COLOR_RGBA2BGRA if arr.shape[2] == 4 else cv2.COLOR_RGB2BGR
        else:
            target = cv2.COLOR_RGB2BGR

        processor.image_array = cv2.cvtColor(arr, target)

    def save(self, processor, buffer):
        try:
            quality = int(processor.quality)
            print("Actual Quality in Save:", quality)


            if processor.icc_profile or processor.has_transparency:
                if processor.image_array.shape[2] == 4:
                    rgb_array = cv2.cvtColor(processor.image_array, cv2.COLOR_BGRA2RGBA)
                else:
                    rgb_array = cv2.cvtColor(processor.image_array, cv2.COLOR_BGR2RGB)
                
                pil_image = Image.fromarray(rgb_array)
                pil_image.save(
                    buffer,
                    format='WEBP',
                    quality=quality,
                    lossless=False,
                    icc_profile=processor.icc_profile
                )
            else:
                success, encoded = cv2.imencode(
                    '.webp',
                    processor.image_array,
                    [cv2.IMWRITE_WEBP_QUALITY, quality]
                )
                if not success:
                    raise ValueError("WEBP encoding failed")
                buffer.write(encoded.tobytes())

            processor.output_buffer = buffer.getvalue()
        except Exception as e:
            logger.error("WEBP save failed: %s", str(e))
            raise