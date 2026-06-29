from .format_handler import BaseFormatHandler
import cv2
import numpy as np
from PIL import Image

from app.core.logs import logger


class PNGHandler(BaseFormatHandler):
    _COMPRESSION_RANGE = (9, 0)
    
    @staticmethod
    def can_handle(format):
        return format.upper() == 'PNG'
    
    @property
    def quality_range(self) -> tuple[int, int]:
        return self._COMPRESSION_RANGE

    def process(self, processor):
        """Optimized color space conversion pipeline"""
        arr = processor.image_array
        
        # Preserve original dtype information
        original_dtype = arr.dtype
        
        # Convert to uint8 if needed
        if arr.dtype != np.uint8:
            arr = (arr * 255).astype(np.uint8) if arr.dtype == np.float32 else arr.astype(np.uint8)

        # Track alpha channel presence
        has_alpha = processor.has_transparency

        # Unified color conversion logic
        if arr.ndim == 2 or arr.shape[2] == 1:  # Grayscale
            target = cv2.COLOR_GRAY2BGRA if has_alpha else cv2.COLOR_GRAY2BGR
        elif arr.shape[2] == 3:  # RGB/BGR
            target = cv2.COLOR_RGB2BGRA if has_alpha else cv2.COLOR_RGB2BGR
        elif arr.shape[2] == 4:  # RGBA
            target = cv2.COLOR_RGBA2BGRA

        # Preserve original dtype in processor metadata
        processor._original_dtype = original_dtype
        
        processor.image_array = cv2.cvtColor(arr, target)
        

    def save(self, processor, buffer):
        """Enhanced PNG saving with ICC profile support"""
        try:
            compression = int(processor.compression_level)
            print("Actual Compression Level in Save:", compression)
            
            if getattr(processor, 'icc_profile', None):
                # Convert back to Pillow-compatible color space
                if processor.image_array.shape[2] == 4:
                    rgb_array = cv2.cvtColor(processor.image_array, cv2.COLOR_BGRA2RGBA)
                else:
                    rgb_array = cv2.cvtColor(processor.image_array, cv2.COLOR_BGR2RGB)
                
                # Restore original dtype if needed
                if getattr(processor, '_original_dtype', None) == np.float32:
                    rgb_array = rgb_array.astype(np.float32) / 255.0
                
                pil_image = Image.fromarray(rgb_array)
                pil_image.save(
                    buffer,
                    format='PNG',
                    compress_level=compression,
                    icc_profile=processor.icc_profile
                )
            else:
                # OpenCV optimized path
                success, encoded = cv2.imencode(
                    '.png',
                    processor.image_array,
                    [cv2.IMWRITE_PNG_COMPRESSION, compression]
                )
                
                if not success:
                    logger.error("PNG encoding failed for array shape: %s", 
                                processor.image_array.shape)
                    raise ValueError("PNG encoding failed")

                buffer.write(encoded.tobytes())  # Fix numpy array to bytes conversion

            processor.output_buffer = buffer.getvalue()
            
        except Exception as e:
            logger.error("PNG save failed: %s", str(e), exc_info=True)
            raise