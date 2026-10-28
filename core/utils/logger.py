import os
import sys
from loguru import logger
from core.utils.config import PROJECT_ROOT

def setup_logger():
    """Configure loguru for console and file output"""
    # Create logs directory in project root
    log_dir = os.path.join(PROJECT_ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, "biyou.log")
    
    # Remove default handler
    logger.remove()
    
    # 1. Console Handler (Simplified for user visibility)
    # Using a clean format for general usage, keeping colors
    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
        level="INFO",
        colorize=True
    )
    
    # 2. File Handler (Detailed for debugging)
    logger.add(
        log_file,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        retention="1 week",
        compression="zip",
        encoding="utf-8"
    )
    
    return logger

# Initialize as a singleton-like instance for the project
bi_logger = setup_logger()
# Export logger directly for easier usage
log = bi_logger
