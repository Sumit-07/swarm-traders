"""Logging configuration using Loguru.

Provides per-agent log files, trade logs, and error logs with rotation.
"""

import sys
from pathlib import Path
from loguru import logger

from config import LOGS_DIR, LOG_LEVEL

# Remove default handler
logger.remove()

# Console output
logger.add(
    sys.stderr,
    level=LOG_LEVEL,
    format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level:<8}</level> | <cyan>{extra[agent_id]:<20}</cyan> | {message}",
    filter=lambda record: "agent_id" in record["extra"],
)

# Fallback console for messages without agent_id
logger.add(
    sys.stderr,
    level=LOG_LEVEL,
    format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level:<8}</level> | {message}",
    filter=lambda record: "agent_id" not in record["extra"],
)

# Error log (all agents)
logger.add(
    str(LOGS_DIR / "error_logs" / "errors_{time:YYYY-MM-DD}.log"),
    level="ERROR",
    rotation="10 MB",
    retention="30 days",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {extra[agent_id]} | {message}",
    filter=lambda record: "agent_id" in record["extra"],
)

# Trade log
logger.add(
    str(LOGS_DIR / "trade_logs" / "trades_{time:YYYY-MM-DD}.log"),
    level="INFO",
    rotation="10 MB",
    retention="30 days",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {extra[agent_id]} | {message}",
    filter=lambda record: record["extra"].get("log_type") == "trade",
)


def get_agent_logger(agent_id: str):
    """Return a logger bound with the given agent_id.

    Also creates a per-agent log file.
    """
    # Per-agent daily log file
    logger.add(
        str(LOGS_DIR / "agent_logs" / f"{agent_id}_{{time:YYYY-MM-DD}}.log"),
        level="DEBUG",
        rotation="10 MB",
        retention="30 days",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {message}",
        filter=lambda record, aid=agent_id: record["extra"].get("agent_id") == aid,
    )
    return logger.bind(agent_id=agent_id)
