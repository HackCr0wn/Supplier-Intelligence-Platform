import os
from loguru import logger

os.makedirs("logs", exist_ok=True)

logger.add(
    "logs/pipeline.log",
    rotation="10 MB",
    retention="30 days",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
)