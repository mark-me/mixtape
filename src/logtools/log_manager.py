import logging
from .issue_tracking import IssueTrackingHandler


# Set up issue tracker handler (shared across all modules)
issue_tracker = IssueTrackingHandler()

def get_logger(name: str | None = None) -> logging.Logger:
    """Returns a logger instance with an attached issue tracking handler.
    Ensures that the logger includes the IssueTrackingHandler for centralized issue reporting.

    Args:
        name: Optional name for the logger.

    Returns:
        logging.Logger: Logger instance with IssueTrackingHandler attached.
    """
    logger = logging.getLogger(name)

    if not any(isinstance(h, IssueTrackingHandler) for h in logger.handlers):
        logger.addHandler(issue_tracker)

    return logger
