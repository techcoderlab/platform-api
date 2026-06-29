# ─────────────────────────────────────────────────────
# Module   : app.modules.image_factory.routes
# ─────────────────────────────────────────────────────
from fastapi import APIRouter, UploadFile, File, Form, Depends, Request, HTTPException
from fastapi.responses import FileResponse
from typing import List, Optional

from app.core.responses import success_response
from app.core.errors import NotFoundError, ValidationError
from .schemas import ProcessRequestForm, ImageProcessResponse
from .service import process_images_orchestrator, get_zip_path

router = APIRouter(tags=["Image Factory"])

def form_dependency(
    output_format: str = Form(...),
    output_max_size: Optional[int] = Form(None),
    quality: Optional[int] = Form(None),
    width: Optional[int] = Form(None),
    height: Optional[int] = Form(None)
) -> ProcessRequestForm:
    try:
        return ProcessRequestForm(
            output_format=output_format,
            output_max_size=output_max_size,
            quality=quality,
            width=width,
            height=height
        )
    except ValueError as e:
        raise ValidationError(str(e))

@router.post("/", response_model=ImageProcessResponse)
async def process_images_endpoint(
    request: Request,
    images: List[UploadFile] = File(...),
    form_data: ProcessRequestForm = Depends(form_dependency)
):
    """
    Process uploaded images (convert, resize, optimize).
    Returns a download link to a zip file containing the processed images.
    """
    if not images:
        raise ValidationError("No images provided.")
        
    result = await process_images_orchestrator(request, images, form_data)
    
    return success_response(
        data=result,
        request_id=getattr(request.state, "request_id", None)
    )

@router.get("/download/{file_id}")
async def download_images(file_id: str):
    """Serve a processed ZIP file for download."""
    zip_path = get_zip_path(file_id)
    if not zip_path:
        raise NotFoundError("File not found or expired")

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"processed_images_{file_id}.zip"
    )
