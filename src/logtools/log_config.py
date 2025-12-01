from pathlib import Path
import logging.config

def get_logging_config(dir_output: str, base_file: str) -> dict:
    """Generates a logging configuration dictionary for the application.
    Creates the output directory if it does not exist and configures file and stdout handlers.

    Args:
        dir_output: Directory where log files will be stored.
        base_file: Name of the log file.

    Returns:
        dict: Logging configuration dictionary compatible with logging.config.dictConfig.
    """
    path_output = Path(dir_output)
    # Ensure directory exists – create if necessary
    path_output.mkdir(parents=True, exist_ok=True)

    path_json = path_output / base_file

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "format": "%(asctime)s %(levelname)s %(message)s %(module)s %(funcName)s %(process)d",
                "class": "pythonjsonlogger.jsonlogger.JsonFormatter",
            },
            "colored": {
                "format": "\033[1m%(levelname)s\033[0m: %(message)s | \033[1mBestand:\033[0m '%(module)s' | \033[1mFunctie:\033[0m '%(funcName)s'",
                "()": "logtools.color_formatter.ColorFormatter",
            },
        },
        "handlers": {
            "tqdm_stdout": {
                "class": "logtools.tqdm_logging.TqdmLoggingHandler",  # Gebruik het juiste pad
                "formatter": "colored",
                "level": "WARNING",  # of een andere drempel
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "json",
                "filename": str(path_json),
                "maxBytes": 204800,
                "backupCount": 10,
            },
        },
        "loggers": {
            "": {"handlers": ["tqdm_stdout", "file"], "level": "WARNING"}
        },
    }

def setup_logging(dir_output: str, base_file: str, log_level: str = "INFO") -> None:
    """Configures the root logger for the application.
    Sets up logging handlers, formatters, and log level for consistent application logging.

    Args:
        dir_output: Directory where log files will be stored.
        base_file: Name of the log file.
        log_level: Logging level to set for the root logger.

    Returns:
        None
    """
    root = logging.getLogger()

    # Avoid reconfiguring only if WE already configured it
    if getattr(root, "_configured_by_app", False):
        return

    # Clear any handlers (Werkzeug, debug mode, etc.)
    for h in root.handlers[:]:
        root.removeHandler(h)

    config = get_logging_config(dir_output=dir_output, base_file=base_file)
    logging.config.dictConfig(config)

    root.setLevel(log_level.upper())
    root._configured_by_app = True