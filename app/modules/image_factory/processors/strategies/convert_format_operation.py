from .image_operation import ConversionStrategy
from ..core.image_processor import ImageProcessor

class FormatConverter(ConversionStrategy):
    def __init__(self, output_format: str):
        self.format = output_format.upper()
        
    def execute(self, processor: ImageProcessor):
        self.convert_to(processor=processor)
          
    def convert_to(self, processor: ImageProcessor) -> "ImageProcessor":
        
        processor.output_format = self.format.upper()
        processor._current_handler = processor._get_handler()
        processor._current_handler.process(processor)
