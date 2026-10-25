"""
Sélection des feuilles DS Comptable à analyser et du taux RS de chacune
(fonctionnalité "plusieurs feuilles DS, une par taux, fusionnées dans un même
rapport"). Ne s'ouvre que si le classeur DS contient plusieurs feuilles — un
classeur à une seule feuille garde le comportement historique (taux unique
choisi dans la fenêtre principale).

Chaque feuille reste analysée séparément avec son propre taux (mêmes hard
rules que l'analyse mono-feuille), puis les résultats sont fusionnés par
core/reconciliation.py::fusionner_analyses.
"""

import re

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QDialogButtonBox, QMessageBox,
)
from PySide6.QtCore import Qt

from core.rules import CATALOGUE_TAUX_RS, parser_taux_saisi


def lire_taux_combo(combo: QComboBox) -> float:
    """Taux d'une liste déroulante éditable du catalogue : valeur exacte si une entrée est sélectionnée, sinon texte libre."""
    data = combo.currentData()
    if data is not None and combo.currentText() == combo.itemText(combo.currentIndex()):
        return float(data)
    return parser_taux_saisi(combo.currentText())


def creer_combo_taux(taux_defaut: float = None) -> QComboBox:
    combo = QComboBox()
    combo.setEditable(True)
    for code, libelle, taux in CATALOGUE_TAUX_RS:
        combo.addItem(f"{code} — {libelle} ({taux * 100:.2f}%)", taux)
    if taux_defaut is not None:
        idx = next((i for i in range(combo.count()) if abs(combo.itemData(i) - taux_defaut) < 1e-9), -1)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(f"{taux_defaut * 100:g}%")
    return combo


def deviner_taux_feuille(nom_feuille: str):
    """Taux lisible dans le nom de la feuille (ex: "RS 1%", "Loyers 10 %", "taux 1,5%") ou None."""
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*%', nom_feuille)
    if not m:
        return None
    return float(m.group(1).replace(',', '.')) / 100


class SheetTauxDialog(QDialog):
    def __init__(self, sheet_names: list, taux_defaut: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Feuilles du fichier DS Comptable")
        self.resize(720, 360)
        self.sheet_names = sheet_names
        self.combos = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Le fichier DS Comptable contient plusieurs feuilles. Cochez celles à analyser et "
            "indiquez le taux RS de chacune :\nchaque feuille est rapprochée séparément avec son "
            "taux, puis tous les résultats sont regroupés dans un même rapport."
        ))

        self.table = QTableWidget(len(sheet_names), 2)
        self.table.setHorizontalHeaderLabels(["Feuille", "Taux de retenue"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)

        for row, nom in enumerate(sheet_names):
            item = QTableWidgetItem(nom)
            item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, 0, item)
            combo = creer_combo_taux(deviner_taux_feuille(nom) or taux_defaut)
            self.table.setCellWidget(row, 1, combo)
            self.combos.append(combo)

        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.selection = []

    def _on_accept(self):
        selection = []
        for row, nom in enumerate(self.sheet_names):
            if self.table.item(row, 0).checkState() != Qt.CheckState.Checked:
                continue
            try:
                selection.append((nom, lire_taux_combo(self.combos[row])))
            except ValueError as e:
                QMessageBox.warning(self, "Taux invalide", f"Feuille « {nom} » : {e}")
                return
        if not selection:
            QMessageBox.warning(self, "Aucune feuille", "Cochez au moins une feuille à analyser.")
            return
        self.selection = selection
        self.accept()

    def get_selection(self) -> list:
        """[(nom_feuille, taux_fraction), ...] dans l'ordre du classeur."""
        return self.selection
