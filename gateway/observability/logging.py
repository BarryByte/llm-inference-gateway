"""Structured JSON logging setup. Import configure_logging() in main.py startup."""
import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
    )
    logging.basicConfig(level=level, stream=sys.stdout)
