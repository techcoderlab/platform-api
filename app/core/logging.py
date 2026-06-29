# ─────────────────────────────────────────────────────
# Module   : app.core.logging
# ─────────────────────────────────────────────────────
import logging
import sys
import json
from datetime import datetime
from app.core.config import settings

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "service": settings.SERVICE_NAME,
            "message": record.getMessage(),
        }
        if hasattr(record, "request_id") and record.request_id:
            log_data["request_id"] = record.request_id
        if record.exc_info:
            log_data["traceback"] = self.formatException(record.exc_info)
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            log_data["context"] = record.extra
            
        return json.dumps(log_data)

def setup_logging():
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        
    console_handler = logging.StreamHandler(sys.stdout)
    
    if settings.LOG_FORMAT.lower() == "json":
        console_handler.setFormatter(JSONFormatter())
    else:
        # Dev fallback using rich if available, else basic
        try:
            from rich.logging import RichHandler
            console_handler = RichHandler(rich_tracebacks=True)
            console_handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
        except ImportError:
            console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s"))
            
    root_logger.addHandler(console_handler)
    
    # Set uvicorn loggers to use the same level
    logging.getLogger("uvicorn.access").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)

def get_logger(name: str):
    return logging.getLogger(name)
