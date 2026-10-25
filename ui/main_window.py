import os
from collections import Counter
from datetime import datetime
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QRadioButton, QButtonGroup,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QFrame, QDialog, QInputDialog, QProgressDialog
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont

# Import des modules du cœur logique (Core & Utils)
from core.parser import (
    list_sheets, load_excel_sheet, map_tej_columns, choisir_feuille_par_sens,
    COLONNE_CONTREPARTIE_TEJ, ROLES_DS,
)
from core.ds_column_mapper import detect_ds_mapping, colonne_contredit_sens, RS_PATTERN_STR
from core.reconciliation import reconcile, resoudre_review, fusionner_analyses
from core.supplier_registry import SupplierRegistry
from utils.exporter import export_reconciliation_results, COLONNES_RAPPORT
from utils.journal import logger
from ui.mapping_dialog import MappingDialog
from ui.review_dialog import ReviewDialog
from ui.supplier_registry_dialog import SupplierRegistryDialog
from ui.sheet_taux_dialog import SheetTauxDialog, creer_combo_taux, lire_taux_combo


def _diag_print(message: str) -> None:
    """
    Trace diagnostique (traçabilité du chargement des fichiers et du
    périmètre d'analyse) — lecture seule, n'influence ni le mapping ni le
    rapprochement. CHANGEMENT : écrite dans le journal
    (utils/journal.py, %APPDATA%/ControleRS/controle_rs.log) et plus
    seulement via print(), qui n'écrit nulle part dans l'exécutable.
    """
    logger.info(message)


def _diag_file_selected(path: str) -> None:
    """[FILE_SELECTED] — trace le chemin absolu réellement retenu par le FileDropZone."""
    abspath = os.path.abspath(path)
    try:
        taille = os.path.getsize(abspath)
        mtime = datetime.fromtimestamp(os.path.getmtime(abspath)).strftime("%d/%m/%Y %H:%M:%S")
    except OSError as e:
        taille, mtime = f"introuvable ({e})", "introuvable"
    _diag_print(
        "[FILE_SELECTED]\n"
        f"  path            : {abspath}\n"
        f"  filename        : {os.path.basename(abspath)}\n"
        f"  size            : {taille} octets\n"
        f"  modification time : {mtime}"
    )


# CHANGEMENT (nouveau moteur de rapprochement bidirectionnel, voir
# core/reconciliation.py) : libellés lisibles en français pour l'affichage —
# les statuts internes du nouveau moteur (MATCH_CERTAIN, FISCAL_ONLY, etc.)
# restent utilisés pour la logique (couleurs, export), mais n'étaient pas
# clairs à lire tels quels dans le tableau. Remplace l'ancienne taxonomie
# CONFORME/AVERTISSEMENT/AMBIGU/ECART_ABSENT_*.
STATUT_LABELS = {
    "MATCH_CERTAIN": "✓ Correspondance certaine",
    "MATCH_PROBABLE": "✓ Correspondance probable",
    "REVIEW": "? À vérifier",
    "FISCAL_ONLY": "✗ Fiscal uniquement",
    "COMPTA_ONLY": "✗ Comptabilité uniquement",
    # CHANGEMENT (décision métier confirmée, "cas ANNULÉ/RECTIFIÉ") :
    # distinct de COMPTA_ONLY — un certificat TEJ correspondant EXISTE
    # (même montant, même fenêtre de délai) mais est ANNULÉ, ce qui est une
    # information différente d'une opération réellement absente côté TEJ.
    "CERTIFICAT_TEJ_ANNULE": "⚠ Certificat TEJ trouvé mais ANNULÉ",
    # CHANGEMENT (décision métier confirmée, "distinguer COMPTA_ONLY
    # réellement inexpliqué de HORS_TAUX_SELECTIONNE") : distinct de
    # COMPTA_ONLY — un certificat TEJ ACTIF correspondant EXISTE (même
    # montant, même fenêtre de délai) mais appartient à un autre taux RS que
    # celui sélectionné pour cette analyse ; ne pas le distinguer risquerait
    # de faire croire à une opération comptable absente de TEJ.
    "HORS_TAUX_SELECTIONNE": "↷ Hors du taux RS sélectionné",
    # CHANGEMENT (décision métier confirmée, "Chantier 3 — DMI/RECTIF et
    # filtrage métier", rectification sémantique demandée) : opération dont
    # le type (DMI, RECTIF côté DS, ou non reconnu) l'exclut VOLONTAIREMENT
    # du moteur RS AVANT même la génération de candidats — jamais un
    # COMPTA_ONLY/FISCAL_ONLY générique, qui laisserait croire à une
    # opération réellement absente de l'autre source. Ce n'est PAS une
    # anomalie : c'est une exclusion métier délibérée (l'opération n'a
    # simplement jamais vocation à être rapprochée par le moteur RS), donc
    # ni le symbole "✗"/"⊘" (réservés aux écarts réels) ni le mot
    # "anomalie" ne doivent lui être associés.
    "HORS_PERIMETRE_TYPE": "○ Hors périmètre RS (exclusion métier volontaire)",
}


class FileDropZone(QFrame):
    """Zone de glisser-déposer stylisée pour charger un fichier Excel."""
    
    def __init__(self, title: str):
        super().__init__()
        self.setAcceptDrops(True)
        self.file_path = None

        self.setStyleSheet("""
            QFrame {
                border: 2px dashed #A0AAB5;
                border-radius: 8px;
                background-color: #F8F9FA;
            }
            QFrame:hover {
                border-color: #1F4E79;
                background-color: #F0F4F8;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.label_title = QLabel(title)
        self.label_title.setFont(QFont("Calibri", 11, QFont.Weight.Bold))
        self.label_title.setStyleSheet("color: #1F4E79; border: none; background: transparent;")

        self.label_status = QLabel("Glissez-déposez un fichier .xlsx ici\nou cliquez pour parcourir")
        self.label_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_status.setFont(QFont("Calibri", 9))
        self.label_status.setStyleSheet("color: #6C757D; border: none; background: transparent;")

        self.btn_browse = QPushButton("Parcourir...")
        self.btn_browse.setStyleSheet("""
            QPushButton {
                background-color: #1F4E79;
                color: white;
                border: none;
                padding: 6px 14px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #153451;
            }
        """)
        self.btn_browse.clicked.connect(self.browse_file)

        layout.addWidget(self.label_title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label_status, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.btn_browse, alignment=Qt.AlignmentFlag.AlignCenter)

    def browse_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Sélectionner un fichier Excel", "", "Fichiers Excel (*.xlsx)")
        if file_path:
            self.set_file(file_path)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if file_path.endswith('.xlsx'):
                self.set_file(file_path)
                break

    def set_file(self, path: str):
        self.file_path = path
        _diag_file_selected(path)
        filename = os.path.basename(path)
        self.label_status.setText(f"✓ Fichier chargé :\n{filename}")
        self.label_status.setStyleSheet("color: #28A745; font-weight: bold; border: none; background: transparent;")


class AnalyseWorker(QThread):
    """
    Exécute le rapprochement (core/reconciliation.py::reconcile, une fois
    par feuille DS, puis fusionner_analyses) hors du fil de l'interface.
    Ne touche à aucun widget : communique uniquement par signaux, traités
    dans le fil principal par MainWindow.
    """
    progression = Signal(int, str)
    termine = Signal(object, object, int)  # (results, stats, nb_feuilles)
    echec = Signal(str)

    def __init__(self, taches: list, df_tej, map_tej: dict, registry: SupplierRegistry):
        super().__init__()
        self.taches = taches  # [(feuille, taux, df_ds, map_ds), ...]
        self.df_tej = df_tej
        self.map_tej = map_tej
        self.registry = registry

    def run(self):
        try:
            analyses = []
            for i, (feuille, taux, df_ds, map_ds) in enumerate(self.taches):
                self.progression.emit(i, f"Rapprochement de la feuille « {feuille} » ({i + 1}/{len(self.taches)})…")
                try:
                    results, stats = reconcile(df_ds, map_ds, self.df_tej, self.map_tej, taux, registry=self.registry)
                except ValueError as e:
                    raise ValueError(f"Feuille DS « {feuille} » : {e}") from e
                analyses.append((f"{feuille} ({taux * 100:.2f}%)", results, stats))
            self.progression.emit(len(self.taches), "Finalisation…")
            results, stats = fusionner_analyses(analyses)
            self.termine.emit(results, stats, len(analyses))
        except Exception as e:
            logger.exception("Erreur pendant le rapprochement")
            self.echec.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Contrôle des Retenues à la Source - ABC Audit")
        self.resize(1100, 700)

        self.results_cache = []
        # Référentiel fournisseurs persisté (Étape 4) : partagé entre les
        # analyses de cette session ET les sessions suivantes (fichier JSON
        # à la racine du projet), enrichi par les validations en ui/review_dialog.py.
        self.registry = SupplierRegistry()

        # Widget central et disposition principale
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(15)

        # 1. En-tête / Titre
        header_label = QLabel("Plateforme de Contrôle & Rapprochement des Retenues à la Source (RS)")
        header_label.setFont(QFont("Calibri", 14, QFont.Weight.Bold))
        header_label.setStyleSheet("color: #1F4E79; margin-bottom: 5px;")
        main_layout.addWidget(header_label)

        # 2. Choix du type d'opération (Achat vs Vente)
        type_box = QHBoxLayout()
        type_label = QLabel("Type d'opération à analyser :")
        type_label.setFont(QFont("Calibri", 10, QFont.Weight.Bold))
        
        self.radio_achat = QRadioButton("Achats (Émis - Payés mensuellement)")
        self.radio_vente = QRadioButton("Ventes (Reçus - Déduits en fin d'année)")
        self.radio_achat.setChecked(True)

        self.type_group = QButtonGroup()
        self.type_group.addButton(self.radio_achat)
        self.type_group.addButton(self.radio_vente)

        type_box.addWidget(type_label)
        type_box.addWidget(self.radio_achat)
        type_box.addWidget(self.radio_vente)
        type_box.addStretch()
        main_layout.addLayout(type_box)

        # 2bis. Taux de retenue du fichier DS Comptable chargé.
        # CHANGEMENT (décision métier confirmée, "correction ciblée — un
        # fichier DS ne contient qu'UN SEUL taux RS à la fois") : RESTAURÉE
        # après vérification du fonctionnement réel du logiciel. Le chantier
        # précédent ("gestion de plusieurs taux RS") avait supprimé cette
        # sélection en partant de l'hypothèse qu'un même fichier DS pouvait
        # mélanger plusieurs taux — hypothèse invalidée depuis : un fichier
        # DS réel ne contient qu'un seul taux, et l'utilisateur le
        # sélectionne ici pour toute l'analyse (core/rules.py::CATALOGUE_TAUX_RS,
        # ou saisie libre). Ce qui reste du chantier précédent : le calcul
        # informatif taux_tej/taux_ds (core/scorer.py), jamais un filtre ni
        # un poids — voir core/reconciliation.py::reconcile.
        taux_box = QHBoxLayout()
        taux_label = QLabel("Taux de retenue du fichier DS Comptable :")
        taux_label.setFont(QFont("Calibri", 10, QFont.Weight.Bold))

        self.combo_taux = creer_combo_taux()
        self.combo_taux.setMinimumWidth(400)
        self.combo_taux.setToolTip(
            "Choisissez une catégorie dans la liste, ou tapez directement un "
            "pourcentage (ex: 1 pour 1 %, ou 7,5) si votre catégorie n'y figure pas.\n"
            "Si le fichier DS contient plusieurs feuilles (une par taux), le "
            "taux de chaque feuille sera demandé au lancement de l'analyse."
        )

        taux_box.addWidget(taux_label)
        taux_box.addWidget(self.combo_taux, stretch=1)
        main_layout.addLayout(taux_box)

        # 3. Zone de dépôt des fichiers
        files_layout = QHBoxLayout()
        self.drop_ds = FileDropZone("1. Fichier DS Comptable")
        self.drop_tej = FileDropZone("2. Fichier Plateforme TEJ")
        files_layout.addWidget(self.drop_ds)
        files_layout.addWidget(self.drop_tej)
        main_layout.addLayout(files_layout)

        # 4. Boutons d'action
        actions_layout = QHBoxLayout()
        self.btn_analyze = QPushButton("⚡ Lancer le Rapprochement")
        self.btn_analyze.setFont(QFont("Calibri", 11, QFont.Weight.Bold))
        self.btn_analyze.setStyleSheet("""
            QPushButton {
                background-color: #28A745;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 5px;
            }
            QPushButton:hover { background-color: #218838; }
        """)
        self.btn_analyze.clicked.connect(self.run_reconciliation)

        self.btn_export = QPushButton("📥 Exporter le Rapport Excel")
        self.btn_export.setEnabled(False)
        self.btn_export.setFont(QFont("Calibri", 11, QFont.Weight.Bold))
        self.btn_export.setStyleSheet("""
            QPushButton {
                background-color: #17A2B8;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 5px;
            }
            QPushButton:hover { background-color: #138496; }
            QPushButton:disabled { background-color: #6C757D; opacity: 0.5; }
        """)
        self.btn_export.clicked.connect(self.export_results)

        # CHANGEMENT (Étape 9, review humaine) : ouvre ui/review_dialog.py
        # pour les correspondances au statut REVIEW — désactivé tant qu'aucune
        # analyse n'a été lancée ou qu'il n'y a rien à vérifier.
        self.btn_review = QPushButton("🔍 Vérifier les correspondances incertaines")
        self.btn_review.setEnabled(False)
        self.btn_review.setFont(QFont("Calibri", 11, QFont.Weight.Bold))
        self.btn_review.setStyleSheet("""
            QPushButton {
                background-color: #6F42C1;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 5px;
            }
            QPushButton:hover { background-color: #5A32A3; }
            QPushButton:disabled { background-color: #6C757D; opacity: 0.5; }
        """)
        self.btn_review.clicked.connect(self.open_review_dialog)

        # CHANGEMENT (décision métier confirmée, "validation fournisseur
        # persistante et propagation automatique", §7) : consultation/
        # contrôle humain du Supplier Registry — toujours activé (le
        # référentiel peut déjà contenir des alias d'analyses précédentes,
        # même avant tout chargement de fichier dans cette session).
        self.btn_registry = QPushButton("📇 Référentiel Fournisseurs")
        self.btn_registry.setFont(QFont("Calibri", 11, QFont.Weight.Bold))
        self.btn_registry.setStyleSheet("""
            QPushButton {
                background-color: #495057;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 5px;
            }
            QPushButton:hover { background-color: #343A40; }
        """)
        self.btn_registry.clicked.connect(self.open_supplier_registry_dialog)

        actions_layout.addWidget(self.btn_analyze)
        actions_layout.addWidget(self.btn_review)
        actions_layout.addWidget(self.btn_export)
        actions_layout.addWidget(self.btn_registry)
        main_layout.addLayout(actions_layout)

        # 5. Tableau des résultats prévisualisés
        # CHANGEMENT (nouveau moteur de rapprochement) : colonnes alignées
        # sur ReconciliationResult — deux dates et deux fournisseurs
        # distincts (fiscal/comptable), plus le score global pour
        # l'explicabilité (voir règles métier validées, §14).
        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(len(COLONNES_RAPPORT))
        self.table_widget.setHorizontalHeaderLabels(COLONNES_RAPPORT)
        self.table_widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        main_layout.addWidget(self.table_widget)

    def _lire_taux_cible(self) -> float:
        """
        Retourne le taux choisi (fraction décimale) — entrée du catalogue ou
        saisie libre lue comme un pourcentage (ex: "1" = 1 %, "7,5%"), voir
        ui/sheet_taux_dialog.py::lire_taux_combo (même règle partagée avec le
        choix des taux par feuille). Un taux nul ou négatif est refusé
        (recette de production : il produisait une analyse "valide" en
        apparence mais sans aucune correspondance possible).
        """
        return lire_taux_combo(self.combo_taux)

    def _sens(self) -> str:
        """Sens d'opération choisi : "achat" ou "vente" (voir core/parser.py et core/ds_column_mapper.py)."""
        return "achat" if self.radio_achat.isChecked() else "vente"

    # Champs obligatoires TEJ : fournisseur (supplier_score), date (fenêtre de
    # délai + date_score), montant RS (hard rule), statut (exclusion des
    # annulés) et HTVA (filtre de taux RS/HTVA appliqué par
    # core/reconciliation.py::reconcile — CORRECTION : ce commentaire
    # affirmait à tort que HTVA n'alimentait plus aucun filtre). Seul
    # date_creation reste optionnel (contrôle de délai de pénalité,
    # informatif, voir core/reconciliation.py::signaler_delais_paiement).
    ROLES_TEJ_OBLIGATOIRES = ["fournisseur", "htva", "rs", "date_paiement", "statut"]

    # Rôles optionnels affichés dans MappingDialog "si disponibles" (décision
    # confirmée) — jamais obligatoires, l'absence d'une colonne plausible ne
    # bloque rien. Suggestion indicative uniquement via core/ds_column_mapper.py :
    # le contrôle humain reste entier.
    ROLES_DS_OPTIONNELS = ["htva", "tva", "ttc", "piece", "folio", "type_operation"]

    def _choisir_feuilles_ds(self, path: str) -> list:
        """
        [(nom_feuille, taux), ...] à analyser. Classeur à une seule feuille :
        comportement historique, taux de la fenêtre principale. Plusieurs
        feuilles : SheetTauxDialog (une feuille par taux, fusion dans un même
        rapport — voir core/reconciliation.py::fusionner_analyses).
        """
        feuilles = list_sheets(path)
        if len(feuilles) == 1:
            return [(feuilles[0], self._lire_taux_cible())]
        try:
            taux_defaut = self._lire_taux_cible()
        except ValueError:
            taux_defaut = None
        dialog = SheetTauxDialog(feuilles, taux_defaut, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            raise ValueError("Sélection des feuilles DS annulée par l'utilisateur.")
        return dialog.get_selection()

    def _load_ds(self, path: str, feuille, mapping_precedent: dict = None):
        """
        Charge UNE feuille du fichier DS Comptable. Le mapping des colonnes
        reste TOUJOURS sous contrôle humain explicite (MappingDialog) —
        décision confirmée : les fichiers DS proviennent de logiciels
        comptables différents, aux intitulés trop variables pour qu'une
        détection automatique décide seule avec certitude. Seule la PRÉSENCE
        des 3 champs obligatoires (fournisseur, montant RS, date — voir
        core/parser.py::ROLES_DS) est vérifiée, jamais leur pertinence.

        mapping_precedent : mapping déjà validé par l'utilisateur pour une
        autre feuille du même classeur. S'il ne référence que des colonnes
        présentes dans cette feuille (même structure, cas normal d'un
        classeur "une feuille par taux"), il est réutilisé sans redemander.

        Câblage achat/vente : la suggestion de la colonne montant RS suit le
        sens choisi (Crédit = achats, Débit = ventes, voir
        core/ds_column_mapper.py::MOT_COLONNE_SENS), et une colonne du sens
        opposé demande confirmation explicite.
        """
        df = load_excel_sheet(path, feuille)
        sens = self._sens()

        if mapping_precedent and all(c is None or c in df.columns for c in mapping_precedent.values()):
            mapping = mapping_precedent
        else:
            suggestion = detect_ds_mapping(df, roles=ROLES_DS + self.ROLES_DS_OPTIONNELS, sens=sens)
            mapping_suggere = {
                role: col for role, col in suggestion.items()
                if role in ROLES_DS or col is not None
            }
            dialog = MappingDialog(list(df.columns), mapping_suggere, f"DS Comptable — feuille « {feuille} »",
                                   self, roles_requis=ROLES_DS)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                raise ValueError("Mapping annulé par l'utilisateur pour le fichier DS Comptable.")
            mapping = dialog.get_validated_mapping()

            if colonne_contredit_sens(mapping.get("rs"), sens):
                libelle_sens = "achats (écritures au crédit)" if sens == "achat" else "ventes (écritures au débit)"
                reponse = QMessageBox.question(
                    self, "Colonne montant RS incohérente avec le sens",
                    f"L'analyse porte sur les {libelle_sens}, mais la colonne montant RS choisie est "
                    f"« {mapping['rs']} ».\n\nLes lignes de l'autre sens seraient rapprochées à la place. "
                    "Continuer quand même ?",
                )
                if reponse != QMessageBox.StandardButton.Yes:
                    raise ValueError("Analyse annulée : colonne montant RS incohérente avec le sens choisi.")

        self._diag_ds_mapping(f"{path} [{feuille}]", df, mapping)
        return df, mapping

    @staticmethod
    def _diag_ds_mapping(path: str, df, mapping: dict) -> None:
        """
        [DS_MAPPING] — diagnostic en lecture seule du mapping DS confirmé par
        l'utilisateur dans MappingDialog (seul décisionnaire — ce diagnostic
        se contente d'afficher le résultat retenu, sans notion de confiance
        calculée). lignes_RS_detectees/exploitees supposent la convention
        d'écriture "RS/..." : repère informatif uniquement, sans effet sur
        le mapping ni sur le moteur de rapprochement.
        """
        from core.matcher import _preparer_records_ds

        lignes = ["[DS_MAPPING]", f"  filename : {os.path.basename(path)}"]
        for role, label in [("fournisseur", "fournisseur"), ("rs", "montant_rs"), ("date_paiement", "date_comptable")]:
            lignes.append(f"  {label:<15} = {mapping.get(role)!r}")

        lignes_rs_detectees = lignes_rs_exploitees = 0
        if mapping.get("fournisseur"):
            masque_rs = df[mapping["fournisseur"]].astype(str).str.contains(
                RS_PATTERN_STR, case=False, regex=True, na=False
            )
            lignes_rs_detectees = int(masque_rs.sum())
            if mapping.get("rs") and mapping.get("date_paiement"):
                ds_records, _, _ = _preparer_records_ds(df, mapping)
                idx_rs = set(df.index[masque_rs])
                idx_exploitees = {r["orig_idx"] for r in ds_records}
                lignes_rs_exploitees = len(idx_rs & idx_exploitees)
        lignes.append(f"  lignes_RS_detectees  : {lignes_rs_detectees}")
        lignes.append(f"  lignes_RS_exploitees : {lignes_rs_exploitees}")

        _diag_print("\n".join(lignes))

    def _load_tej(self, path: str):
        """
        Charge le fichier TEJ. TEJ sépare achats et ventes en deux feuilles :
        celle du sens choisi est retenue automatiquement si son nom le
        permet, sinon l'utilisateur la désigne. Fournisseur, HTVA, montant
        RS, date de paiement et statut sont obligatoires (voir
        ROLES_TEJ_OBLIGATOIRES), date_creation reste optionnelle.

        La colonne contrepartie dépend du sens (bénéficiaire pour les
        achats, déclarant pour les ventes — core/parser.py::
        COLONNE_CONTREPARTIE_TEJ). Si cette colonne exacte est absente, le
        repli par mots-clés pourrait prendre la mauvaise raison sociale
        (déclarant/bénéficiaire/émetteur) : le mapping est alors soumis à
        l'utilisateur au lieu d'être appliqué silencieusement.
        """
        sens = self._sens()
        feuilles = list_sheets(path)
        feuille = choisir_feuille_par_sens(feuilles, sens)
        if feuille is None:
            feuille, ok = QInputDialog.getItem(
                self, "Feuille TEJ",
                f"Quelle feuille du fichier TEJ contient les {'achats' if sens == 'achat' else 'ventes'} ?",
                feuilles, 0, False,
            )
            if not ok:
                raise ValueError("Sélection de la feuille TEJ annulée par l'utilisateur.")

        df = load_excel_sheet(path, feuille)
        mapping = map_tej_columns(df, sens=sens)

        manquants = [role for role in self.ROLES_TEJ_OBLIGATOIRES if mapping.get(role) is None]
        contrepartie_incertaine = mapping.get("fournisseur") != COLONNE_CONTREPARTIE_TEJ[sens]

        if manquants or contrepartie_incertaine:
            dialog = MappingDialog(list(df.columns), mapping, f"TEJ — feuille « {feuille} »", self,
                                   roles_requis=self.ROLES_TEJ_OBLIGATOIRES)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                raise ValueError("Mapping annulé par l'utilisateur pour le fichier TEJ.")
            mapping = dialog.get_validated_mapping()

        self._diag_tej_parsed(f"{path} [{feuille}]", df, mapping)
        return df, mapping

    @staticmethod
    def _diag_tej_parsed(path: str, df, mapping: dict) -> None:
        """
        [TEJ_PARSED] — diagnostic en lecture seule du fichier TEJ tel qu'il a
        été réellement lu depuis le disque (pas d'hypothèse : on relit df et
        mapping après coup, ne modifie rien). Ne s'exécute qu'une fois le
        mapping des colonnes définitivement résolu.
        """
        col_statut = mapping.get("statut")
        col_fournisseur = mapping.get("fournisseur")
        col_rs = mapping.get("rs")

        statuts = Counter(str(v).strip() for v in df[col_statut].dropna()) if col_statut else Counter()
        fournisseurs = df[col_fournisseur].dropna().astype(str).str.strip() if col_fournisseur else []
        montants = df[col_rs].dropna() if col_rs else []

        lignes = [
            "[TEJ_PARSED]",
            f"  filename         : {os.path.basename(path)}",
            f"  total_rows       : {len(df)}",
        ]
        for statut_val, count in statuts.items():
            lignes.append(f"  rows_{statut_val:<12} : {count}")
        lignes.append(f"  unique_suppliers : {len(set(fournisseurs)) if len(fournisseurs) else 0}")
        lignes.append(f"  unique_amounts   : {len(set(montants)) if len(montants) else 0}")


        _diag_print("\n".join(lignes))

    @staticmethod
    def _diag_perimetre(stats: dict) -> None:
        """
        [PERIMETRE_DS] — diagnostic explicite du périmètre temporel de
        l'analyse (voir core/analysis_scope.py, décision métier validée
        "ne pas confondre période d'analyse DS et fenêtre de matching").
        Lecture seule : n'influence rien, se contente d'expliquer à
        l'auditeur ce que le moteur a déjà décidé.
        """
        scopes = [(libelle, sc) for libelle, sc in stats.get("scopes", []) if sc is not None]
        if not scopes:
            return
        fmt = lambda d: d.strftime("%d/%m/%Y") if d else "-"
        scope = scopes[0][1]
        lignes = ["[PERIMETRE_DS]"]
        for libelle, sc in scopes:
            lignes.append(f"  Période comptable analysée ({libelle}) : {fmt(sc.date_debut_ds)} → {fmt(sc.date_fin_ds)}")
        lignes += [
            "[FENETRE_MATCHING]",
            f"  Fenêtre temporelle (retard autorisé) : MAX_DATE_WINDOW = {scope.max_date_window} jours "
            f"(≈ {scope.max_date_window / 30:.1f} mois)",
            "[REGLE_FISCAL_ONLY]",
            "  Seuls les certificats TEJ dont la date est comprise dans la période DS analysée "
            "peuvent être classés FISCAL_ONLY.",
            "[REGLE_MATCHING]",
            "  Les certificats TEJ hors période DS peuvent néanmoins matcher une opération DS "
            "s'ils respectent la fenêtre temporelle autorisée.",
            f"  TEJ dans la période DS analysée              : {stats.get('tej_dans_periode_ds', '-')}",
            f"  TEJ hors période DS                          : {stats.get('tej_hors_periode_ds', '-')}",
            f"  ... dont utilisés comme candidats de matching : {stats.get('tej_hors_periode_utilises_comme_candidats', '-')}",
        ]
        _diag_print("\n".join(lignes))

    def run_reconciliation(self):
        """Lance l'analyse automatique avec le moteur de rapprochement bidirectionnel."""
        path_ds = self.drop_ds.file_path
        path_tej = self.drop_tej.file_path

        if not path_ds or not path_tej:
            QMessageBox.warning(self, "Avertissement", "Veuillez charger les DEUX fichiers Excel avant de lancer l'analyse.")
            return

        # Diagnostic : reconfirme au moment précis du clic, à partir des
        # MÊMES attributs (self.drop_ds.file_path / self.drop_tej.file_path)
        # que ceux fixés par FileDropZone.set_file() — aucune variable
        # intermédiaire, aucun chemin par défaut ni fichier de test ne peut
        # s'y substituer entre la sélection et l'analyse.
        _diag_print("[ANALYSE_LANCEE] chemins utilisés par le bouton de rapprochement :")
        _diag_file_selected(path_ds)
        _diag_file_selected(path_tej)

        # Phase 1 (fil principal) : tout ce qui demande l'utilisateur —
        # choix des feuilles et des taux, mapping des colonnes (dialogues Qt,
        # qui ne peuvent s'ouvrir que depuis le fil de l'interface).
        try:
            # Taux RS : un par feuille DS analysée. Classeur à une seule
            # feuille : taux sélectionné dans la fenêtre principale
            # (comportement historique, "un fichier DS ne contient qu'un seul
            # taux RS à la fois"). Classeur à plusieurs feuilles : une feuille
            # par taux, chacune rapprochée séparément puis fusionnée dans un
            # même rapport (core/reconciliation.py::fusionner_analyses).
            feuilles_ds = self._choisir_feuilles_ds(path_ds)
            for feuille, taux in feuilles_ds:
                _diag_print(f"[TAUX_RS] feuille « {feuille} » : taux sélectionné = {taux * 100:.2f}%")

            df_tej, map_tej = self._load_tej(path_tej)

            taches, map_ds = [], None
            for feuille, taux in feuilles_ds:
                df_ds, map_ds = self._load_ds(path_ds, feuille, mapping_precedent=map_ds)
                taches.append((feuille, taux, df_ds, map_ds))
        except Exception as e:
            logger.exception("Erreur pendant la préparation de l'analyse")
            QMessageBox.critical(self, "Erreur lors de l'analyse", f"Une erreur s'est produite :\n{str(e)}")
            return

        # Phase 2 (fil de travail) : le rapprochement lui-même, qui peut
        # prendre du temps sur de gros fichiers. CHANGEMENT : il tournait sur
        # le fil de l'interface, qui se figeait ("Ne répond pas") jusqu'à la
        # fin du calcul.
        self.btn_analyze.setEnabled(False)
        self.progression = QProgressDialog("Rapprochement en cours…", None, 0, len(taches), self)
        self.progression.setWindowTitle("Analyse")
        self.progression.setWindowModality(Qt.WindowModality.WindowModal)
        self.progression.setMinimumDuration(0)
        self.progression.setValue(0)

        self.worker = AnalyseWorker(taches, df_tej, map_tej, self.registry)
        self.worker.progression.connect(self._on_analyse_progression)
        self.worker.termine.connect(self._on_analyse_terminee)
        self.worker.echec.connect(self._on_analyse_echec)
        self.worker.start()

    def _on_analyse_progression(self, etape: int, message: str):
        self.progression.setLabelText(message)
        self.progression.setValue(etape)

    def _fin_analyse(self):
        self.progression.close()
        self.btn_analyze.setEnabled(True)
        self.worker = None

    def _on_analyse_echec(self, message: str):
        self._fin_analyse()
        QMessageBox.critical(self, "Erreur lors de l'analyse", f"Une erreur s'est produite :\n{message}")

    def _on_analyse_terminee(self, results: list, stats: dict, nb_feuilles: int):
        """Phase 3 (fil principal) : affichage des résultats du fil de travail."""
        self._fin_analyse()
        self.results_cache = results
        self._diag_perimetre(stats)

        self.display_results(self.results_cache)
        self.btn_export.setEnabled(True)

        nb_review = sum(1 for r in self.results_cache if r.statut == "REVIEW")
        self.btn_review.setEnabled(nb_review > 0)

        counts = Counter(r.statut for r in self.results_cache)
        # CHANGEMENT (rectification sémantique demandée, "Chantier 3 —
        # rectification finale") : "Hors périmètre" est séparé du reste
        # par une ligne vide et jamais mêlé à FISCAL_ONLY/COMPTA_ONLY —
        # c'est une catégorie de PÉRIMÈTRE (exclusion métier volontaire,
        # DMI/RECTIF côté DS), pas une anomalie de rapprochement.
        resume = (
            f"Correspondances certaines : {counts.get('MATCH_CERTAIN', 0)}\n"
            f"Correspondances probables : {counts.get('MATCH_PROBABLE', 0)}\n"
            f"À vérifier : {counts.get('REVIEW', 0)}\n"
            f"Fiscal uniquement : {counts.get('FISCAL_ONLY', 0)}\n"
            f"Comptabilité uniquement : {counts.get('COMPTA_ONLY', 0)}\n"
            f"Certificat TEJ trouvé mais annulé : {counts.get('CERTIFICAT_TEJ_ANNULE', 0)}\n"
            f"Hors du taux RS sélectionné : {counts.get('HORS_TAUX_SELECTIONNE', 0)}\n"
            f"\n"
            f"Hors périmètre RS (exclusion métier volontaire) : {counts.get('HORS_PERIMETRE_TYPE', 0)}"
        )
        if stats.get("delais_paiement_depasses"):
            resume += (
                f"\n\n⚠ Certificats TEJ payés plus de 31 jours après leur création "
                f"(risque de pénalité) : {stats['delais_paiement_depasses']}"
            )
        if nb_feuilles > 1:
            resume = f"Feuilles DS analysées : {nb_feuilles}\n\n" + resume

        _diag_print(f"[ANALYSE_TERMINEE] {len(self.results_cache)} lignes — {dict(counts)}")
        QMessageBox.information(
            self,
            "Analyse Terminée",
            f"Le rapprochement est terminé avec succès !\nTotal opérations : {len(self.results_cache)}\n\n{resume}"
        )

    def display_results(self, results: list):
        """Rempli le tableau GUI avec des couleurs de statut (list[ReconciliationResult])."""
        self.table_widget.setRowCount(0)

        for row_idx, r in enumerate(results):
            self.table_widget.insertRow(row_idx)

            if r.statut == "MATCH_CERTAIN":
                bg_color = QColor("#D4EDDA")  # Vert
            elif r.statut == "MATCH_PROBABLE":
                bg_color = QColor("#D1E7F0")  # Bleu clair
            elif r.statut == "REVIEW":
                bg_color = QColor("#FFF3CD")  # Orange/Jaune clair
            elif r.statut == "CERTIFICAT_TEJ_ANNULE":
                bg_color = QColor("#FFE0B2")  # Orange vif — distinct de COMPTA_ONLY (anomalie signalée, pas une simple absence)
            elif r.statut == "HORS_TAUX_SELECTIONNE":
                bg_color = QColor("#E2E3E5")  # Gris neutre — hors du périmètre choisi, pas une anomalie comptable
            elif r.statut == "HORS_PERIMETRE_TYPE":
                bg_color = QColor("#D6D0E8")  # Violet clair — exclusion métier volontaire (DMI/RECTIF côté DS), jamais une anomalie
            else:  # FISCAL_ONLY, COMPTA_ONLY
                bg_color = QColor("#F8D7DA")  # Rouge

            # CORRECTION : raw_name vaut None quand la cellule fournisseur est
            # vide (voir core/matcher.py::_extraire_fournisseur) —
            # QTableWidgetItem(None) levait alors une TypeError.
            fournisseur_tej = (r.fiscal_op.raw_name or "-") if r.fiscal_op else "-"
            fournisseur_ds = (r.accounting_op.raw_name or "-") if r.accounting_op else "-"
            date_tej = r.fiscal_op.op_date if r.fiscal_op else None
            date_ds = r.accounting_op.op_date if r.accounting_op else None
            montant = r.fiscal_op.amount if r.fiscal_op else (r.accounting_op.amount if r.accounting_op else None)
            score = f"{r.scores.global_score:.1f}%" if r.scores else "-"

            items = [
                QTableWidgetItem(STATUT_LABELS.get(r.statut, r.statut)),
                QTableWidgetItem(r.feuille or "-"),
                QTableWidgetItem(str(fournisseur_tej)),
                QTableWidgetItem(str(fournisseur_ds)),
                QTableWidgetItem(self._format_date(date_tej)),
                QTableWidgetItem(self._format_date(date_ds)),
                QTableWidgetItem(self._format_montant(montant)),
                QTableWidgetItem(score),
                QTableWidgetItem(r.commentaire),
            ]

            for col_idx, item in enumerate(items):
                item.setBackground(bg_color)
                self.table_widget.setItem(row_idx, col_idx, item)

    def open_supplier_registry_dialog(self):
        """Ouvre le référentiel fournisseurs (consultation + annulation d'alias, voir §7)."""
        dialog = SupplierRegistryDialog(self.registry, self)
        dialog.exec()

    def open_review_dialog(self):
        """Étape 9 : ouvre la fenêtre de vérification humaine pour les correspondances REVIEW."""
        a_verifier = [r for r in self.results_cache if r.statut == "REVIEW"]
        if not a_verifier:
            return

        # tous_les_resultats=self.results_cache (decision metiere confirmee,
        # "CHANTIER 1 — reoptimisation locale des composantes") : passe la
        # liste COMPLETE (par reference) pour que reappliquer_alias() puisse
        # reprendre un resultat deja COMPTA_ONLY/FISCAL_ONLY suite a une
        # validation d'alias, pas seulement les REVIEW affichees ici.
        dialog = ReviewDialog(a_verifier, self.registry, self, tous_les_resultats=self.results_cache)
        dialog.exec()

        # CHANGEMENT (décision métier confirmée, point 2/3/6) : pour une
        # REVIEW ambiguë (plusieurs candidats), on passe désormais par
        # core/reconciliation.py::resoudre_review — les candidats non
        # retenus ne deviennent COMPTA_ONLY qu'À CE moment, jamais avant.
        # Pour une REVIEW à candidat unique (score faible mais pas
        # d'ambiguïté), comportement historique inchangé : confirmer ->
        # MATCH_CERTAIN en place, rejeter -> FISCAL_ONLY + COMPTA_ONLY.
        from core.models import ReconciliationResult

        nouveaux_resultats = []
        for r in self.results_cache:
            decision = dialog.decisions.get(id(r))
            if decision is None:
                nouveaux_resultats.append(r)
                continue

            if len(r.candidates) > 1:
                resolus = resoudre_review(r, candidat_choisi=decision["candidat"])
                for n in resolus:
                    n.feuille = r.feuille
                nouveaux_resultats.extend(resolus)
                continue

            if decision["action"] == "confirme":
                r.statut = "MATCH_CERTAIN"
                r.commentaire = "Confirmé manuellement par l'utilisateur"
                nouveaux_resultats.append(r)
            else:
                nouveaux_resultats.append(ReconciliationResult(
                    statut="FISCAL_ONLY", fiscal_op=r.fiscal_op, accounting_op=None, scores=None,
                    commentaire="Rejeté manuellement — correspondance proposée mais infirmée par l'utilisateur",
                    feuille=r.feuille,
                ))
                nouveaux_resultats.append(ReconciliationResult(
                    statut="COMPTA_ONLY", fiscal_op=None, accounting_op=r.accounting_op, scores=None,
                    commentaire="Rejeté manuellement — correspondance proposée mais infirmée par l'utilisateur",
                    feuille=r.feuille,
                ))

        self.results_cache = nouveaux_resultats
        self.display_results(self.results_cache)
        nb_review = sum(1 for r in self.results_cache if r.statut == "REVIEW")
        self.btn_review.setEnabled(nb_review > 0)

    @staticmethod
    def _format_date(valeur) -> str:
        if valeur is None:
            return "-"
        return valeur.strftime("%d/%m/%Y")

    @staticmethod
    def _format_montant(valeur) -> str:
        """Montant peut être None (ligne sans opération d'un côté) — évite un TypeError."""
        if valeur is None:
            return "-"
        return f"{valeur:,.3f}"

    def export_results(self):
        """Propose à l'utilisateur où sauvegarder le rapport Excel multi-feuilles."""
        if not self.results_cache:
            return

        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer le rapport de contrôle",
            "Rapport_Controle_RS.xlsx",
            "Fichier Excel (*.xlsx)"
        )

        if output_path:
            try:
                export_reconciliation_results(self.results_cache, output_path)
                QMessageBox.information(self, "Succès", f"Rapport Excel généré avec succès :\n{output_path}")
            except Exception as e:
                logger.exception("Erreur pendant l'export du rapport")
                QMessageBox.critical(self, "Erreur d'exportation", f"Impossible d'exporter le fichier :\n{str(e)}")