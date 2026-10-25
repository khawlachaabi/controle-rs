"""
Consultation et contrôle humain du Supplier Registry (décision métier
confirmée, "validation fournisseur persistante et propagation automatique",
§7) : affiche chaque alias validé (fournisseur canonique / alias / source),
avec la possibilité d'annuler UNE validation précise sans jamais toucher au
fournisseur canonique ni à ses autres alias (core/supplier_registry.py::remove_alias).

Lecture seule sur le pipeline de rapprochement lui-même : ce dialogue ne
fait qu'exposer/corriger le référentiel déjà utilisé par
core/scorer.py::supplier_score — aucune nouvelle règle de matching ici.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
)

from core.supplier_registry import SupplierRegistry


class SupplierRegistryDialog(QDialog):
    def __init__(self, registry: SupplierRegistry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.setWindowTitle("Référentiel Fournisseurs (Supplier Registry)")
        self.resize(700, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Alias fournisseurs validés humainement — réutilisés automatiquement pour reconnaître "
            "les mêmes fournisseurs dans cette analyse et les analyses futures."
        ))

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Fournisseur canonique", "Alias validé", "Source"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.btn_supprimer = QPushButton("Annuler la validation sélectionnée")
        self.btn_supprimer.clicked.connect(self._supprimer_selection)
        self.btn_fermer = QPushButton("Fermer")
        self.btn_fermer.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_supprimer)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_fermer)
        layout.addLayout(btn_layout)

        self._recharger()

    def _recharger(self):
        lignes = self.registry.list_all()
        self.table.setRowCount(0)
        for row_idx, ligne in enumerate(lignes):
            self.table.insertRow(row_idx)
            self.table.setItem(row_idx, 0, QTableWidgetItem(ligne["canonical"]))
            self.table.setItem(row_idx, 1, QTableWidgetItem(ligne["alias"]))
            self.table.setItem(row_idx, 2, QTableWidgetItem(ligne["source"]))

    def _supprimer_selection(self):
        lignes_selectionnees = self.table.selectionModel().selectedRows()
        if not lignes_selectionnees:
            QMessageBox.information(self, "Aucune sélection", "Sélectionnez d'abord une ligne à annuler.")
            return

        row_idx = lignes_selectionnees[0].row()
        canonical = self.table.item(row_idx, 0).text()
        alias = self.table.item(row_idx, 1).text()

        confirmation = QMessageBox.question(
            self, "Confirmer l'annulation",
            f"Annuler la validation :\n« {alias} » = « {canonical} » ?\n\n"
            "Seule cette relation précise sera retirée — le fournisseur canonique et ses autres "
            "alias validés restent inchangés.",
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        self.registry.remove_alias(canonical, alias, persist=True)
        self._recharger()
