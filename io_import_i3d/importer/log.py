import logging
import sys

_LOGGER_NAME = "io_import_i3d"


def get_logger(level: str = "INFO") -> logging.Logger:
    log = logging.getLogger(_LOGGER_NAME)
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("[i3d] %(levelname)s %(message)s"))
        log.addHandler(h)
        log.propagate = False
    log.setLevel(getattr(logging, level, logging.INFO))
    return log
