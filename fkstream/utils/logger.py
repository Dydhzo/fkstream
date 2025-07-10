import sys
import logging
import os

from loguru import logger
from .models import settings
def setupLogger():
    """
    Configure le logger Loguru avec des niveaux personnalisés, des formats et des couleurs.
    Le niveau de log est déterminé par la variable d'environnement LOG_LEVEL ou par défaut.
    """
    log_level = os.getenv("LOG_LEVEL")
    
    if not log_level:
        try:
            log_level = getattr(settings, 'LOG_LEVEL', 'DEBUG')
        except (ImportError, AttributeError):
            log_level = "DEBUG"
    
    logger.level("FKSTREAM", no=50, icon="🚀", color="<fg #7871d6>")
    logger.level("API", no=45, icon="👾", color="<fg #006989>")
    logger.level("SCRAPER", no=40, icon="👻", color="<fg #d6bb71>")
    logger.level("STREAM", no=35, icon="🎬", color="<fg #d171d6>")
    logger.level("LOCK", no=30, icon="🔒", color="<fg #71d6d6>")
    logger.level("INFO", icon="📰", color="<fg #FC5F39>")
    logger.level("DEBUG", icon="🕸️", color="<fg #DC5F00>")
    logger.level("WARNING", icon="⚠️", color="<fg #DC5F00>")
    logger.level("ERROR", icon="❌", color="<fg #FF0000>")

    if log_level == "PRODUCTION":
        log_format = "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | <level>{level}</level> | <level>{message}</level>"
        actual_level = "WARNING"
    else:
        log_format = (
            "<white>{time:YYYY-MM-DD}</white> <magenta>{time:HH:mm:ss}</magenta> | "
            "<level>{level.icon}</level> <level>{level}</level> | "
            "<cyan>{module}</cyan>.<cyan>{function}</cyan> - <level>{message}</level>"
        )
        actual_level = "DEBUG"

    logger.remove()
    logger.add(
        sys.stderr,
        level=actual_level,
        format=log_format,
        backtrace=False,
        diagnose=False,
        enqueue=True,
    )

    if log_level == "PRODUCTION":
        logger.warning(f"🏭 MODE PRODUCTION - Niveau de log: {actual_level}")
    else:
        logger.info(f"🛠️ MODE DEBUG - Niveau de log: {actual_level}")

setupLogger()
