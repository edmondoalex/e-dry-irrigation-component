from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_debug_logger() -> logging.Logger:
    """Configure and return a dedicated logger for the integration.

    This creates a rotating file handler at
    `<custom_components>/e_dry/e_dry_debug.log` and attaches it to the
    `custom_components.e_dry` logger. Safe to call multiple times.
    """
    base_name = "custom_components.e_dry"
    logger = logging.getLogger(base_name)

    # Ensure the log file lives next to this module so users can easily find it
    folder = os.path.dirname(__file__)
    log_path = os.path.join(folder, "e_dry_debug.log")

    # Avoid adding multiple handlers if already configured
    for h in list(logger.handlers):
        try:
            if isinstance(h, RotatingFileHandler) and os.path.abspath(getattr(h, "baseFilename", "")) == os.path.abspath(log_path):
                return logger
        except Exception:
            continue

    try:
        handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        handler.setFormatter(formatter)
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        # Keep logger at debug level so debug messages are captured
        logger.setLevel(logging.DEBUG)
    except Exception:
        # Best-effort: if we cannot create file handler, leave standard logging unchanged
        logging.getLogger(__name__).exception("setup_debug_logger: failed to create file handler")

    return logger
