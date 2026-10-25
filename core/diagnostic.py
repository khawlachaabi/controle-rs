"""
Mode diagnostic — lecture seule, AUCUNE modification des seuils, poids ou
règles de décision du moteur. Réutilise exactement candidate_generator et
scorer pour que le diagnostic reflète fidèlement ce que ferait une vraie
analyse, sans dupliquer ni approximer leur logique.

Objectif : pour chaque opération comptable (DS), expliquer précisément
pourquoi elle a — ou n'a pas — de candidat fiscal (TEJ), à quel niveau
(montant / fenêtre de délai / fournisseur), avant toute décision de modifier
un seuil.

IMPORTANT (honnêteté architecturale) : le fournisseur n'est PAS un filtre
dur dans candidate_generator — seuls le montant (hard rule stricte) et la
fenêtre de délai excluent un candidat de la liste. Un score fournisseur
faible ne retire donc jamais un candidat de la liste ; il empêche seulement
une classification automatique MATCH_CERTAIN/MATCH_PROBABLE (voir
core/rules.py::SUPPLIER_MIN_SCORE et core/reconciliation.py::_classifier).
Ce module ne invente pas de rejet fournisseur qui n'existe pas dans le
moteur réel — il signale seulement, pour chaque candidat déjà généré, s'il
est sous ce seuil.
"""

from dataclasses import dataclass, field
from typing import Optional

from core.rules import DATE_WINDOW_MAX_DAYS, SUPPLIER_MIN_SCORE
from core.scorer import score_candidate
from core.supplier_registry import SupplierRegistry


@dataclass
class DiagnosticCandidat:
    tej_raw_name: str
    tej_date: object
    delta_jours: Optional[int]
    supplier_score: float  # 0-100
    date_score: float  # 0-100
    global_score: float  # 0-100
    sous_seuil_fournisseur: bool


@dataclass
class DiagnosticLigne:
    ds_id: str
    ds_raw_name: str
    ds_montant: float
    ds_date: object
    nb_meme_montant: int  # TEJ de MEME montant exact, quelle que soit la date
    nb_dans_fenetre: int  # ... et dans la fenêtre de délai => candidats réellement générés
    nb_hors_fenetre: int  # même montant mais hors fenêtre => rejetés pour date
    candidats: list = field(default_factory=list)  # list[DiagnosticCandidat], triés par score décroissant
    candidat_retenu: Optional[str] = None
    statut_final: Optional[str] = None
    raison_finale: Optional[str] = None


def diagnostiquer_ds(accounting_op, fiscal_ops: list, registry: SupplierRegistry,
                      resultat_final=None) -> DiagnosticLigne:
    """
    Diagnostic pour UNE opération comptable (DS). Ne modifie rien : calcule
    en lecture seule, avec les mêmes fonctions que le vrai pipeline.
    """
    meme_montant = [f for f in fiscal_ops if f.amount == accounting_op.amount]
    dans_fenetre, hors_fenetre = [], []
    for f in meme_montant:
        if f.op_date is None or accounting_op.op_date is None:
            hors_fenetre.append(f)
            continue
        delta = abs((f.op_date - accounting_op.op_date).days)
        (dans_fenetre if delta <= DATE_WINDOW_MAX_DAYS else hors_fenetre).append(f)

    candidats = []
    for f in dans_fenetre:
        s = score_candidate(f, accounting_op, registry)
        candidats.append(DiagnosticCandidat(
            tej_raw_name=f.raw_name,
            tej_date=f.op_date,
            delta_jours=s.date_delta_days,
            supplier_score=round(s.supplier_score * 100, 1),
            date_score=round(s.date_score * 100, 1),
            global_score=s.global_score,
            sous_seuil_fournisseur=s.supplier_score < SUPPLIER_MIN_SCORE,
        ))
    candidats.sort(key=lambda c: c.global_score, reverse=True)

    ligne = DiagnosticLigne(
        ds_id=str(getattr(accounting_op, "extra", {}).get("ID", accounting_op.index)),
        ds_raw_name=accounting_op.raw_name,
        ds_montant=accounting_op.amount,
        ds_date=accounting_op.op_date,
        nb_meme_montant=len(meme_montant),
        nb_dans_fenetre=len(dans_fenetre),
        nb_hors_fenetre=len(hors_fenetre),
        candidats=candidats,
    )

    if resultat_final is not None:
        ligne.candidat_retenu = resultat_final.fiscal_op.raw_name if resultat_final.fiscal_op else None
        ligne.statut_final = resultat_final.statut
        ligne.raison_finale = resultat_final.commentaire

    return ligne


def diagnostiquer_tous_ds(accounting_ops: list, fiscal_ops: list, registry: SupplierRegistry,
                           results: list = None) -> list:
    """Diagnostic pour toutes les opérations comptables (DS) — voir diagnostiquer_ds."""
    result_by_ds_identity = {}
    if results:
        for r in results:
            if r.accounting_op is not None:
                result_by_ds_identity[id(r.accounting_op)] = r
            for c in r.candidates:
                result_by_ds_identity.setdefault(id(c.accounting_op), r)

    return [
        diagnostiquer_ds(aop, fiscal_ops, registry, result_by_ds_identity.get(id(aop)))
        for aop in accounting_ops
    ]


def formatter_diagnostic(ligne: DiagnosticLigne) -> str:
    """Représentation texte lisible d'une DiagnosticLigne, pour impression console/rapport."""
    date_str = ligne.ds_date.strftime("%d/%m/%Y") if ligne.ds_date else "date inconnue"
    lignes = [
        f"DS {ligne.ds_id} — {ligne.ds_raw_name!r} — {ligne.ds_montant:.3f} DT — {date_str}",
        f"  Candidats TEJ de MÊME montant (quelle que soit la date)      : {ligne.nb_meme_montant}",
        f"  ... dont dans la fenêtre de délai ({DATE_WINDOW_MAX_DAYS}j) [= candidats générés] : {ligne.nb_dans_fenetre}",
        f"  ... dont hors fenêtre de délai (rejetés pour date)           : {ligne.nb_hors_fenetre}",
    ]
    if ligne.candidats:
        lignes.append("  Détail des candidats générés (triés par score décroissant) :")
        for c in ligne.candidats:
            drapeau = f" [SOUS LE SEUIL FOURNISSEUR {SUPPLIER_MIN_SCORE * 100:.0f}%]" if c.sous_seuil_fournisseur else ""
            lignes.append(
                f"    - {c.tej_raw_name!r} (écart {c.delta_jours}j) : "
                f"fournisseur={c.supplier_score}% date={c.date_score}% global={c.global_score}%{drapeau}"
            )
    else:
        lignes.append("  Aucun candidat généré : soit aucun TEJ de montant identique, soit tous hors fenêtre de délai.")
    if ligne.statut_final:
        lignes.append(f"  -> Statut final : {ligne.statut_final} | Candidat retenu : {ligne.candidat_retenu or '-'}")
        lignes.append(f"     Raison : {ligne.raison_finale}")
    return "\n".join(lignes)
