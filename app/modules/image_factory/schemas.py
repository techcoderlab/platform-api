# ─────────────────────────────────────────────────────
# Module   : app.modules.image_factory.schemas
# ─────────────────────────────────────────────────────
from typing import Optional, List
from pydantic import BaseModel, validator, model_validator
from fastapi import UploadFile

class ImageProcessResponse(BaseModel):
    id: str
    download_link: str
    expires_in: int

class ProcessRequestForm(BaseModel):
    output_format: str
    output_max_size: Optional[int] = None
    quality: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    
    @validator("output_format")
    def validate_format(cls, v):
        valid_formats = ["jpeg", "jpg", "png", "webp", "avif", "tiff", "tif"]
        if v.lower() not in valid_formats:
            raise ValueError(f"Unsupported format. Must be one of {valid_formats}")
        return v.lower()

    @model_validator(mode='after')
    def validate_quality_and_size(self):
        if self.output_max_size is not None and self.quality is not None:
            raise ValueError("output_max_size and quality cannot be specified together.")
        if self.output_max_size is None and self.quality is None:
            raise ValueError("Either output_max_size or quality must be specified.")
        
        if self.quality is not None and not (0 <= self.quality <= 100):
             raise ValueError("Quality must be between 0 and 100")
             
        return self
