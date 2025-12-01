import logging
import csv


class IssueTrackingHandler(logging.Handler):
    """A logging handler that tracks issues with severity WARNING or higher.
    Collects log records as issues and provides methods to export or query them.
    """

    def __init__(self):
        super().__init__()
        self.issues = []

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record.

        Args:
            record: The log record to emit.
        """
        if record.levelno >= logging.WARNING:
            self.issues.append(
                {
                    "severity": record.levelname,
                    "message": record.getMessage(),
                    "module": record.module,
                    "line": record.lineno,
                    "func": record.funcName,
                }
            )

    def max_severity_level(self) -> str:
        if self.issues:
            return min(self.issues, key=lambda x: x["severity"])["severity"]

    def has_errors(self) -> bool:
        """Checks if errors were logger

        Retourneert:
            True if errors logged else False.
        """
        return self.max_severity_level() == "ERROR"

    def get_issues(self) -> list:
        """Retrieves list of issues

        Returns:
            List of dictionaries
        """
        return self.issues

    def write_csv(self, file_csv: str) -> None:
        """Exports logged issues to a CSV file.

        Args:
            file_csv: The location of the CSV file
        """
        if self.issues:
            with open(file_csv, "w", encoding="utf8", newline="") as output_file:
                fc = csv.DictWriter(
                    output_file,
                    fieldnames=self.issues[0].keys(),
                    dialect="excel",
                    quoting=csv.QUOTE_STRINGS,
                )
                fc.writeheader()
                fc.writerows(self.issues)
        else:
            print(
                "No issues to write"
            )
