import sys
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

# Importation de l'interface et des styles du projet
from ui.main_window import MainWindow
from ui.styles import QSS_THEME
from utils.journal import configurer_journal, logger


def get_asset_path(relative_path: str) -> str:
    """
    Gère la résolution du chemin vers les ressources (icônes, images)
    compatible à la fois en mode développement et une fois compilé avec PyInstaller (.exe).
    """
    if hasattr(sys, '_MEIPASS'):
        # Mode binaire compilé PyInstaller
        return os.path.join(sys._MEIPASS, relative_path)
    # Mode développement : relatif au dossier du projet (pas au dossier
    # courant, qui dépend de l'endroit d'où le script est lancé)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


def main():
    # 0. Journal fichier (%APPDATA%\ControleRS\controle_rs.log) — seule trace
    # disponible dans l'exécutable sans console (voir utils/journal.py)
    chemin_journal = configurer_journal()
    logger.info("Démarrage de l'application — journal : %s", chemin_journal)

    # 1. Initialisation de l'application Qt
    app = QApplication(sys.argv)
    
    # Nom de l'application (affiché dans le gestionnaire des tâches / OS)
    app.setApplicationName("Contrôle RS - ABC Audit")
    app.setOrganizationName("ABC Audit Comptable")

    # 2. Application du style graphique global (QSS)
    app.setStyle("Fusion")  # Style de base uniforme sur tous les OS
    app.setStyleSheet(QSS_THEME)

    # 3. Chargement de l'icône de l'application (si le dossier assets existe)
    icon_path = get_asset_path("assets/icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # 4. Création et affichage de la fenêtre principale
    window = MainWindow()
    
    # Si une icône existe, on l'affecte aussi explicitement à la fenêtre
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))
        
    window.show()

    # 5. Lancement de la boucle d'événements
    sys.exit(app.exec())


if __name__ == "__main__":
    main()