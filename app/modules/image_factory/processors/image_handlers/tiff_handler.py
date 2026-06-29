from .format_handler import BaseFormatHandler
import cv2
import numpy as np
import io
from PIL import Image, features
from app.core.logging import get_logger
logger = get_logger(__name__)


class TIFFHandler(BaseFormatHandler):
    _COMPRESSION_MAP = {
        'auto': {
            'lossless': ['lzw', 'deflate', 'zstd'],
            'lossy': ['jpeg', 'webp']
        },
        'quality_based': {
            # 95: 'zstd',     # Best lossless
            85: 'deflate',  # Balanced lossless
            75: 'lzw',      # Wider compatibility
            50: 'jpeg',     # Lossy
            0: 'jpeg'       # Max compression
        }
    }
    
    _QUALITY_RANGE = (0, 95)
    _FALLBACK_COMPRESSION_METHOD = 'lzw'

    
    @property
    def quality_range(self) -> tuple[int, int]:
        return self._QUALITY_RANGE
    
    
    def __init__(self):
        self.zstd_supported = self._verify_zstd_support()
        
    def _verify_zstd_support(self):
        """Check for ZSTD support at runtime"""
        if not features.check('libtiff'):
            return False
            
        try:
            # Test compression
            img = Image.new('RGB', (1,1))
            buf = io.BytesIO()
            img.save(buf, format='TIFF', compression='tiff_zstd')
            return True
        except Exception as e:
            logger.warning(f"ZSTD not available: {str(e)}")
            return False
    
    @staticmethod
    def can_handle(format):
        return format.upper() in ('TIFF', 'TIF')

    def process(self, processor):
        """Process image array for TIFF format requirements"""
        arr = processor.image_array
        
        # Maintain original dtype for TIFF
        if arr.dtype == np.uint8 and processor.image.mode == 'I;16':
            arr = arr.astype(np.uint16) * 257  # Scale 8-bit to 16-bit
        elif arr.dtype == np.float32:
            arr = (arr * 65535).astype(np.uint16)  # Convert float to 16-bit

        # Handle color space conversions
        if arr.ndim == 2 or arr.shape[2] == 1:  # Grayscale
            arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        elif arr.shape[2] == 3:  # RGB/BGR
            arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        elif arr.shape[2] == 4:  # RGBA
            arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGRA)

        processor.image_array = arr

    def _determine_compression(self, processor):
        """Auto-select compression based on image characteristics"""
        
        if processor.compression_method.lower() != 'auto':
            return processor.compression_method

        if processor.has_transparency:
            return 'deflate'  # Best lossless with alpha support

        for threshold in sorted(self._COMPRESSION_MAP['quality_based'].keys(), reverse=True):
            if processor.quality >= threshold:
                
                return self._COMPRESSION_MAP['quality_based'][threshold]
                
        return 'none'

    def save(self, processor, buffer):
        """Save processed image to TIFF format"""
        try:
            
            quality = int(processor.quality)
            
            # Convert array back to Pillow-compatible format
            if processor.image_array.shape[2] == 4:
                rgb_array = cv2.cvtColor(processor.image_array, cv2.COLOR_BGRA2RGBA)
            else:
                rgb_array = cv2.cvtColor(processor.image_array, cv2.COLOR_BGR2RGB)
            
            pil_image = Image.fromarray(rgb_array)
            
            
            # Determine compression method
            processor.compression_method = self._determine_compression(processor)
            
            if processor.compression_method.lower() == 'zstd' and not self.zstd_supported:
                logger.warning("ZSTD requested but not available, using DEFLATE")
                processor.compression_method = 'deflate'
            
            # Prepare save parameters
            save_params = {
                'compression': processor.compression_method,
                'icc_profile': processor.icc_profile
            }

            # Handle quality for lossy formats
            if processor.compression_method in ['jpeg', 'webp']:
                save_params['quality'] = quality
            
            # print save_params except icc_profile
            # a = {k: v for k, v in save_params.items() if k != 'icc_profile'}
            
            
            print(f"Trying to save with: {processor.compression_method} compression method, {'zstd supported' if self.zstd_supported else 'zstd not supported'}")


            # Save with selected parameters
            pil_image.save(buffer, format='TIFF', **save_params)
            processor.output_buffer = buffer.getvalue()
            
            print(f"Successfully saved with: {processor.compression_method} compression method")

        except Exception as e:
            raise Exception(f"TIFF save failed: {str(e)}")
        
    
    def _determine_compression(self, processor):
        """Auto-select compression based on image characteristics"""
        
        if processor.compression_method.lower() != 'auto':
            return processor.compression_method

        if processor.has_transparency:
            return 'deflate'  # Best lossless with alpha support

        for threshold in sorted(self._COMPRESSION_MAP['quality_based'].keys(), reverse=True):
            if processor.quality >= threshold:
                
                return self._COMPRESSION_MAP['quality_based'][threshold]
                
        return 'none'