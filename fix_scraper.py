import os
import re

scraper_dir = "app/modules/web_scraper"

for root, _, files in os.walk(scraper_dir):
    for f in files:
        if f.endswith(".py"):
            path = os.path.join(root, f)
            with open(path, "r") as file:
                content = file.read()
            
            # 1. Update internal absolute imports
            content = re.sub(r'from (application|domain|infrastructure|presentation)\.', r'from app.modules.web_scraper.\1.', content)
            content = re.sub(r'import (application|domain|infrastructure|presentation)\.', r'import app.modules.web_scraper.\1.', content)

            # 2. Update settings imports
            content = re.sub(
                r'from app\.modules\.web_scraper\.application\.config import [Ss]ettings',
                r'from app.core.config import settings',
                content
            )
            content = re.sub(
                r'from app\.modules\.web_scraper\.application\.config import Settings',
                r'from app.core.config import Settings, settings',
                content
            )

            # 3. Update logging setup
            content = re.sub(
                r'import structlog\s*\n\s*log\s*=\s*structlog\.get_logger\(__name__\)',
                r'from app.core.logging import get_logger\nlog = get_logger(__name__)',
                content
            )
            content = re.sub(
                r'from app\.modules\.web_scraper\.infrastructure\.logging_config import get_logger\s*\n\s*log\s*=\s*get_logger\(__name__\)',
                r'from app.core.logging import get_logger\nlog = get_logger(__name__)',
                content
            )
            # Remove direct import of structlog if it's there
            content = re.sub(r'import structlog\n', '', content)
            
            # 4. ContextVars replacement for standard logging (best effort)
            # Find structlog.contextvars.bind_contextvars(...) and clear_contextvars()
            # This is tricky, but let's replace them with pass or a comment for now, 
            # and I'll update the logging calls to include `extra={}` manually.
            
            with open(path, "w") as file:
                file.write(content)
print("Finished rewriting")
