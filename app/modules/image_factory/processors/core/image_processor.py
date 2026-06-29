from PIL import Image
import io
import pillow_avif
import os
import numpy as np
import cv2
from typing import Optional, Tuple, Dict, Union
from PIL.TiffTags import TAGS_V2 as TIFF_TAGS

from app.modules.image_factory.processors.image_handlers.png_handler import PNGHandler
from app.modules.image_factory.processors.image_handlers.jpeg_handler import JPEGHandler
from app.modules.image_factory.processors.image_handlers.webp_handler import WEBPHandler
from app.modules.image_factory.processors.image_handlers.avif_handler import AVIFHandler
from app.modules.image_factory.processors.image_handlers.tiff_handler import TIFFHandler


from app.modules.image_factory.processors.strategies.image_operation import ConversionStrategy
from app.modules.image_factory.processors.image_handlers.format_handler import BaseFormatHandler


class ImageProcessor:
    __slots__ = ('image', 'image_array', 'output_format', 'quality', 
                 'compression_level', 'operations', 'handlers', 'output_buffer','_current_handler','color_space','icc_profile', '_original_dtype','compression_method')

    def __init__(self, image_source: Union[str, io.BytesIO]):
        self.image = self._load_image(image_source)
        self.icc_profile = self.image.info.get('icc_profile')  # Capture profile
        
        self.image_array = np.asarray(self.image)
        self.output_format = None
        self.quality = 98
        self.compression_level = 6
        self.operations = []
        self.handlers = self._init_handlers()
        self.output_buffer = None
        self._current_handler = None
        self.color_space = 'RGB'  # Default assumption
        self.compression_method = 'auto'  # Default value
         
        
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def _init_handlers(self) -> Dict[str, BaseFormatHandler]:
        return {
            'JPEG': JPEGHandler(),
            'PNG': PNGHandler(),
            'WEBP': WEBPHandler(),
            'AVIF': AVIFHandler(),
            'TIFF': TIFFHandler()
        }
        
    def _get_handler(self) -> BaseFormatHandler:
        if self.output_format not in self.handlers:
            raise ValueError(f"Unsupported format: {self.output_format}")
        return self.handlers[self.output_format]

    def _load_image(self, source) -> Image.Image:
        if isinstance(source, str):
            if not os.path.exists(source):
                raise FileNotFoundError(f"File not found: {source}")
            return Image.open(source)
        elif isinstance(source, io.BytesIO):
            source.seek(0)
            return Image.open(source)
        else:
            raise ValueError("Invalid image source type")

    def add_operation(self, operation: ConversionStrategy) -> 'ImageProcessor':
        self.operations.append(operation)
        return self

    def execute(self) -> 'ImageProcessor':
        for op in self.operations:
            op.execute(self)
        return self


    def save(self) -> "ImageProcessor":
        """Optimized save with quality integration"""
        
        if self._current_handler is not None:
            buffer = io.BytesIO()
            self._current_handler.save(self, buffer)
        
        return self

    @property
    def has_transparency(self) -> bool:
        # Check Pillow metadata
        pillow_alpha = self.image.mode in {'RGBA', 'LA', 'PA'} or \
                    (self.image.mode == 'P' and 'transparency' in self.image.info)
        
        # Check OpenCV array
        array_alpha = (
            hasattr(self, 'image_array') and 
            self.image_array.shape[2] == 4  # 4th channel exists
        )
        
        # Enhanced TIFF alpha detection
        tiff_alpha = (
            self.image.format == 'TIFF' and (
                self.image.mode in {'RGBa', 'La', 'RGBA', 'LA'} or
                self.image.tag_v2.get(338, 0) is not None or  # EXTRASAMPLES tag
                self.image.tag_v2.get(284, 0) == 2  # Planar configuration
            )
        )
        
        return pillow_alpha or array_alpha or tiff_alpha
    
    
    def quality_percentage(self, min_val, max_val):
        """Calculates a value within a given range based on the quality percentage."""

        if self.output_format == 'PNG':
            percentage = self.compression_level
        else:
            percentage = self.quality

        range_size = max_val - min_val
        percentage_decimal = percentage / 100.0  # Ensure float division
        result = min_val + (range_size * percentage_decimal)

        return np.clip(result, min_val, max_val)
        