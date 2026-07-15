from __future__ import annotations
import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logging(name: str = "app") -> logging.Logger:
    """
    Configure a rotating file logger + console logger (idempotent).
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    os.makedirs("logs", exist_ok=True)

    try:
        file_handler = RotatingFileHandler(
            filename=os.path.join("logs", "app.log"),
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
            delay=True  # Delay file creation until first write
        )
    except PermissionError:
        # If file is locked, try using a different name
        import time
        timestamp = int(time.time())
        file_handler = RotatingFileHandler(
            filename=os.path.join("logs", f"app_{timestamp}.log"),
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
            delay=True
        )
    
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(console)
    logger.propagate = False
    return logger