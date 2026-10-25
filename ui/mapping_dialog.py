from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QComboBox, QPushButton, QFormLayout,
    QDialogButtonBox, QMessageBox,
)

# CHANGEMENT (décision métier confirmée, "je veux garder le contrôle humain
# sur le mapping des colonnes DS") : DS Comptable et TEJ n'ont pas les mêmes
# rôles à mapper (DS: 3 rôles obligatoires + quelques rôles optionnels
# indicatifs, TEJ: jusqu'à 6). Ce dialogue construit ses champs à partir des
# clés réellement présentes dans detected_mapping, PAS d'une liste fixe.
ROLE_LABELS = {
    "fournisseur": "Fournisseur / Libellé",
    "htva": "Montant HTVA",
    "tva": "Montant TVA",
    "ttc": "Montant TTC",
    "rs": "Montant RS",
    "date_creation": "Date de Saisie / Écriture",
    "date_paiement": "Date comptable / Paiement",
    "statut": "Statut de l'opération",
    "piece": "Pièce",
    "folio": "Folio",
    "type_operation": "Type d'opération (RS / DMI / RECTIF)",
}


class MappingDialog(QDialog):
    """
    Point de contrôle humain unique pour le mapping des colonnes d'un
    fichier (DS Comptable ou TEJ). Les fichiers DS proviennent de logiciels
    comptables différents, aux intitulés de colonnes trop variables pour
    qu'une détection automatique décide seule avec certitude — c'est
    pourquoi ce dialogue s'ouvre systématiquement pour le DS et reste
    l'unique décisionnaire : aucun mapping n'est appliqué sans confirmation
    humaine explicite, et le clic sur OK est bloqué (message "Mapping
    incomplet") tant qu'un champ obligatoire n'est pas sélectionné.
    """

    def __init__(self, df_columns: list[str], detected_mapping: dict, file_label: str, parent=None,
                 roles_requis: list = None):
        """
        detected_mapping : {role: colonne ou None} — une SUGGESTION indicative
        pour pré-remplir les listes déroulantes (voir core/ds_column_mapper.py
        côté DS), jamais appliquée sans que l'utilisateur valide ou corrige.
        roles_requis : rôles dont l'absence bloque la validation (OK) avec un
        message explicite ; les autres clés de detected_mapping restent
        modifiables mais optionnelles (ex: HTVA/TVA/TTC/Pièce/Folio côté DS).
        """
        super().__init__(parent)
        self.setWindowTitle(f"Validation des Colonnes - {file_label}")
        self.resize(440, 340)

        self.roles_requis = list(roles_requis) if roles_requis is not None else list(detected_mapping.keys())
        self.combos = {}
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            f"Veuillez sélectionner les colonnes correspondantes pour :\n<b>{file_label}</b>"
        ))

        form_layout = QFormLayout()

        for role in detected_mapping.keys():
            label_text = ROLE_LABELS.get(role, role.replace("_", " ").title())
            if role in self.roles_requis:
                label_text += "  *"
            combo = QComboBox()
            combo.addItems(["-- Ignorer / Non présent --"] + df_columns)

            # Pré-sélection : simple suggestion, l'utilisateur peut toujours
            # la corriger — aucune colonne n'est appliquée sans son accord.
            current_detected = detected_mapping.get(role)
            if current_detected in df_columns:
                combo.setCurrentText(current_detected)

            self.combos[role] = combo
            form_layout.addRow(label_text, combo)

        layout.addLayout(form_layout)

        if self.roles_requis:
            layout.addWidget(QLabel("* champ obligatoire"))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        """
        Bloque la fermeture tant qu'un champ obligatoire n'est pas
        sélectionné — c'est le SEUL contrôle automatique effectué ici
        (présence, pas pertinence : l'utilisateur reste seul juge de la
        colonne correcte).
        """
        manquants = [
            ROLE_LABELS.get(role, role)
            for role in self.roles_requis
            if self._valeur_selectionnee(role) is None
        ]
        if manquants:
            QMessageBox.warning(
                self,
                "Mapping incomplet",
                "⚠ Mapping incomplet\n\n"
                "Le(s) champ(s) suivant(s) doi(ven)t être sélectionné(s) avant de lancer le rapprochement :\n"
                + "\n".join(f"- {nom}" for nom in manquants),
            )
            return
        self.accept()

    def _valeur_selectionnee(self, role: str):
        combo = self.combos.get(role)
        if combo is None:
            return None
        selected = combo.currentText()
        return None if selected.startswith("--") else selected

    def get_validated_mapping(self) -> dict:
        return {role: self._valeur_selectionnee(role) for role in self.combos}
