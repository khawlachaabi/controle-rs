"""
Génération des candidats de rapprochement (blocking) — Étape 5.

Réduit le nombre de comparaisons en regroupant les opérations par montant
EXACT au millime (hard rule, voir core/rules.py) avant d'appliquer la
fenêtre de délai (deuxième hard rule, bidirectionnelle). Sans ce blocking,
comparer chaque opération fiscale à chaque opération comptable serait en
O(n*m) — voir Documentation, section performance. Avec l'indexation par
montant, le coût moyen redescend à O(n) pour la génération des candidats.

Le nom fournisseur n'est volontairement PAS utilisé comme critère de
blocking : les noms sont peu fiables (saisie manuelle, RS/IMPRIMEX vs
IMPRIMEX vs nom arabe...), bloquer dessus risquerait d'éliminer de vrais
candidats avant même le scoring (voir règles métier validées, §8).

CHANGEMENT (décision métier confirmée, "gestion des écarts de montant de
0,001 avec validation humaine") : max_amount_gap_milliemes (0 par défaut,
comportement IDENTIQUE à avant pour tout appelant existant qui ne le passe
pas) permet d'élargir le blocking à des montants voisins de ±N millièmes.
Seul core/reconciliation.py::reconcile_operations() passe explicitement 1
(ECART_MONTANT_MAX_MILLIEMES) pour le rapprochement principal — les appels
utilisés pour détecter les certificats annulés ou hors-taux
(core/reconciliation.py::signaler_certificats_annules,
signaler_hors_taux) restent à 0 (exact), volontairement : ces
vérifications répondent à une question différente ("existe-t-il EXACTEMENT
ce montant ailleurs ?") et ne doivent pas se fondre avec la tolérance
d'écart de 1 millime. Élargir le pool de candidats ne change JAMAIS la
classification automatique — un candidat à 1 millime d'écart est toujours
forcé en REVIEW par core/reconciliation.py::_classifier, jamais promu
MATCH_CERTAIN/MATCH_PROBABLE ici ni ailleurs.
"""

from collections import defaultdict

from core.rules import DATE_WINDOW_MAX_DAYS, montant_en_milliemes


def _index_by_milliemes(ops: list) -> dict:
    index = defaultdict(list)
    for op in ops:
        index[montant_en_milliemes(op.amount)].append(op)
    return index


def generate_candidates(fiscal_ops: list, accounting_ops: list, max_date_window: int = DATE_WINDOW_MAX_DAYS,
                         max_amount_gap_milliemes: int = 0) -> dict:
    """
    Pour chaque opération fiscale, retourne la liste des opérations
    comptables candidates :
      - montant au millime près, à ±max_amount_gap_milliemes millièmes
        (hard rule — EXACT par défaut, comme avant) ;
      - date dans la fenêtre autorisée, bidirectionnelle
        (abs(delta_days) <= max_date_window — hard rule).

    Retourne {fiscal_op: [accounting_op, ...]} (liste vide si aucun candidat).
    Une opération fiscale sans date exploitable n'a aucun candidat (on ne
    peut pas vérifier la fenêtre de délai) — elle ressortira en FISCAL_ONLY,
    ce qui est correct : sans date fiable, aucune correspondance ne peut être
    validée avec confiance.
    """
    accounting_by_milliemes = _index_by_milliemes(accounting_ops)
    candidates = {}
    for fop in fiscal_ops:
        fop_milliemes = montant_en_milliemes(fop.amount)
        pool = []
        for delta_milliemes in range(-max_amount_gap_milliemes, max_amount_gap_milliemes + 1):
            pool.extend(accounting_by_milliemes.get(fop_milliemes + delta_milliemes, []))
        valides = []
        if fop.op_date is not None:
            for aop in pool:
                if aop.op_date is None:
                    continue
                delta = abs((aop.op_date - fop.op_date).days)
                if delta <= max_date_window:
                    valides.append(aop)
        candidates[fop] = valides
    return candidates
