import os

files_to_fix = [
    "app/modules/web_scraper/application/analysis_service.py",
    "app/modules/web_scraper/infrastructure/scraper.py",
    "app/modules/web_scraper/infrastructure/session_store.py",
    "app/modules/web_scraper/infrastructure/repository.py",
    "app/modules/web_scraper/infrastructure/resilience.py",
    "app/modules/web_scraper/presentation/routes.py"
]

for filepath in files_to_fix:
    with open(filepath, "r") as f:
        content = f.read()
    
    # Remove any stray structlog imports the user added
    content = content.replace("import structlog\n", "")
    content = content.replace("# pyrefly: ignore [missing-import]\n", "")
    
    # Replace the initialization
    content = content.replace(
        "log = structlog.get_logger(__name__)",
        "from app.core.logging import get_logger\nlog = get_logger(__name__)"
    )
    
    with open(filepath, "w") as f:
        f.write(content)

print("Fixed loggers")
