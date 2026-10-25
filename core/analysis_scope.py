"""
Contexte d'analyse — périmètre temporel (décision métier confirmée, "ne pas
confondre période d'analyse DS et fenêtre de matching").

Le fichier DS Comptable définit la PÉRIODE D'ANALYSE (date_debut_ds,
date_fin_ds), calculée exclusivement à partir des opérations DS valides —
JAMAIS depuis le fichier TEJ, qui est un référentiel historique
potentiellement bien plus large que la période comptable analysée (voir
core/reconciliation.py::reconcile, où AnalysisScope est construit).

Deux responsabilités STRICTEMENT séparées, à ne jamais mélanger :
  - is_eligible_for_fiscal_only(date_tej) : un certificat TEJ ne peut être
    déclaré FISCAL_ONLY que si sa date tombe DANS la période DS analysée.
  - is_in_matching_window(date_ds, date_tej) : la fenêtre de retard
    bidirectionnelle (~2 mois, voir core/rules.py::DATE_WINDOW_MAX_DAYS)
    déjà utilisée par core/candidate_generator.py pour la GÉNÉRATION de
    candidats — INCHANGÉE par ce module, reste seule responsable du
    périmètre de recherche de candidats. Un certificat TEJ hors période DS
    peut donc très bien rester un candidat valide pour une opération DS
    proche dans le temps (voir règle métier validée, "CAS des TEJ après la
    période DS") — ce module ne restreint JAMAIS candidate_generator.
"""

from dataclasses import dataclass
from datetime import date as date_type
from typing import Optional

from core.rules import DATE_WINDOW_MAX_DAYS


@dataclass
class AnalysisScope:
    """
    date_debut_ds/date_fin_ds : bornes de la période DS analysée (None si
    indéterminable — voir core/reconciliation.py::reconcile, qui bloque
    l'analyse avec un diagnostic explicite dans ce cas plutôt que de
    silencieusement tout accepter ou tout rejeter).
    max_date_window : fenêtre de retard bidirectionnelle pour le MATCHING
    uniquement (voir docstring du module) — reprend DATE_WINDOW_MAX_DAYS
    (déjà validé, ~2 mois) par défaut, configurable si besoin.
    """
    date_debut_ds: Optional[date_type]
    date_fin_ds: Optional[date_type]
    max_date_window: int = DATE_WINDOW_MAX_DAYS

    def is_in_ds_period(self, d: Optional[date_type]) -> bool:
        """Vrai si `d` tombe dans la période DS analysée (bornes incluses)."""
        if d is None or self.date_debut_ds is None or self.date_fin_ds is None:
            return False
        return self.date_debut_ds <= d <= self.date_fin_ds

    def is_in_matching_window(self, date_ds: Optional[date_type], date_tej: Optional[date_type]) -> bool:
        """
        Fenêtre de retard bidirectionnelle — même règle, même valeur par
        défaut que core/candidate_generator.py (qui reste la seule source
        de vérité pour la génération de candidats ; cette méthode n'est
        qu'une fonction d'explication/diagnostic cohérente avec la même
        règle, jamais utilisée pour restreindre candidate_generator).
        """
        if date_ds is None or date_tej is None:
            return False
        return abs((date_tej - date_ds).days) <= self.max_date_window

    def is_eligible_for_fiscal_only(self, date_tej: Optional[date_type]) -> bool:
        """
        Un certificat TEJ ne peut être classé FISCAL_ONLY QUE si sa date
        tombe dans la période DS analysée — jamais simplement parce qu'il
        existe quelque part dans le fichier TEJ (voir règle métier
        validée, §3 : distinction matching DS→TEJ vs FISCAL_ONLY TEJ→DS).
        """
        return self.is_in_ds_period(date_tej)
