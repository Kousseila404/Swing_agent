"""
Module de logging centralisé.
Tous les autres modules importent `logger` depuis ici.
"""
import logging
import os
import config

# Crée le dossier logs s'il n'existe pas
os.makedirs(os.path.dirname(config.LOG_FILE) or "logs", exist_ok=True)

# Configuration du logger
logger = logging.getLogger("SwingAgent")
logger.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))

# Format lisible
formatter = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(module)-14s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Handler fichier (historique)
fh = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
fh.setFormatter(formatter)
logger.addHandler(fh)

# Handler console (temps réel)
ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)
