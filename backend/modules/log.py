"""
Module de logging centralisé.
Tous les autres modules importent `logger` depuis ici.

Formats :
  • Défaut              → texte lisible, couleur humaine (dev local).
  • LOG_JSON=true       → JSON une ligne par event, indexable par Loki/ELK.
"""
import json
import logging
import logging.handlers
import os

import config

# Crée le dossier logs s'il n'existe pas
os.makedirs(os.path.dirname(config.LOG_FILE) or "logs", exist_ok=True)

# ─────────────────────────────────────────────────────────────────
# FORMATTERS
# ─────────────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    """Formatter JSON une-ligne pour agrégateurs de logs (Loki, Elastic, Datadog).

    Émet les champs standards (ts, level, logger, module, msg) + toute
    `extra={}` fournie aux appels logger.*. Exception info placée dans
    `exc_info` sérialisée (str) pour rester grep-friendly.
    """

    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts":     self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":  record.levelname,
            "logger": record.name,
            "module": record.module,
            "msg":    record.getMessage(),
        }
        # Attacher les extra= passés aux appels logger.info("...", extra={...})
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                try:
                    json.dumps(value)  # skippe les non-sérialisables
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = repr(value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_JSON_MODE = os.getenv("LOG_JSON", "").lower() in {"1", "true", "yes"}

if _JSON_MODE:
    formatter: logging.Formatter = _JsonFormatter()
else:
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(module)-14s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

# ─────────────────────────────────────────────────────────────────
# LOGGER
# ─────────────────────────────────────────────────────────────────

logger = logging.getLogger("SwingAgent")
logger.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))

# Idempotence : si le module est ré-importé (ex. tests, reload uvicorn), on
# évite la duplication des handlers (sinon chaque log s'affiche N fois).
if not logger.handlers:
    fh = logging.handlers.RotatingFileHandler(
        config.LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)
