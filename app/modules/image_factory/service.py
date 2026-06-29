# ─────────────────────────────────────────────────────
# Module   : app.modules.image_factory.service
# ─────────────────────────────────────────────────────
import os
import io
import re
import uuid
import zipfile
import asyncio
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor

from fastapi import UploadFile, Request
from app.core.config import settings
from app.core.logging import get_logger
from app.core.errors import AppError

from .schemas import ProcessRequestForm
from .processors.core.image_processor import ImageProcessor
from .processors.strategies.resize_operation import ResizeOperator
from .processors.strategies.convert_format_operation import FormatConverter
from .processors.strategies.adjust_quality_operation import QualityOptimizer, SizeOptimizer

logger = get_logger(__name__)

# Reusable executor to bound concurrency
executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS)

async def _async_cleanup_file(file_path: str, delay: int):
    """Async wrapper to avoid blocking or orphaned threads for file cleanup."""
    await asyncio.sleep(delay)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up {file_path}")
    except OSError as e:
        logger.error(f"Error deleting {file_path}: {e}")

def _process_single(file_content: bytes, filename: str, form_data: ProcessRequestForm):
    try:
        input_buffer = io.BytesIO(file_content)

        with ImageProcessor(input_buffer) as processor:
            processor = processor.add_operation(FormatConverter(form_data.output_format))

            if form_data.width or form_data.height:
                processor = processor.add_operation(ResizeOperator(width=form_data.width, height=form_data.height))

            if form_data.output_max_size:
                processor = processor.add_operation(SizeOptimizer(form_data.output_max_size, form_data.output_format))
            elif form_data.quality is not None:
                processor = processor.add_operation(QualityOptimizer(form_data.quality))

            processor = processor.execute().save()
            output_content = processor.output_buffer

        # sanitize filename
        safe_base = re.sub(r"[^a-zA-Z0-9_.-]", "_", os.path.splitext(filename)[0])
        safe_name = f"{safe_base}.{form_data.output_format.lower()}"
        return safe_name, output_content
    except Exception as e:
        logger.error(f"Failed to process {filename}: {str(e)}", exc_info=True)
        return None

async def process_images_orchestrator(request: Request, images: List[UploadFile], form_data: ProcessRequestForm) -> dict:
    file_id = str(uuid.uuid4())
    zip_path = os.path.join(settings.ZIP_STORAGE, f"{file_id}.zip")

    # Read all files into memory asynchronously to avoid holding up the event loop during file I/O
    file_contents = []
    for f in images:
        content = await f.read()
        file_contents.append((content, f.filename))

    loop = asyncio.get_running_loop()
    
    # Process images in executor
    tasks = [
        loop.run_in_executor(executor, _process_single, content, filename, form_data)
        for content, filename in file_contents
    ]
    
    results = await asyncio.gather(*tasks)

    # Write to zip (also blockingly, so run in executor)
    def write_zip(results_list, path):
        written = 0
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for result in results_list:
                if result:
                    filename, content = result
                    zipf.writestr(filename, content)
                    written += 1
        return written
        
    written_count = await loop.run_in_executor(executor, write_zip, results, zip_path)

    if written_count == 0:
        if os.path.exists(zip_path):
            os.remove(zip_path)
        raise AppError("No files were processed successfully.", status_code=400)

    # Schedule async cleanup
    asyncio.create_task(_async_cleanup_file(zip_path, settings.ZIP_EXPIRES_IN))
    
    # Build dynamic download URL
    download_link = f"{str(request.base_url).rstrip('/')}{settings.api_base}/image-factory/download/{file_id}"

    return {
        "id": file_id,
        "download_link": download_link,
        "expires_in": settings.ZIP_EXPIRES_IN
    }

def get_zip_path(file_id: str) -> Optional[str]:
    zip_path = os.path.join(settings.ZIP_STORAGE, f"{file_id}.zip")
    return zip_path if os.path.exists(zip_path) else None
