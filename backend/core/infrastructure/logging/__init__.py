"""
Structured logging infrastructure
"""

import logging
import sys
from pythonjsonlogger import jsonlogger
from datetime import datetime
import os


def setup_logging():
    """Setup structured JSON logging"""
    # Get log level from environment, default to INFO
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    # Create formatter
    json_formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s %(funcName)s %(lineno)d",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level))

    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(json_formatter)

    # Add handler if not already added
    if not root_logger.handlers:
        root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the specified name"""
    return logging.getLogger(name)


def log_api_call(
    logger: logging.Logger,
    endpoint: str,
    method: str,
    user_id: int = None,
    org_id: int = None,
    response_time: float = None,
    status_code: int = None,
):
    """Log API call with structured data"""
    logger.info(
        "API call executed",
        extra={
            "event": "api_call",
            "endpoint": endpoint,
            "method": method,
            "user_id": user_id,
            "organization_id": org_id,
            "response_time_ms": response_time,
            "status_code": status_code,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


def log_scraping_attempt(
    logger: logging.Logger,
    url: str,
    method: str,
    success: bool,
    confidence: float,
    processing_time: float,
    error_message: str = None,
):
    """Log scraping attempt with structured data"""
    logger.info(
        f"Scraping {'successful' if success else 'failed'}",
        extra={
            "event": "scraping_attempt",
            "url": url,
            "method": method,
            "success": success,
            "confidence": confidence,
            "processing_time_ms": processing_time,
            "error_message": error_message,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


def log_enrichment_attempt(
    logger: logging.Logger,
    lead_id: int,
    method: str,
    success: bool,
    confidence: float,
    processing_time: float,
    error_message: str = None,
):
    """Log enrichment attempt with structured data"""
    logger.info(
        f"Enrichment {'successful' if success else 'failed'}",
        extra={
            "event": "enrichment_attempt",
            "lead_id": lead_id,
            "method": method,
            "success": success,
            "confidence": confidence,
            "processing_time_ms": processing_time,
            "error_message": error_message,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )
