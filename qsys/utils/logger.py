import sys
from loguru import logger
from qsys.config.manager import cfg

def setup_logger():
    level = cfg.get("log_level", "INFO")
    logger.remove()
    logger.add(sys.stderr, level=level, format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")
    return logger

log = setup_logger()
