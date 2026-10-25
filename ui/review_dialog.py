"""
Fenêtre de review humaine (Étape 9) : présente les correspondances au statut
REVIEW une par une (montant + délai compatibles, mais score global sous les
seuils automatiques, fournisseur sous SUPPLIER_MIN_SCORE, ou AMBIGUÏTÉ entre
plusieurs candidats à score proche — voir règles métier validées, §11, §14,
§20 et point 2/3/6 sur la représentation des candidats multiples).

Deux modes d'affichage selon `len(result.candidates)` :
  - 1 seul candidat (accounting_op déjà défini) : comparaison simple
    fiscal/comptable, confirmer/rejeter (comportement historique).
  - plusieurs candidats (accounting_op=None, ambiguïté réelle) : tous les
    candidats sont listés avec leur score, l'utilisateur choisit lequel
    retenir (ou rejette tout) — voir core/reconciliation.py::resoudre_review,
    qui n'est appelée qu'à ce moment : les candidats non choisis ne
    deviennent COMPTA_ONLY qu'APRÈS cette décision, jamais avant.

Une confirmation avec un fournisseur peu similaire propose de mémoriser
l'alias dans le Supplier Registry, pour que les rapprochements suivants
reconnaissent directement cette variante (voir §9, apprentissage des
validations humaines).
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QRadioButton, QButtonGroup, QWidget,
)
from PySide6.QtGui import QFont

from core.reconciliation import reappliquer_alias
from core.supplier_registry import SupplierRegistry


class ReviewDialog(QDialog):
    """
    Affiche successivement les ReconciliationResult passés en paramètre
    (normalement tous au statut REVIEW). Après fermeture, `self.decisions`
    contient {id(result): {"action": "confirme"|"rejete", "candidat":
    MatchCandidate ou None}} pour chaque résultat traité (un résultat fermé
    sans décision, via "Passer" ou la croix de la fenêtre, n'y figure pas —
    il reste REVIEW dans le rapport).
    """

    def __init__(self, review_results: list, registry: SupplierRegistry, parent=None, tous_les_resultats: list = None):
        """
        tous_les_resultats (optionnel, décision métier confirmée, "CHANTIER 1
        — réoptimisation locale des composantes") : référence à la liste
        COMPLÈTE des résultats de l'analyse (ui/main_window.py::results_cache),
        pas seulement le sous-ensemble REVIEW affiché ici. Nécessaire pour que
        core.reconciliation.reappliquer_alias puisse reprendre un résultat déjà
        tombé en COMPTA_ONLY/FISCAL_ONLY (invisible dans review_results) quand
        un alias fournisseur change l'affectation optimale de sa composante.
        Par défaut (None), reprend review_results — comportement historique
        inchangé pour tout appelant qui ne fournit pas ce paramètre.
        """
        super().__init__(parent)
        self.setWindowTitle("Vérification des correspondances incertaines")
        self.resize(680, 520)
        self.registry = registry
        self.review_results = review_results
        self.tous_les_resultats = tous_les_resultats if tous_les_resultats is not None else review_results
        self.index = 0
        self.decisions = {}
        self.candidat_radios = []  # [(QRadioButton, MatchCandidate), ...] pour le mode multi-candidats

        layout = QVBoxLayout(self)

        self.label_progress = QLabel()
        self.label_progress.setFont(QFont("Calibri", 9))
        self.label_progress.setStyleSheet("color: #6C757D;")
        layout.addWidget(self.label_progress)

        self.label_content = QLabel()
        self.label_content.setFont(QFont("Consolas", 10))
        self.label_content.setWordWrap(True)
        self.label_content.setStyleSheet(
            "background:#F8F9FA; border:1px solid #CBD5E1; border-radius:6px; padding:12px;"
        )
        layout.addWidget(self.label_content)

        # Zone dynamique pour les boutons radio des candidats multiples
        # (visible seulement en mode ambiguïté, voir _afficher_courant)
        self.zone_candidats = QWidget()
        self.layout_candidats = QVBoxLayout(self.zone_candidats)
        self.layout_candidats.setContentsMargins(12, 0, 12, 0)
        self.groupe_candidats = QButtonGroup(self)
        layout.addWidget(self.zone_candidats)

        self.check_alias = QCheckBox(
            "Mémoriser cette correspondance de fournisseur pour la reconnaître "
            "automatiquement la prochaine fois"
        )
        layout.addWidget(self.check_alias)

        btn_layout = QHBoxLayout()
        self.btn_confirmer = QPushButton("✓ Confirmer")
        self.btn_confirmer.setStyleSheet(
            "QPushButton { background-color: #28A745; color: white; padding: 8px 16px; border-radius: 4px; }"
        )
        self.btn_rejeter = QPushButton("✗ Rejeter")
        self.btn_rejeter.setStyleSheet(
            "QPushButton { background-color: #DC3545; color: white; padding: 8px 16px; border-radius: 4px; }"
        )
        self.btn_passer = QPushButton("Passer (garder en attente)")

        self.btn_confirmer.clicked.connect(self._confirmer)
        self.btn_rejeter.clicked.connect(self._rejeter)
        self.btn_passer.clicked.connect(self._suivant)

        btn_layout.addWidget(self.btn_confirmer)
        btn_layout.addWidget(self.btn_rejeter)
        btn_layout.addWidget(self.btn_passer)
        layout.addLayout(btn_layout)

        self._afficher_courant()

    def _vider_zone_candidats(self):
        for radio, _ in self.candidat_radios:
            self.groupe_candidats.removeButton(radio)
            radio.deleteLater()
        self.candidat_radios = []
        while self.layout_candidats.count():
            item = self.layout_candidats.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _afficher_courant(self):
        self._vider_zone_candidats()

        # Saute les résultats déjà résolus automatiquement (via reappliquer_alias,
        # voir _confirmer) depuis qu'ils ont été mis en file — plus rien à
        # demander à l'utilisateur pour ceux-là.
        while self.index < len(self.review_results) and self.review_results[self.index].statut != "REVIEW":
            self.index += 1

        if self.index >= len(self.review_results):
            self.accept()
            return

        r = self.review_results[self.index]
        self.label_progress.setText(f"Correspondance à vérifier {self.index + 1} / {len(self.review_results)}")

        if len(r.candidates) > 1:
            self._afficher_ambigu(r)
        else:
            self._afficher_simple(r)

    def _afficher_ambigu(self, r):
        """Mode ambiguïté : plusieurs candidats comptables pour une même opération fiscale (voir point 2/3)."""
        f = r.fiscal_op
        lignes = [
            "PLATEFORME FISCALE (TEJ)",
            "-" * 40,
            f"Fournisseur : {f.raw_name}",
            f"Date        : {self._fmt_date(f.op_date)}",
            f"Montant RS  : {f.amount:,.3f}",
            "",
            f"{len(r.candidates)} candidats comptables (DS) à score proche — choisissez celui qui correspond, ou rejetez tout :",
        ]
        self.label_content.setText("\n".join(lignes))
        self.zone_candidats.setVisible(True)
        self.check_alias.setVisible(False)  # pas d'alias en mode multi-candidats (ambiguïté sur QUEL candidat, pas sur le nom)

        for cand in sorted(r.candidates, key=lambda c: c.scores.global_score, reverse=True):
            a = cand.accounting_op
            texte = (
                f"{a.raw_name}  —  {self._fmt_date(a.op_date)}  —  "
                f"score {cand.scores.global_score:.1f}%  (fournisseur {cand.scores.supplier_score * 100:.0f}%, "
                f"écart date {cand.scores.date_delta_days} jour(s))"
            )
            radio = QRadioButton(texte)
            self.groupe_candidats.addButton(radio)
            self.layout_candidats.addWidget(radio)
            self.candidat_radios.append((radio, cand))

        if self.candidat_radios:
            self.candidat_radios[0][0].setChecked(True)

    def _afficher_simple(self, r):
        """Mode candidat unique : comparaison directe, confirmer/rejeter (comportement historique)."""
        f, a = r.fiscal_op, r.accounting_op
        lignes = [
            "PLATEFORME FISCALE (TEJ)                          COMPTABILITÉ (DS)",
            "-" * 66,
            f"Date         : {self._fmt_date(f.op_date):<28} Date         : {self._fmt_date(a.op_date)}",
            f"Fournisseur  : {f.raw_name}",
            f"Libellé DS   : {a.raw_name}",
        ]
        # CHANGEMENT (décision métier confirmée, "gestion des écarts de
        # montant de 0,001 avec validation humaine") : un candidat REVIEW
        # peut désormais avoir un montant TEJ différent du montant DS de 1
        # millime exactement (voir core/candidate_generator.py,
        # ECART_MONTANT_MAX_MILLIEMES) — afficher explicitement les deux
        # montants et l'écart plutôt qu'une seule ligne "Montant RS" qui
        # masquerait la différence à l'auditeur.
        if r.scores.ecart_montant_millimes:
            lignes.append("")
            lignes.append("⚠ Écart de montant au millime")
            lignes.append(f"  Montant DS  : {a.amount:,.3f}")
            lignes.append(f"  Montant TEJ : {f.amount:,.3f}")
            lignes.append(f"  Écart       : {r.scores.ecart_montant_millimes / 1000:.3f}")
            lignes.append("  → Validation humaine requise.")
        else:
            lignes.append(f"Montant RS   : {f.amount:,.3f}")
        lignes += [
            "",
            f"SCORE GLOBAL : {r.scores.global_score:.1f}%",
            f"  Fournisseur : {r.scores.supplier_score * 100:.0f}%",
            f"  Date        : {r.scores.date_score * 100:.0f}%  (écart de {r.scores.date_delta_days} jour(s))",
            "",
        ]
        for raison in r.scores.raisons:
            lignes.append(f"  {raison}")
        lignes.append("")
        lignes.append(f"Raison de la vérification : {r.commentaire}")

        self.label_content.setText("\n".join(lignes))
        self.zone_candidats.setVisible(False)
        # CHANGEMENT (décision métier confirmée, "validation fournisseur
        # persistante") : si l'identité fournisseur est déjà résolue via un
        # alias validé (ex: REVIEW causée uniquement par un écart de montant
        # de 0,001, fournisseur déjà connu), rien à mémoriser — évite une
        # case à cocher redondante pour une connaissance déjà acquise.
        deja_valide = r.scores.supplier_identity == "VALIDATED_ALIAS"
        self.check_alias.setVisible(f.clean_name != a.clean_name and not deja_valide)
        self.check_alias.setChecked(r.scores.supplier_score < 0.75)

    @staticmethod
    def _fmt_date(d):
        return d.strftime("%d/%m/%Y") if d else "-"

    def _candidat_choisi(self):
        for radio, cand in self.candidat_radios:
            if radio.isChecked():
                return cand
        return None

    def _confirmer(self):
        r = self.review_results[self.index]
        if len(r.candidates) > 1:
            candidat = self._candidat_choisi()
            if candidat is None:
                return  # aucun candidat sélectionné, ignore le clic
            self.decisions[id(r)] = {"action": "confirme", "candidat": candidat}
        else:
            self.decisions[id(r)] = {"action": "confirme", "candidat": None}
            if self.check_alias.isChecked():
                f, a = r.fiscal_op, r.accounting_op
                canonical = self.registry.resolve(a.raw_name) or self.registry.resolve(f.raw_name) or a.raw_name
                self.registry.add_alias(canonical, f.raw_name, persist=False)
                self.registry.add_alias(canonical, a.raw_name, persist=True)
                # CHANGEMENT (décision métier confirmée, "une validation
                # humaine doit avoir une portée persistante, pas seulement
                # modifier le résultat courant") : réapplique immédiatement
                # l'alias fraîchement validé. Passe self.tous_les_resultats
                # (pas seulement review_results, voir "CHANTIER 1 —
                # réoptimisation locale des composantes") : un résultat déjà
                # tombé en COMPTA_ONLY/FISCAL_ONLY (invisible dans
                # review_results) doit pouvoir être repris si l'alias change
                # l'affectation optimale de sa composante — sans jamais
                # écraser une décision humaine déjà prise (voir
                # core/reconciliation.py::reappliquer_alias).
                reappliquer_alias(self.tous_les_resultats, self.registry, deja_decides=set(self.decisions.keys()))
        self._suivant()

    def _rejeter(self):
        r = self.review_results[self.index]
        self.decisions[id(r)] = {"action": "rejete", "candidat": None}
        self._suivant()

    def _suivant(self):
        self.index += 1
        self._afficher_courant()
