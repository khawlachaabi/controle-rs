"""
Journal de l'application : %APPDATA%\\ControleRS\\controle_rs.log.

Dans l'exécutable (sans console), print() n'écrit nulle part — les
diagnostics ([FILE_SELECTED], [TEJ_PARSED], [PERIMETRE_DS]...) et les
erreurs étaient donc perdus. Ils sont désormais écrits dans ce fichier
(rotation à 1 Mo, 3 fichiers conservés), et aussi sur la console quand il y
en a une (mode développement).
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

NOM_LOGGER = "controle_rs"
logger = logging.getLogger(NOM_LOGGER)


def dossier_donnees() -> str:
    """Dossier des données utilisateur de l'application (journal, référentiel fournisseurs en mode exe)."""
    dossier = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "ControleRS")
    os.makedirs(dossier, exist_ok=True)
    return dossier


def configurer_journal() -> str:
    """Branche le journal fichier (+ console si disponible). Idempotent. Retourne le chemin du fichier."""
    chemin = os.path.join(dossier_donnees(), "controle_rs.log")
    if logger.handlers:
        return chemin

    logger.setLevel(logging.INFO)
    fichier = RotatingFileHandler(chemin, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fichier.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fichier)

    if sys.stdout is not None:
        try:
            sys.stdout.reconfigure(errors="replace")  # console Windows cp1252 : noms arabes, symboles ✓ ⚠
        except (AttributeError, ValueError):
            pass
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(console)

    def _exception_non_geree(exc_type, exc, tb):
        logger.critical("Exception non gérée", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _exception_non_geree
    return chemin
