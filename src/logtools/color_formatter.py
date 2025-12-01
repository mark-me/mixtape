import logging

class ColorFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': "\033[37m",      # Grijs
        'INFO': "\033[36m",       # Cyaan
        'WARNING': "\033[33m",    # Geel
        'ERROR': "\033[31m",      # Rood
        'CRITICAL': "\033[41m",   # Witte tekst op rode achtergrond
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)