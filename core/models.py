"""
Modèle de données du moteur de rapprochement (record linkage) : représentation
neutre d'une opération, qu'elle vienne de la plateforme fiscale (TEJ) ou de la
comptabilité (DS), plus les structures de résultat du matching multi-critères.

Étape 2 de l'architecture de rapprochement financier (voir Documentation).
"""

from dataclasses import dataclass, field
from datetime import date as date_type
from typing import Optional


@dataclass(eq=False)
class Operation:
    """
    Une opération normalisée, issue d'une des deux sources (DS ou TEJ).

    eq=False : on garde l'égalité/hash par défaut basée sur l'identité de
    l'objet (id()), pas sur la valeur des champs — nécessaire pour suivre
    "cette ligne précise a déjà été matchée" dans le moteur d'assignation
    (deux opérations avec les mêmes valeurs ne sont PAS la même opération).
    """
    source: str  # "DS" (comptabilité) ou "TEJ" (plateforme fiscale)
    index: int  # index de la ligne dans le DataFrame d'origine (traçabilité/export)
    raw_name: str
    clean_name: str
    amount: float  # montant RS — seul montant directement comparable entre les 2 sources (hard rule)
    op_date: Optional[date_type]  # date utilisée pour le rapprochement (DS: "Date" ; TEJ: "Date de paiement")
    extra: dict = field(default_factory=dict)  # champs additionnels (htva, tva, date_creation, statut...)
    matched: bool = False


@dataclass
class SignalScores:
    """Détail des signaux ayant contribué au score global d'un candidat — pour l'explicabilité (voir doc)."""
    amount_score: float
    supplier_score: float
    date_score: float
    date_delta_days: Optional[int]
    reference_score: Optional[float] = None
    global_score: float = 0.0
    raisons: list = field(default_factory=list)  # ex: "✓ Montant identique", "⚠ Écart de date de 47 jours"

    # CHANGEMENT (décision métier confirmée, "gestion de plusieurs taux RS
    # dans une même analyse") : taux RS (rs/htva) de chaque côté quand HTVA
    # est disponible (TEJ presque toujours ; DS seulement si mappé, voir
    # core/matcher.py::_preparer_records_ds). PUREMENT INFORMATIF — voir
    # core/scorer.py::score_candidate : n'entre JAMAIS dans global_score, ne
    # filtre JAMAIS un candidat, aucun poids ni seuil associé (règle métier
    # de pénalisation par le taux non validée à ce jour). taux_coherent
    # reste None tant que les deux taux ne sont pas simultanément connus —
    # ce n'est alors ni une confirmation ni une alerte, juste une donnée
    # absente.
    taux_tej: Optional[float] = None
    taux_ds: Optional[float] = None
    taux_coherent: Optional[bool] = None

    # CHANGEMENT (décision métier confirmée, "gestion des écarts de montant
    # de 0,001 avec validation humaine") : 0 = montant strictement identique
    # (comportement historique inchangé) ; 1 = écart d'exactement 1 millime
    # (0,001) — le candidat existe (voir core/candidate_generator.py,
    # ECART_MONTANT_MAX_MILLIEMES) mais core/reconciliation.py::_classifier
    # force TOUJOURS le statut REVIEW dans ce cas, jamais MATCH_CERTAIN ni
    # MATCH_PROBABLE, quel que soit global_score/supplier_score. N'entre
    # JAMAIS dans le calcul de global_score (amount_score n'y participe déjà
    # pas, voir plus haut) — uniquement un déclencheur de classification et
    # une donnée d'explicabilité.
    ecart_montant_millimes: int = 0

    # CHANGEMENT (décision métier confirmée, "validation fournisseur
    # persistante et propagation automatique") : marqueur PUREMENT INFORMATIF
    # (voir core/scorer.py::supplier_score) — n'entre JAMAIS dans
    # global_score, ne change AUCUN seuil ni poids. Distingue les 3 niveaux
    # de confiance sur l'identité fournisseur :
    #   "VALIDATED_ALIAS"          -> résolu via un alias déjà validé humainement
    #                                 (core/supplier_registry.py) — supplier_score=1.0
    #   "EXACT_NAME"               -> noms identiques après normalisation, sans alias
    #   "FUZZY_SIMILAR_UNVALIDATED" -> similarité floue au-dessus du seuil,
    #                                  jamais validée humainement (Niveau 2)
    #   "FUZZY_DIFFERENT_UNVALIDATED" -> similarité floue en dessous du seuil (Niveau 3)
    #   "NOT_COMPARABLE"           -> nom fournisseur absent d'un côté ou des deux
    # Ne pilote AUCUNE décision de classification à lui seul : c'est toujours
    # supplier_score (le float) qui reste comparé à SUPPLIER_MIN_SCORE dans
    # core/reconciliation.py::_classifier, inchangé.
    supplier_identity: str = "NOT_COMPARABLE"

    # CHANGEMENT (décision métier confirmée, "Chantier 3 — DMI/RECTIF et
    # filtrage métier") : True si le certificat TEJ du candidat porte un
    # statut de RECTIFICATION (voir core/operation_types.py::is_rectification).
    # Une RECTIF reste un candidat de matching valide (jamais exclue comme
    # un ANNULÉ), mais ne doit JAMAIS être traitée comme une RS ordinaire :
    # core/reconciliation.py::_classifier force TOUJOURS REVIEW dans ce cas
    # (raison RECTIFICATION_TEJ), jamais MATCH_CERTAIN ni MATCH_PROBABLE,
    # même à score parfait. N'entre JAMAIS dans le calcul de global_score —
    # uniquement un déclencheur de classification et une donnée
    # d'explicabilité, au même titre que ecart_montant_millimes ci-dessus.
    est_rectification: bool = False


@dataclass
class MatchCandidate:
    """Un candidat de rapprochement entre une opération fiscale et une opération comptable, avec son score."""
    fiscal_op: Operation
    accounting_op: Operation
    scores: SignalScores


@dataclass
class ReconciliationResult:
    """
    Résultat final pour une opération (matchée ou non).

    CHANGEMENT (décision métier confirmée) : `second_best` (un seul
    candidat concurrent) remplacé par `candidates` (liste complète). Pour un
    statut REVIEW causé par une AMBIGUÏTÉ réelle (plusieurs candidats à
    score proche), `accounting_op` reste None — aucun candidat n'est
    "choisi" tant qu'une validation humaine n'a eu lieu (voir
    core/reconciliation.py::reconcile_operations et ::resoudre_review) — et
    `candidates` contient tous les candidats encore en lice (2 ou plus).
    Pour un MATCH (ou une REVIEW causée par un score faible sur un candidat
    UNIQUE, pas une ambiguïté), `accounting_op` est défini normalement et
    `candidates` contient ce seul candidat, pour une structure uniforme.
    """
    statut: str  # MATCH_CERTAIN, MATCH_PROBABLE, REVIEW, FISCAL_ONLY, COMPTA_ONLY
    fiscal_op: Optional[Operation]
    accounting_op: Optional[Operation]
    scores: Optional[SignalScores]
    candidates: list = field(default_factory=list)  # list[MatchCandidate] — voir docstring ci-dessus
    commentaire: str = ""
    # Feuille DS d'origine et taux associé (ex: "Loyers (10.00%)") — renseigné
    # par core/reconciliation.py::fusionner_analyses quand plusieurs feuilles
    # DS (une par taux) sont analysées puis fusionnées dans un même rapport.
    # Sert aussi à cantonner reappliquer_alias à une seule feuille/taux.
    feuille: str = ""
