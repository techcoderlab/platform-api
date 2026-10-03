# ─────────────────────────────────────────────────────
# Module   : app.core.logging
# ─────────────────────────────────────────────────────
import logging
import sys
import json
from datetime import datetime
from app.core.config import settings

# Attributes every LogRecord carries; anything else was passed via `extra=`
_STANDARD_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "request_id"}

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
        # `extra={...}` kwargs land as record attributes, not as `record.extra`
        context = {
            key: value for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_ATTRS
        }
        if context:
            log_data["context"] = context

        return json.dumps(log_data, default=str)

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
