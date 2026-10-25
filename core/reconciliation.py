"""
Rapprochement bidirectionnel complet — Étapes 7 et 8.

Pipeline : candidate_generator (hard rules montant+délai) -> scorer (soft
signaux fournisseur+date) -> assignation globale 1-to-1 -> classification
(MATCH_CERTAIN / MATCH_PROBABLE / REVIEW) -> détection FISCAL_ONLY /
COMPTA_ONLY pour tout ce qui n'a pas été assigné.

Remplace, pour le rapprochement lui-même, le pipeline séquentiel de
core/matcher.py::reconcile_data — mais RÉUTILISE ses fonctions de nettoyage
(_preparer_records_ds / _preparer_records_tej) via l'adaptateur ci-dessous,
qui gèrent déjà correctement les exclusions (montant illisible, statut
annulé) — voir core/matcher.py.
"""

from collections import defaultdict

from core.analysis_scope import AnalysisScope
from core.candidate_generator import generate_candidates
from core.matcher import _preparer_records_ds, _preparer_records_tej, _extraire_certificats_annules
from core.models import MatchCandidate, Operation, ReconciliationResult
from core.operation_types import is_dmi, is_rs_reconcilable
from core.rules import (
    MARGE_AMBIGUITE,
    SEUIL_MATCH_CERTAIN,
    SEUIL_MATCH_PROBABLE,
    SEUIL_REVIEW,
    SUPPLIER_MIN_SCORE,
    ECART_MONTANT_MAX_MILLIEMES,
    DELAI_MAX_PAIEMENT_JOURS,
    calculer_taux,
    check_payment_delay,
    montant_en_milliemes,
)
from core.scorer import score_candidate
from core.supplier_registry import SupplierRegistry

# Au-delà de cette taille (côté fiscal OU comptable) pour une composante
# connexe du graphe de candidats, la recherche exacte (exponentielle en
# nombre de comptables) bascule sur un repli glouton documenté — voir
# _matching_optimal_composante. Le montant étant une hard rule (les
# candidats ne relient que des opérations de montant EXACTEMENT identique),
# une composante regroupe uniquement des transactions partageant le même
# montant dans la même fenêtre de délai : en pratique quelques éléments,
# jamais des milliers — ce plafond n'est donc quasiment jamais atteint.
TAILLE_MAX_COMPOSANTE_EXACTE = 14


def _to_operations(records: list, source: str) -> list:
    """Adapte les dicts produits par core/matcher.py::_preparer_records_* vers des Operation."""
    champs_connus = {"orig_idx", "raw_name", "clean_name", "rs", "date_paiement", "matched"}
    ops = []
    for r in records:
        ops.append(Operation(
            source=source,
            index=r["orig_idx"],
            raw_name=r["raw_name"],
            clean_name=r["clean_name"],
            amount=r["rs"],
            op_date=r["date_paiement"],
            extra={k: v for k, v in r.items() if k not in champs_connus},
        ))
    return ops


def _classifier(global_score: float, supplier_score: float, ecart_montant_millimes: int = 0,
                 est_rectification: bool = False) -> tuple:
    """
    Statut + raison à partir du score global et de trois garde-fous
    (fournisseur, écart de montant, rectification TEJ) — le score global ne
    peut CONTOURNER aucun d'eux.

    CHANGEMENT (résolution globale des candidats) : l'ambiguïté n'est plus
    détectée ici via une marge locale (score du 1er candidat moins le 2e
    d'une même opération fiscale) — elle est désormais déterminée AU NIVEAU
    DE LA COMPOSANTE CONNEXE ENTIÈRE par reconcile_operations() (voir
    _resoudre_composante_optimale et son commentaire) : un candidat "proche"
    dont l'utilisation exclusive dégraderait fortement le total optimal
    global n'est PAS une vraie ambiguïté (il est déjà mieux exploité
    ailleurs par une autre opération), alors qu'un candidat proche qui ne
    coûte presque rien au total global EST une vraie ambiguïté. _classifier
    n'est donc appelé QUE pour un candidat déjà confirmé comme seul choix
    plausible pour son opération fiscale — plus de paramètre `marge` ici.

    Ordre des vérifications (le score global ne peut jamais les contourner,
    voir règles métier validées) :
      1. ecart_montant_millimes != 0 (voir core/candidate_generator.py,
         ECART_MONTANT_MAX_MILLIEMES, "gestion des écarts de montant de
         0,001 avec validation humaine") ET/OU est_rectification (voir
         core/operation_types.py::is_rectification, "Chantier 3 — DMI/
         RECTIF et filtrage métier") => REVIEW obligatoire, raison(s)
         ECART_MONTANT_0_001 et/ou RECTIFICATION_TEJ (combinées si les deux
         s'appliquent), JAMAIS MATCH_CERTAIN ni MATCH_PROBABLE même à score
         global de 100% — vérifiées en premier, avant même le garde-fou
         fournisseur, car ce sont les vérifications les plus spécifiques et
         les plus strictes ;
      2. fournisseur sous SUPPLIER_MIN_SCORE => jamais MATCH_CERTAIN ni
         MATCH_PROBABLE automatique, même si le score global est élevé — un
         nom très différent peut être un problème de langue/saisie (français/
         arabe, alias non encore validé), pas nécessairement une fausse
         correspondance : REVIEW pour validation humaine, jamais un rejet
         automatique (voir core/rules.py, commentaire SUPPLIER_MIN_SCORE) ;
      3. sinon, classification standard par seuils de score global.
    """
    raisons_forcees = []
    if ecart_montant_millimes != 0:
        raisons_forcees.append(
            f"ECART_MONTANT_0_001 — écart de montant de {ecart_montant_millimes} millime(s) (0,"
            f"{ecart_montant_millimes:03d}) entre TEJ et DS"
        )
    if est_rectification:
        raisons_forcees.append("RECTIFICATION_TEJ — le certificat TEJ retenu est une rectification (statut RECTIFIÉ)")
    if raisons_forcees:
        return "REVIEW", (
            " ; ".join(raisons_forcees) + " — validation humaine obligatoire, ne peut jamais devenir une "
            "correspondance automatique certaine ou probable, quel que soit le score global"
        )
    if supplier_score < SUPPLIER_MIN_SCORE:
        return "REVIEW", (
            f"Fournisseur peu fiable (score {supplier_score * 100:.0f}%, "
            f"seuil {SUPPLIER_MIN_SCORE * 100:.0f}%) malgré un score global de "
            f"{global_score:.1f}% — vérification humaine nécessaire (nom très différent, "
            f"éventuellement langue différente ou alias non encore validé)"
        )
    if global_score >= SEUIL_MATCH_CERTAIN:
        return "MATCH_CERTAIN", "Score élevé, aucun candidat concurrent suffisamment proche"
    if global_score >= SEUIL_MATCH_PROBABLE:
        return "MATCH_PROBABLE", "Score élevé mais pas suffisamment certain pour une validation automatique complète"
    if global_score >= SEUIL_REVIEW:
        return "REVIEW", "Score intermédiaire — vérification recommandée"
    return "REVIEW", "Score faible malgré montant et délai compatibles — vérification recommandée"


def _describe_candidat(c: MatchCandidate) -> str:
    """Résumé lisible d'un candidat pour le commentaire d'une ligne REVIEW ambiguë (voir point 3)."""
    aop = c.accounting_op
    date_str = aop.op_date.strftime("%d/%m/%Y") if aop.op_date else "date inconnue"
    return f"« {aop.raw_name} » ({date_str}, score {c.scores.global_score:.1f}%)"


def _composantes_connexes(all_candidates: list) -> list:
    """
    Regroupe les candidats en composantes connexes du graphe biparti
    TEJ<->DS (union-find). Le montant étant une hard rule appliquée en amont
    par core/candidate_generator.py, deux opérations de montants différents
    ne peuvent jamais partager une composante — chaque composante regroupe
    donc uniquement des transactions de même montant, dans la fenêtre de
    délai (voir TAILLE_MAX_COMPOSANTE_EXACTE ci-dessus).
    """
    parent = {}

    def find(x):
        racine = x
        while parent.get(racine, racine) is not racine:
            racine = parent[racine]
        while parent.get(x, x) is not racine:
            parent[x], x = racine, parent.get(x, x)
        return racine

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx is not ry:
            parent[rx] = ry

    for c in all_candidates:
        parent.setdefault(c.fiscal_op, c.fiscal_op)
        parent.setdefault(c.accounting_op, c.accounting_op)
        union(c.fiscal_op, c.accounting_op)

    groupes = defaultdict(list)
    for c in all_candidates:
        groupes[find(c.fiscal_op)].append(c)
    return list(groupes.values())


def _matching_glouton_composante(candidats: list, force: MatchCandidate = None) -> tuple:
    """
    Repli glouton pour une composante anormalement grande (voir
    TAILLE_MAX_COMPOSANTE_EXACTE) — trie par score décroissant, assigne
    gloutonnement sans réutiliser une opération déjà prise. Pas garanti
    optimal globalement (contrairement à _resoudre_composante_optimale) mais
    évite une explosion combinatoire sur un cas de collision de montants
    extrêmement improbable en pratique.
    """
    candidats_tries = sorted(candidats, key=lambda c: c.scores.global_score, reverse=True)
    if force is not None:
        candidats_tries = [force] + [c for c in candidats_tries if c is not force]
    utilises_f, utilises_a = set(), set()
    affectation = {}
    total = 0.0
    for c in candidats_tries:
        if c.fiscal_op in utilises_f or c.accounting_op in utilises_a:
            if force is c and (c.fiscal_op in utilises_f or c.accounting_op in utilises_a):
                return {}, float("-inf")  # force impossible (conflit direct)
            continue
        affectation[c.fiscal_op] = c
        utilises_f.add(c.fiscal_op)
        utilises_a.add(c.accounting_op)
        total += c.scores.global_score
    return affectation, total


def _resoudre_composante_optimale(candidats: list, force: MatchCandidate = None) -> tuple:
    """
    Résolution EXACTE (recherche exhaustive mémoïsée) du matching biparti de
    poids maximum sur une composante connexe : chaque opération fiscale est
    affectée à AU PLUS une opération comptable (et réciproquement), en
    maximisant la somme des scores globaux, une opération pouvant aussi
    rester délibérément non appariée (contribution nulle) si c'est
    globalement préférable. C'est la résolution bipartite 1-à-1 demandée —
    équivalente au problème d'affectation résolu par l'algorithme hongrois,
    implémentée ici par recherche exhaustive avec mémoïsation plutôt qu'une
    dépendance externe (scipy absent de l'environnement) : le montant étant
    une hard rule, une composante ne regroupe que quelques opérations de
    même montant en pratique (voir TAILLE_MAX_COMPOSANTE_EXACTE) — la
    recherche exhaustive y est instantanée et garantit l'optimum exact,
    contrairement à une heuristique gloutonne.

    force (optionnel) : impose que CE candidat précis fasse partie de la
    solution — utilisé pour tester "si on impose ce candidat pour cette
    opération fiscale, quel est le meilleur total atteignable pour le reste
    de la composante ?" (voir reconcile_operations, détection d'ambiguïté
    globale : comparer ce total contraint au véritable optimum révèle si un
    candidat concurrent est une vraie alternative ou un choix nettement
    moins bon déjà mieux exploité ailleurs).

    Retourne (affectation: dict[fiscal_op -> MatchCandidate], score_total).
    """
    fiscaux = sorted({c.fiscal_op for c in candidats}, key=id)
    comptables = {c.accounting_op for c in candidats}

    if len(fiscaux) > TAILLE_MAX_COMPOSANTE_EXACTE or len(comptables) > TAILLE_MAX_COMPOSANTE_EXACTE:
        return _matching_glouton_composante(candidats, force=force)

    voisins = defaultdict(list)
    for c in candidats:
        voisins[c.fiscal_op].append(c)

    memo = {}

    def resoudre(i, utilises):
        if i == len(fiscaux):
            return 0.0, ()
        cle = (i, utilises)
        if cle in memo:
            return memo[cle]

        fop = fiscaux[i]
        options = []

        if force is None or force.fiscal_op is not fop:
            score_suite, choix_suite = resoudre(i + 1, utilises)
            options.append((score_suite, ((fop, None),) + choix_suite))

        for cand in voisins[fop]:
            if cand.accounting_op in utilises:
                continue
            if force is not None and force.fiscal_op is fop and cand is not force:
                continue  # force impose CE candidat précis pour ce fiscal_op
            score_suite, choix_suite = resoudre(i + 1, utilises | {cand.accounting_op})
            options.append((cand.scores.global_score + score_suite, ((fop, cand),) + choix_suite))

        meilleur = max(options, key=lambda t: t[0]) if options else (float("-inf"), ())
        memo[cle] = meilleur
        return meilleur

    total, choix = resoudre(0, frozenset())
    affectation = {fop: cand for fop, cand in choix if cand is not None}
    return affectation, total


def _resultats_composante(candidats: list) -> list:
    """
    Traite UNE composante connexe : résout le matching optimal global, puis
    détermine pour chaque opération fiscale affectée si son choix est
    réellement ambigu au niveau du graphe entier (voir
    _resoudre_composante_optimale). Retourne la liste de ReconciliationResult
    pour toutes les opérations (fiscales ET comptables) de cette composante.
    """
    affectation_opt, total_opt = _resoudre_composante_optimale(candidats)
    fiscaux = sorted({c.fiscal_op for c in candidats}, key=id)
    comptables = {c.accounting_op for c in candidats}

    resultats = []
    comptables_geres = set()

    for fop in fiscaux:
        cand_choisi = affectation_opt.get(fop)
        candidats_de_fop = [c for c in candidats if c.fiscal_op is fop]

        if cand_choisi is None:
            # Non retenu par l'optimum global : aucune de ses correspondances
            # possibles ne s'intègre dans une solution proche de l'optimum
            # (voir CAS G002 — un concurrent mieux placé a préempté sa seule
            # option). Pas d'ambiguïté à envisager ici : FISCAL_ONLY direct.
            resultats.append(ReconciliationResult(
                statut="FISCAL_ONLY",
                fiscal_op=fop,
                accounting_op=None,
                scores=None,
                commentaire="Opération présente sur la plateforme fiscale, non retrouvée en comptabilité "
                            "(aucun candidat compatible avec l'affectation optimale globale).",
            ))
            continue

        if len(candidats_de_fop) == 1:
            # Un seul candidat possible pour cette opération fiscale : rien à
            # comparer, pas besoin de ré-invoquer le solveur (court-circuit
            # de performance — voir Documentation, immense majorité des cas
            # réels puisque le montant est déjà un filtre très sélectif).
            candidats_proches = [cand_choisi]
        else:
            # CHANGEMENT (résolution globale des candidats, "étape suivante")
            # : pour chaque candidat concurrent de cette opération fiscale,
            # on FORCE son utilisation et on résout à nouveau la composante.
            # Si le total obtenu reste proche (< MARGE_AMBIGUITE) du véritable
            # optimum, ce concurrent est une alternative réellement quasi
            # équivalente (CAS A, ambiguïté réelle — ex. T006/D006/D007,
            # ou G005). Si le total chute nettement, ce concurrent est en
            # réalité déjà mieux exploité ailleurs par une autre opération
            # fiscale (CAS B — ex. le cas DUPLICAT FOURNISSEUR, G003, G004) :
            # pas d'ambiguïté, le choix de l'optimum est confiant.
            candidats_proches = []
            for cand in candidats_de_fop:
                if cand is cand_choisi:
                    candidats_proches.append(cand)  # coût nul par définition (c'est déjà le choix optimal)
                    continue
                _, total_force = _resoudre_composante_optimale(candidats, force=cand)
                if (total_opt - total_force) < MARGE_AMBIGUITE:
                    candidats_proches.append(cand)

        if len(candidats_proches) > 1:
            for cand in candidats_proches:
                comptables_geres.add(cand.accounting_op)
            noms = ", ".join(_describe_candidat(cand) for cand in candidats_proches)
            resultats.append(ReconciliationResult(
                statut="REVIEW",
                fiscal_op=fop,
                accounting_op=None,
                scores=None,
                candidates=candidats_proches,
                commentaire=(
                    f"{len(candidats_proches)} candidats quasi équivalents dans l'affectation globale "
                    f"(écart < {MARGE_AMBIGUITE} points) — vérification humaine nécessaire : {noms}"
                ),
            ))
            continue

        comptables_geres.add(cand_choisi.accounting_op)
        cand_choisi.fiscal_op.matched = True
        cand_choisi.accounting_op.matched = True
        statut, commentaire = _classifier(
            cand_choisi.scores.global_score, cand_choisi.scores.supplier_score,
            cand_choisi.scores.ecart_montant_millimes, cand_choisi.scores.est_rectification,
        )
        resultats.append(ReconciliationResult(
            statut=statut,
            fiscal_op=cand_choisi.fiscal_op,
            accounting_op=cand_choisi.accounting_op,
            scores=cand_choisi.scores,
            candidates=[cand_choisi],
            commentaire=commentaire,
        ))

    for aop in comptables:
        if aop not in comptables_geres:
            resultats.append(ReconciliationResult(
                statut="COMPTA_ONLY",
                fiscal_op=None,
                accounting_op=aop,
                scores=None,
                commentaire="Opération présente en comptabilité, non retrouvée sur la plateforme fiscale "
                            "(aucun candidat compatible avec l'affectation optimale globale).",
            ))

    return resultats


def _filtrer_hors_perimetre(ops: list, source: str) -> tuple:
    """
    Écarte du pool de matching les opérations hors périmètre du moteur RS
    (décision métier confirmée, "Chantier 3 — DMI/RECTIF et filtrage
    métier") — AVANT generate_candidates, comme demandé ("le point le plus
    en amont possible") : candidate_generator ne voit donc JAMAIS une
    opération DMI, elle ne peut ni générer ni recevoir un candidat.

    Deux règles DISTINCTES selon la source, car le vocabulaire de type
    diffère (voir core/operation_types.py) :
      - DS  : is_rs_reconcilable(extra["type_operation"]) — colonne
        optionnelle (RS/DMI/RECTIF/vide) ; vide/absente => reconciliable
        (comportement historique inchangé pour la quasi-totalité des
        fichiers réels, qui n'ont pas cette colonne). DMI et RECTIF côté DS
        (écritures comptables, jamais une déclaration RS primaire) sont
        exclus.
      - TEJ : is_dmi(extra["statut"]) uniquement — un ANNULÉ est déjà
        exclu bien plus en amont (core/matcher.py::_est_annule, avant même
        la construction des Operation, inchangé) ; un RECTIFIE N'EST PAS
        exclu ici : il doit rester un candidat de matching valide (voir
        core/operation_types.py::is_rectification, exploitée séparément
        par le scorer/classifier pour forcer REVIEW sans jamais l'écarter
        du pool).

    Retourne (ops_reconciliables, resultats_hors_perimetre) — les seconds
    au statut HORS_PERIMETRE_TYPE, plutôt que de laisser une opération DMI
    produire artificiellement un COMPTA_ONLY/FISCAL_ONLY générique.

    CHANGEMENT (rectification sémantique demandée, "Chantier 3 —
    rectification finale") : HORS_PERIMETRE_TYPE désigne explicitement une
    EXCLUSION MÉTIER VOLONTAIRE (type DMI, ou RECTIF côté DS) — ce n'est en
    aucun cas une anomalie ni un rejet. Elle ne doit jamais être comptée
    parmi FISCAL_ONLY/COMPTA_ONLY/REVIEW ni présentée comme telle : elle
    reste une catégorie de statut strictement séparée (voir son propre
    compteur "operations_hors_perimetre_type" dans les stats, et son propre
    libellé/couleur côté UI et export). Le commentaire généré ci-dessous
    reflète ce choix de vocabulaire.
    """
    reconciliables, hors_perimetre_ops = [], []
    for op in ops:
        if source == "DS":
            exclu = not is_rs_reconcilable(op.extra.get("type_operation"))
        else:
            exclu = is_dmi(op.extra.get("statut"))
        (hors_perimetre_ops if exclu else reconciliables).append(op)

    resultats = []
    for op in hors_perimetre_ops:
        cle_type = "type_operation" if source == "DS" else "statut"
        valeur_type = op.extra.get(cle_type)
        commentaire = (
            f"Exclusion métier volontaire du moteur de rapprochement RS (type « {valeur_type} »), "
            "jamais proposée à la génération de candidats — ce n'est pas une anomalie, "
            "voir core/operation_types.py."
        )
        if source == "DS":
            resultats.append(ReconciliationResult(
                statut="HORS_PERIMETRE_TYPE", fiscal_op=None, accounting_op=op, scores=None,
                commentaire=commentaire,
            ))
        else:
            resultats.append(ReconciliationResult(
                statut="HORS_PERIMETRE_TYPE", fiscal_op=op, accounting_op=None, scores=None,
                commentaire=commentaire,
            ))
    return reconciliables, resultats


def reconcile_operations(fiscal_ops: list, accounting_ops: list, registry: SupplierRegistry = None,
                          scope: AnalysisScope = None) -> list:
    """
    Cœur du moteur : résolution GLOBALE par composante connexe du graphe
    biparti de candidats (voir _composantes_connexes,
    _resoudre_composante_optimale, _resultats_composante), et non plus une
    assignation gloutonne locale ligne par ligne. Une opération comptable
    déjà assignée dans une composante n'est jamais réutilisée par une autre
    (règle métier validée, §12, matching 1-to-1) — garanti structurellement
    puisque chaque composante affecte chacun de ses nœuds au plus une fois.

    Toute opération fiscale ou comptable qui n'apparaît dans AUCUN candidat
    du tout (aucune opération de montant compatible dans l'autre source,
    dans la fenêtre de délai) est triviale : FISCAL_ONLY / COMPTA_ONLY
    immédiats, sans passer par une composante.

    CHANGEMENT (décision métier confirmée, "gestion des écarts de montant
    de 0,001 avec validation humaine") : generate_candidates accepte ici un
    écart de montant allant jusqu'à ECART_MONTANT_MAX_MILLIEMES (1 millime,
    0,001) — EXCEPTION UNIQUE et étroite à la hard rule montant exact, au-
    delà de laquelle le rejet reste total comme avant. Un candidat à 1
    millime d'écart est TOUJOURS forcé en REVIEW par _classifier (voir plus
    haut, raison ECART_MONTANT_0_001), jamais promu automatiquement — ce
    module ne fait qu'élargir le pool de candidats, jamais la décision.

    CHANGEMENT (décision métier confirmée, "ne pas confondre période
    d'analyse DS et fenêtre de matching") : `scope` (optionnel, défaut None
    = comportement IDENTIQUE à avant pour tout appelant existant qui ne le
    passe pas — voir core/analysis_scope.py::AnalysisScope) restreint
    UNIQUEMENT quels FISCAL_ONLY sont retenus dans le résultat final : un
    certificat TEJ actif sans correspondant DS ne devient FISCAL_ONLY que
    si sa date tombe dans la période DS analysée (scope.is_eligible_for_
    fiscal_only) — sinon il est simplement absent des résultats ("ignoré
    hors période", pas un nouveau statut). generate_candidates et le
    solveur de composantes restent COMPLÈTEMENT inchangés par `scope` : un
    certificat TEJ hors période DS reste un candidat de matching valide
    tant qu'il est dans la fenêtre de retard (voir DATE_WINDOW_MAX_DAYS),
    exactement comme avant — seule la classification FISCAL_ONLY finale est
    filtrée, jamais la génération de candidats ni le scoring.

    CHANGEMENT (décision métier confirmée, "Chantier 3 — DMI/RECTIF et
    filtrage métier") : `fiscal_ops`/`accounting_ops` sont d'abord filtrées
    par _filtrer_hors_perimetre (voir sa docstring) — les opérations DMI
    (ou de type DS non reconnu) sont retirées du pool AVANT
    generate_candidates et ressortent directement en HORS_PERIMETRE_TYPE.
    Absence de classification de type (cas de la quasi-totalité des
    fichiers réels) => rien n'est filtré, comportement historique inchangé.
    """
    registry = registry or SupplierRegistry()

    fiscal_ops, resultats_hors_perimetre_tej = _filtrer_hors_perimetre(fiscal_ops, source="TEJ")
    accounting_ops, resultats_hors_perimetre_ds = _filtrer_hors_perimetre(accounting_ops, source="DS")

    candidates_map = generate_candidates(fiscal_ops, accounting_ops, max_amount_gap_milliemes=ECART_MONTANT_MAX_MILLIEMES)

    all_candidates = []
    for fop, pool in candidates_map.items():
        for aop in pool:
            scores = score_candidate(fop, aop, registry)
            all_candidates.append(MatchCandidate(fop, aop, scores))

    results = list(resultats_hors_perimetre_tej) + list(resultats_hors_perimetre_ds)

    if all_candidates:
        for composante in _composantes_connexes(all_candidates):
            results.extend(_resultats_composante(composante))

    fiscaux_dans_composantes = {c.fiscal_op for c in all_candidates}
    comptables_dans_composantes = {c.accounting_op for c in all_candidates}

    for fop in fiscal_ops:
        if fop not in fiscaux_dans_composantes:
            results.append(ReconciliationResult(
                statut="FISCAL_ONLY",
                fiscal_op=fop,
                accounting_op=None,
                scores=None,
                commentaire="Opération présente sur la plateforme fiscale, non retrouvée en comptabilité.",
            ))

    for aop in accounting_ops:
        if aop not in comptables_dans_composantes:
            results.append(ReconciliationResult(
                statut="COMPTA_ONLY",
                fiscal_op=None,
                accounting_op=aop,
                scores=None,
                commentaire="Opération présente en comptabilité, non retrouvée sur la plateforme fiscale.",
            ))

    if scope is not None:
        # Filtre final, unique point d'application : un FISCAL_ONLY (qu'il
        # provienne de la boucle triviale ci-dessus ou d'un fop non retenu
        # par l'affectation optimale d'une composante, voir
        # _resultats_composante) hors période DS est retiré des résultats —
        # jamais transformé en un autre statut, simplement absent ("ignoré
        # hors période"). N'affecte aucun autre statut.
        results = [
            r for r in results
            if not (r.statut == "FISCAL_ONLY" and not scope.is_eligible_for_fiscal_only(r.fiscal_op.op_date))
        ]

    return results


def signaler_certificats_annules(results: list, annule_ops: list) -> list:
    """
    Requalifie un COMPTA_ONLY en CERTIFICAT_TEJ_ANNULE quand un certificat
    TEJ existe au MÊME montant EXACT (hard rule stricte — même précision,
    AUCUNE tolérance, y compris la tolérance d'écart de 1 millime du
    rapprochement principal, volontairement non appliquée ici — voir plus
    bas) dans la fenêtre de délai déjà validée, mais est ANNULÉ (voir
    core/matcher.py::_extraire_certificats_annules) — décision métier
    confirmée, "cas ANNULÉ/RECTIFIÉ" : un COMPTA_ONLY générique ne permet
    pas de distinguer une opération réellement absente côté TEJ d'une
    opération dont le certificat existe mais a été annulé, ce qui est une
    information différente et importante pour l'auditeur.

    CHANGEMENT (décision métier confirmée, "gestion des écarts de montant
    de 0,001 avec validation humaine") : un DS peut désormais rester REVIEW
    (candidat à 1 millime d'écart, ex. certificat RECTIFIÉ) plutôt que
    COMPTA_ONLY. Si un certificat ANNULÉ existe EN PLUS au montant EXACT de
    ce DS, l'information est AJOUTÉE au commentaire de la REVIEW SANS
    changer son statut ni la fusionner avec CERTIFICAT_TEJ_ANNULE (voir
    règle métier validée, "ne pas fusionner ANNULE et ECART_MONTANT_0_001",
    cas réel IMPRIMEX client de test : DS 10,835 doit voir à la fois le candidat
    RECTIFIÉ à 10,834 [REVIEW/ECART_MONTANT_0_001] ET le certificat annulé
    à 10,835 exact, sans que l'un masque l'autre).

    Réutilise generate_candidates SANS élargir le montant (max_amount_gap_
    milliemes=0, comportement par défaut) : la détection d'un certificat
    annulé répond à une question différente ("existe-t-il EXACTEMENT ce
    montant, annulé, ailleurs ?") et ne doit jamais se confondre avec la
    tolérance de 1 millime du rapprochement principal. Mute `results` en
    place et le retourne aussi par commodité. Ne touche jamais un vrai
    MATCH ni un FISCAL_ONLY.
    """
    cibles = [
        r.accounting_op for r in results
        if r.accounting_op is not None and r.statut in ("COMPTA_ONLY", "REVIEW")
    ]
    if not annule_ops or not cibles:
        return results

    candidats_annules = generate_candidates(annule_ops, cibles)  # {annule_op: [accounting_op, ...]}, montant EXACT
    aop_vers_annules = defaultdict(list)
    for annule_op, aops in candidats_annules.items():
        for aop in aops:
            aop_vers_annules[aop].append(annule_op)

    for r in results:
        if r.accounting_op not in aop_vers_annules:
            continue
        noms = ", ".join(
            f"« {op.raw_name} » ({op.op_date.strftime('%d/%m/%Y')}, {op.amount:,.3f}, {op.extra.get('statut', 'ANNULÉ')})"
            for op in aop_vers_annules[r.accounting_op]
        )
        if r.statut == "COMPTA_ONLY":
            r.statut = "CERTIFICAT_TEJ_ANNULE"
            r.commentaire = (
                f"Un certificat TEJ existe au même montant ({r.accounting_op.amount:,.3f}) mais est ANNULÉ : {noms} "
                "— ne compte pas comme une correspondance valide, vérification recommandée."
            )
        elif r.statut == "REVIEW":
            r.commentaire += (
                f" | Par ailleurs, un certificat TEJ existe au montant EXACT ({r.accounting_op.amount:,.3f}) mais "
                f"est ANNULÉ : {noms} — information contextuelle distincte, ne remplace pas la vérification du "
                "candidat à 1 millime d'écart ci-dessus."
            )
    return results


def _decrire_taux(op: Operation) -> str:
    taux = calculer_taux(op.extra.get("htva"), op.amount)
    if taux is None:
        return f"« {op.raw_name} » ({op.op_date.strftime('%d/%m/%Y')}, {op.amount:,.3f})"
    return f"« {op.raw_name} » ({op.op_date.strftime('%d/%m/%Y')}, {op.amount:,.3f}, taux calculé {taux * 100:.2f}%)"


def signaler_hors_taux(results: list, hors_taux_ops: list, taux_cible: float) -> list:
    """
    Requalifie un COMPTA_ONLY en HORS_TAUX_SELECTIONNE quand un certificat
    TEJ ACTIF (ni annulé, ni illisible) existe au MÊME montant (hard rule
    stricte, aucune tolérance) dans la fenêtre de délai déjà validée, mais a
    été exclu de CETTE analyse car son taux RS/HTVA calculé ne correspond
    pas au taux sélectionné (voir core/reconciliation.py::reconcile) —
    décision métier confirmée : un COMPTA_ONLY générique ne permet pas à
    l'auditeur de distinguer une opération réellement absente côté TEJ
    d'une opération qui existe mais appartient à une autre catégorie de
    taux (auquel cas la bonne action est de relancer une analyse avec
    l'autre taux, pas de conclure à une anomalie comptable) — voir l'écart
    observé sur les fichiers réels client de test (14 puis 21 "COMPTA_ONLY" qui
    étaient en réalité hors du taux sélectionné pour l'analyse en cours).

    Réutilise generate_candidates (mêmes hard rules que partout ailleurs,
    aucune nouvelle règle de montant/délai). Appelée APRÈS
    signaler_certificats_annules : ne retouche donc jamais un résultat déjà
    requalifié en CERTIFICAT_TEJ_ANNULE — les deux catégories restent
    mutuellement exclusives par construction (annule_ops et hors_taux_ops
    sont des listes disjointes, voir core/matcher.py et reconcile()).
    """
    compta_only_ops = [r.accounting_op for r in results if r.statut == "COMPTA_ONLY" and r.accounting_op is not None]
    if not hors_taux_ops or not compta_only_ops:
        return results

    candidats_hors_taux = generate_candidates(hors_taux_ops, compta_only_ops)  # {hors_taux_op: [accounting_op, ...]}
    aop_vers_hors_taux = defaultdict(list)
    for op, aops in candidats_hors_taux.items():
        for aop in aops:
            aop_vers_hors_taux[aop].append(op)

    for r in results:
        if r.statut != "COMPTA_ONLY" or r.accounting_op not in aop_vers_hors_taux:
            continue
        noms = ", ".join(_decrire_taux(op) for op in aop_vers_hors_taux[r.accounting_op])
        r.statut = "HORS_TAUX_SELECTIONNE"
        r.commentaire = (
            f"Un certificat TEJ existe au même montant ({r.accounting_op.amount:,.3f}) mais appartient à un "
            f"autre taux RS que celui sélectionné pour cette analyse ({taux_cible * 100:.2f}%) : {noms} — "
            "relancer une analyse avec l'autre taux plutôt que de conclure à une opération absente côté TEJ."
        )
    return results


def signaler_delais_paiement(results: list) -> int:
    """
    Contrôle de délai de paiement côté TEJ (date de création vs date de
    paiement, > DELAI_MAX_PAIEMENT_JOURS = risque de pénalité) — règle métier
    documentée, appliquée par l'ancien moteur mais plus branchée depuis le
    passage au moteur bidirectionnel (core/rules.py::check_payment_delay
    n'était plus appelée nulle part). CORRECTION : rebranchée ici.

    Purement INFORMATIF, comme avant : ajoute un avertissement au
    commentaire, ne change JAMAIS le statut de rapprochement (le délai de
    déclaration ne dit rien sur la correspondance DS<->TEJ elle-même).
    Silencieux si la date de création TEJ est absente (colonne optionnelle).
    Mute `results` en place ; retourne le nombre de certificats signalés.
    """
    nb = 0
    for r in results:
        op = r.fiscal_op
        if op is None:
            continue
        valide, delai = check_payment_delay(op.extra.get("date_creation"), op.op_date)
        if valide is False:
            nb += 1
            r.commentaire += (
                f" | ⚠ Risque de pénalité côté TEJ : paiement {delai} jours après la création "
                f"(délai max {DELAI_MAX_PAIEMENT_JOURS} jours)"
            )
    return nb


def fusionner_analyses(analyses: list) -> tuple:
    """
    Fusionne plusieurs analyses reconcile() — une par feuille DS, chacune
    avec son propre taux RS — en un seul rapport (fonctionnalité "plusieurs
    feuilles DS, une par taux, fusionnées dans un même rapport").

    analyses : liste de (libelle_feuille, results, stats).

    Chaque analyse reste indépendante (même moteur, mêmes hard rules, son
    propre taux et sa propre période DS) ; la fusion ne fait que :
      1. étiqueter chaque résultat avec sa feuille (ReconciliationResult.feuille) ;
      2. dédoublonner les certificats TEJ vus par plusieurs analyses. Un
         certificat dont le taux n'est pas vérifiable (HTVA absent) n'est
         exclu par aucun filtre de taux : il entre donc dans le pool de
         CHAQUE feuille. S'il est rapproché (MATCH/REVIEW) dans une feuille,
         ses FISCAL_ONLY des autres feuilles sont retirés ; s'il est
         FISCAL_ONLY partout, il n'est gardé qu'une fois.
      3. sommer les compteurs numériques des stats (les lignes TEJ annulées
         ou illisibles sont comptées une seule fois : même fichier TEJ).

    Retourne (results_fusionnes, stats_fusionnees).
    """
    tej_rapproches = set()
    for _, results, _ in analyses:
        for r in results:
            if r.statut != "FISCAL_ONLY" and r.fiscal_op is not None:
                tej_rapproches.add(r.fiscal_op.index)

    fusion, fiscal_only_vus = [], set()
    for libelle, results, _ in analyses:
        for r in results:
            r.feuille = libelle
            if r.statut == "FISCAL_ONLY" and r.fiscal_op is not None:
                idx = r.fiscal_op.index
                if idx in tej_rapproches or idx in fiscal_only_vus:
                    continue
                fiscal_only_vus.add(idx)
            fusion.append(r)

    stats = {}
    cles_fichier_tej = {"lignes_tej_annulees", "lignes_tej_montant_illisible"}
    for _, _, s in analyses:
        for cle, valeur in s.items():
            if not isinstance(valeur, (int, float)):
                continue
            if cle in cles_fichier_tej:
                stats[cle] = valeur
            else:
                stats[cle] = stats.get(cle, 0) + valeur
    stats["scopes"] = [(libelle, s.get("scope")) for libelle, _, s in analyses]
    return fusion, stats


def _appliquer_resultat_sur(cible: ReconciliationResult, source: ReconciliationResult) -> None:
    """
    Copie le contenu de `source` (fraîchement recalculé) sur l'objet `cible`
    déjà référencé ailleurs (ui/main_window.py::results_cache,
    ui/review_dialog.py::review_results, dialog.decisions...) — mutation EN
    PLACE, jamais de remplacement d'objet ni de réordonnancement d'une
    liste, pour que toute la chaîne UI en aval (qui retrouve/compare des
    résultats par id()) voie la mise à jour automatiquement sans câblage
    supplémentaire.
    """
    cible.statut = source.statut
    cible.fiscal_op = source.fiscal_op
    cible.accounting_op = source.accounting_op
    cible.scores = source.scores
    cible.candidates = source.candidates
    cible.commentaire = source.commentaire


def _reoptimiser_composantes_impactees(results: list, registry: SupplierRegistry, deja_decides: set) -> None:
    """
    Réoptimisation LOCALE des composantes de matching potentiellement
    affectées par une validation d'alias fournisseur (décision métier
    confirmée, "CHANTIER 1 — réoptimisation locale des composantes") — pas
    un reconcile() global : reconstruit le graphe biparti UNIQUEMENT à
    partir des opérations déjà présentes dans `results` (donc déjà dans le
    périmètre temporel/taux de l'analyse en cours — un opération scope-
    filtrée par reconcile() n'apparaît jamais dans `results`, elle ne peut
    donc jamais être "ressuscitée" ici), puis réutilise EXACTEMENT
    generate_candidates, score_candidate, _composantes_connexes et
    _resultats_composante — le MÊME moteur que reconcile_operations(),
    aucun second moteur de matching parallèle.

    Cas résolu : un alias validé peut changer quel candidat gagne
    l'affectation optimale d'une composante (ex. D012/D022/T013 — voir
    campagne de test réelle) ; l'ancien reappliquer_alias() ne rescorait
    que les REVIEW à candidat unique encore en attente et ne pouvait donc
    jamais "reprendre" un résultat déjà tombé en COMPTA_ONLY/FISCAL_ONLY ou
    déjà gagné par un concurrent.

    Portée volontairement EXCLUE (prudence, jamais de mutation partielle
    incorrecte) :
      - toute opération d'un résultat déjà décidé par un humain
        (id(r) dans deja_decides — jamais réoffert comme candidat à un
        concurrent, jamais écrasé) ;
      - toute opération d'une REVIEW AMBIGUË préexistante (plusieurs
        candidats) — sa résolution reste le mécanisme distinct
        resoudre_review(), jamais mélangé ici ;
      - toute composante dont la réoptimisation produirait une NOUVELLE
        ambiguïté ou un nombre de résultats différent du nombre d'objets
        existants disponibles pour cette composante — laissée strictement
        inchangée plutôt que mutée de façon incertaine (cas rarissime,
        hors du gap explicitement corrigé ici).

    Mute les objets ReconciliationResult déjà présents dans `results` EN
    PLACE (voir _appliquer_resultat_sur) — `results` lui-même n'est ni
    réordonné ni remplacé.
    """
    resultat_par_fiscal, resultat_par_accounting = {}, {}
    fiscal_ops, accounting_ops = [], []
    for r in results:
        if id(r) in deja_decides or len(r.candidates) > 1:
            continue
        if r.fiscal_op is not None:
            resultat_par_fiscal[id(r.fiscal_op)] = r
            fiscal_ops.append(r.fiscal_op)
        if r.accounting_op is not None:
            resultat_par_accounting[id(r.accounting_op)] = r
            accounting_ops.append(r.accounting_op)

    if not fiscal_ops or not accounting_ops:
        return

    candidates_map = generate_candidates(fiscal_ops, accounting_ops, max_amount_gap_milliemes=ECART_MONTANT_MAX_MILLIEMES)
    all_candidates = [
        MatchCandidate(fop, aop, score_candidate(fop, aop, registry))
        for fop, pool in candidates_map.items() for aop in pool
    ]
    if not all_candidates:
        return

    for composante in _composantes_connexes(all_candidates):
        nouveaux = _resultats_composante(composante)
        if any(len(n.candidates) > 1 for n in nouveaux):
            continue  # nouvelle ambiguïté émergente -> hors périmètre, composante laissée inchangée

        # Pool des objets ReconciliationResult EXISTANTS impliqués dans cette
        # composante (un objet peut être "propriétaire" à la fois d'un
        # fiscal_op et d'un accounting_op quand il représentait déjà une
        # paire matchée/REVIEW simple — dédupliqué via id()).
        pool, vus = [], set()
        for c in composante:
            for op, table in ((c.fiscal_op, resultat_par_fiscal), (c.accounting_op, resultat_par_accounting)):
                proprietaire = table.get(id(op))
                if proprietaire is not None and id(proprietaire) not in vus:
                    vus.add(id(proprietaire))
                    pool.append(proprietaire)

        if len(pool) != len(nouveaux):
            continue  # la composante a changé de "forme" -> hors périmètre de cette mutation en place

        for cible, nouveau in zip(pool, nouveaux):
            _appliquer_resultat_sur(cible, nouveau)


def reappliquer_alias(results: list, registry: SupplierRegistry, deja_decides: set = None) -> list:
    """
    Réapplique le Supplier Registry (déjà mis à jour, ex. juste après une
    validation humaine d'alias dans ui/review_dialog.py) aux résultats
    encore susceptibles de changer — pour qu'un alias tout juste validé
    profite immédiatement aux AUTRES opérations du même fournisseur dans la
    MÊME analyse, sans attendre une nouvelle exécution complète (décision
    métier confirmée, "une validation humaine doit avoir une portée
    persistante, pas seulement modifier le résultat courant").

    Deux passes, dans cet ordre :
      1. REVIEW à candidat UNIQUE -> rescoring direct (comportement
         historique inchangé, cas le plus fréquent et le plus rapide) ;
      2. réoptimisation LOCALE des composantes potentiellement impactées
         (voir _reoptimiser_composantes_impactees) — corrige le cas où un
         alias change quel candidat GAGNE l'affectation optimale d'une
         composante entière (ex. un résultat déjà tombé en COMPTA_ONLY/
         FISCAL_ONLY doit pouvoir être repris, ce que la passe 1 seule ne
         pouvait pas faire).

    Ne retouche JAMAIS :
      - une REVIEW ambiguë préexistante (plusieurs candidats — le choix
        reste humain, l'ambiguïté porte sur QUEL candidat, pas sur le nom
        du fournisseur) ;
      - un résultat déjà décidé par un humain, y compris un REJET explicite
        (id(result) dans deja_decides) — un alias validé ailleurs ne doit
        jamais écraser silencieusement une décision humaine négative ;
      - le montant, l'écart de montant (ECART_MONTANT_0_001), la fenêtre de
        délai, le taux, le périmètre temporel ou la règle 1-à-1 : toutes
        hard rules déjà figées par candidate_generator/_classifier/le
        périmètre de `results` lui-même, jamais recalculées ni contournées
        ici.
    Seul le signal fournisseur (via score_candidate/_classifier, tous deux
    déjà validés et inchangés) peut faire sortir un résultat du statut
    REVIEW ou changer une affectation. Mute `results` en place (aucun
    réordonnancement, aucun remplacement d'objet) et le retourne aussi par
    commodité.
    """
    deja_decides = deja_decides or set()
    # CHANGEMENT (analyse multi-feuilles DS, une par taux) : la réoptimisation
    # locale reconstruit un graphe de candidats à partir des résultats — elle
    # doit rester cantonnée à UNE feuille/taux à la fois, sinon une opération
    # DS d'une feuille à 1% pourrait être appariée à un certificat TEJ qui
    # n'a été retenu que pour la feuille à 10% (hard rule de taux contournée).
    # Analyse mono-feuille : un seul groupe (feuille=""), comportement inchangé.
    groupes_feuille = defaultdict(list)
    for r in results:
        groupes_feuille[r.feuille].append(r)

    for r in results:
        if id(r) in deja_decides:
            continue
        if r.statut != "REVIEW" or len(r.candidates) != 1:
            continue
        if r.fiscal_op is None or r.accounting_op is None:
            continue
        nouveau_score = score_candidate(r.fiscal_op, r.accounting_op, registry)
        nouveau_statut, nouveau_commentaire = _classifier(
            nouveau_score.global_score, nouveau_score.supplier_score,
            nouveau_score.ecart_montant_millimes, nouveau_score.est_rectification,
        )
        if nouveau_statut == "REVIEW":
            continue
        r.scores = nouveau_score
        r.candidates = [MatchCandidate(r.fiscal_op, r.accounting_op, nouveau_score)]
        r.statut = nouveau_statut
        r.commentaire = nouveau_commentaire + " — résolu automatiquement via alias fournisseur déjà validé"

    for groupe in groupes_feuille.values():
        _reoptimiser_composantes_impactees(groupe, registry, deja_decides)
    return results


def resoudre_review(result: ReconciliationResult, candidat_choisi: MatchCandidate = None) -> list:
    """
    Résout un ReconciliationResult REVIEW ambigu (`len(result.candidates) > 1`,
    `accounting_op=None`) suite à une validation humaine (voir
    ui/review_dialog.py) — fonction pure, testable sans passer par l'UI.

    - candidat_choisi fourni (doit être un élément de result.candidates) :
      retourne [MATCH_CERTAIN pour ce candidat] + [COMPTA_ONLY pour CHAQUE
      autre candidat de la liste] — ce n'est qu'à CE moment, une fois la
      décision humaine prise, que les candidats non retenus deviennent
      réellement des écarts "comptabilité uniquement" (voir point 2/6).
    - candidat_choisi=None (rejet total, aucun des candidats ne correspond) :
      retourne [FISCAL_ONLY pour result.fiscal_op] + [COMPTA_ONLY pour
      chaque candidat].
    """
    if candidat_choisi is not None:
        if candidat_choisi not in result.candidates:
            raise ValueError("candidat_choisi doit être un des candidats de ce résultat REVIEW.")
        resolus = [ReconciliationResult(
            statut="MATCH_CERTAIN",
            fiscal_op=candidat_choisi.fiscal_op,
            accounting_op=candidat_choisi.accounting_op,
            scores=candidat_choisi.scores,
            candidates=[candidat_choisi],
            commentaire="Confirmé manuellement par l'utilisateur parmi plusieurs candidats ambigus",
        )]
        for cand in result.candidates:
            if cand is not candidat_choisi:
                resolus.append(ReconciliationResult(
                    statut="COMPTA_ONLY",
                    fiscal_op=None,
                    accounting_op=cand.accounting_op,
                    scores=None,
                    commentaire="Candidat non retenu après vérification manuelle d'une correspondance ambiguë",
                ))
        return resolus

    resolus = [ReconciliationResult(
        statut="FISCAL_ONLY",
        fiscal_op=result.fiscal_op,
        accounting_op=None,
        scores=None,
        commentaire="Rejeté manuellement — aucun des candidats proposés ne correspond",
    )]
    for cand in result.candidates:
        resolus.append(ReconciliationResult(
            statut="COMPTA_ONLY",
            fiscal_op=None,
            accounting_op=cand.accounting_op,
            scores=None,
            commentaire="Candidat non retenu — correspondance ambiguë rejetée manuellement",
        ))
    return resolus


def reconcile(df_ds, map_ds: dict, df_tej, map_tej: dict, taux_cible: float,
              registry: SupplierRegistry = None) -> tuple:
    """
    Point d'entrée haut niveau : DS = comptabilité, TEJ = plateforme fiscale.
    Parse les deux fichiers via les fonctions de nettoyage existantes, filtre
    TEJ par taux_cible, puis lance reconcile_operations().

    CHANGEMENT (décision métier confirmée, "correction ciblée — un fichier
    DS ne contient qu'UN SEUL taux RS à la fois") : `taux_cible` (choisi par
    l'utilisateur dans l'UI, voir ui/main_window.py) est RESTAURÉ tel qu'il
    fonctionnait avant le chantier "gestion de plusieurs taux RS" — filtre
    les certificats TEJ dont le taux RS/HTVA calculé ne correspond pas au
    taux sélectionné (`check_taux_conforme`, TOLERANCE_TAUX inchangée,
    `is not False` pour ne jamais exclure un taux simplement non vérifiable
    — voir décision métier antérieure, toujours valide). Le chantier
    multi-taux partait d'une hypothèse invalidée depuis (un fichier DS réel
    mélangerait plusieurs taux) ; la vérification faite entre-temps confirme
    qu'un fichier DS ne contient qu'un seul taux à la fois, sélectionné par
    l'utilisateur — ce filtre reste donc la bonne portée pour l'analyse.

    Ce qui RESTE du chantier multi-taux (délibérément conservé, toujours
    utile) : le calcul informatif taux_tej/taux_ds/taux_coherent par
    candidat (core/scorer.py) — n'a jamais été un filtre ni un poids, reste
    une donnée d'explicabilité pure, désormais redondante avec taux_cible
    pour le côté TEJ (les deux devraient converger sur un fichier DS à taux
    unique) mais reste utile côté DS quand HTVA y est mappé.

    CHANGEMENT (décision métier confirmée, "cas ANNULÉ/RECTIFIÉ") : les
    certificats TEJ ANNULÉS (exclus du matching normal comme avant) sont
    capturés séparément (_extraire_certificats_annules) et utilisés en fin
    de fonction pour requalifier un COMPTA_ONLY en CERTIFICAT_TEJ_ANNULE
    quand un certificat annulé correspond exactement en montant et en date
    (voir signaler_certificats_annules) — jamais une tolérance de montant.

    Retourne (results: list[ReconciliationResult], stats: dict) — stats
    conserve le même format que core/matcher.py::reconcile_data pour ne pas
    casser les appelants existants, avec un compteur supplémentaire
    (certificats_annules_detectes).
    """
    from core.rules import check_taux_conforme

    registry = registry or SupplierRegistry()

    ds_records, nb_ds_montant_illisible, nb_ds_non_identifiables = _preparer_records_ds(df_ds, map_ds)
    tej_records, nb_tej_annulees, nb_tej_montant_illisible = _preparer_records_tej(df_tej, map_tej)
    annule_records = _extraire_certificats_annules(df_tej, map_tej)

    # Portée du fichier DS chargé : ne garder que les lignes TEJ dont le
    # taux calculé (RS/HTVA) correspond au taux sélectionné par
    # l'utilisateur. `is not False` (et non `if r[0]`) : check_taux_conforme
    # retourne (None, None) quand HTVA est indisponible (colonne non mappée
    # ou valeur illisible) — un champ optionnel manquant ne doit jamais
    # produire le même effet qu'un rejet métier explicite (taux
    # "non vérifiable" ≠ "non conforme") : seul un taux calculé et confirmé
    # DIFFÉRENT (conforme=False) exclut la ligne.
    # CHANGEMENT (décision métier confirmée, "distinguer COMPTA_ONLY
    # réellement inexpliqué de HORS_TAUX_SELECTIONNE") : les lignes TEJ
    # ACTIVES exclues par le filtre de taux (conforme=False, un taux calculé
    # ET différent — jamais un HTVA simplement indisponible) sont capturées
    # séparément AVANT d'être retirées de tej_records, pour être signalées
    # après coup plutôt que silencieusement absentes — voir signaler_hors_taux.
    hors_taux_records = [
        r for r in tej_records
        if check_taux_conforme(r["htva"], r["rs"], taux_cible)[0] is False
    ]
    tej_records = [
        r for r in tej_records
        if check_taux_conforme(r["htva"], r["rs"], taux_cible)[0] is not False
    ]
    # Même portée de taux appliquée aux certificats annulés : signaler un
    # certificat annulé d'une AUTRE catégorie de taux (coïncidence de
    # montant/date, rare) serait trompeur pour une analyse volontairement
    # scopée à un seul taux (voir check_taux_conforme ci-dessus, HTVA
    # indisponible = non exclu, seul un taux confirmé différent l'est).
    annule_records = [
        r for r in annule_records
        if check_taux_conforme(r.get("htva"), r["rs"], taux_cible)[0] is not False
    ]

    accounting_ops = _to_operations(ds_records, source="DS")
    fiscal_ops = _to_operations(tej_records, source="TEJ")
    annule_ops = _to_operations(annule_records, source="TEJ")
    hors_taux_ops = _to_operations(hors_taux_records, source="TEJ")

    # CHANGEMENT (décision métier confirmée, "ne pas confondre période
    # d'analyse DS et fenêtre de matching") : la période d'analyse est
    # calculée UNIQUEMENT à partir des dates DS valides (jamais depuis le
    # TEJ, référentiel historique potentiellement bien plus large — voir
    # core/analysis_scope.py). Bloque l'analyse avec un diagnostic explicite
    # si aucune date DS exploitable n'existe (voir règle métier validée §16)
    # plutôt que de continuer avec un périmètre indéterminé.
    dates_ds_valides = [op.op_date for op in accounting_ops if op.op_date is not None]
    if not dates_ds_valides:
        raise ValueError(
            "Aucune date DS valide trouvée dans le fichier chargé — impossible de déterminer la période "
            "d'analyse (date_debut_ds/date_fin_ds). Vérifiez le mapping de la colonne date du fichier DS."
        )
    scope = AnalysisScope(date_debut_ds=min(dates_ds_valides), date_fin_ds=max(dates_ds_valides))

    results = reconcile_operations(fiscal_ops, accounting_ops, registry, scope=scope)
    results = signaler_certificats_annules(results, annule_ops)
    results = signaler_hors_taux(results, hors_taux_ops, taux_cible)
    nb_delais_depasses = signaler_delais_paiement(results)

    # Diagnostic du périmètre (voir ui/main_window.py, bloc [PERIMETRE_DS]) :
    # combien de certificats TEJ actifs tombent dans la période DS analysée,
    # combien sont hors période, et parmi ceux hors période, combien ont
    # malgré tout servi de candidat valide (MATCH_*/REVIEW) grâce à la
    # fenêtre de retard — pour que le comportement reste auditable.
    tej_dans_periode = sum(1 for op in fiscal_ops if scope.is_eligible_for_fiscal_only(op.op_date))
    tej_hors_periode = len(fiscal_ops) - tej_dans_periode
    tej_hors_periode_utilises_comme_candidats = sum(
        1 for r in results
        if r.statut in ("MATCH_CERTAIN", "MATCH_PROBABLE", "REVIEW")
        and r.fiscal_op is not None
        and not scope.is_eligible_for_fiscal_only(r.fiscal_op.op_date)
    )

    stats = {
        "lignes_ds_montant_illisible": nb_ds_montant_illisible,
        "lignes_ds_non_identifiables": nb_ds_non_identifiables,
        "lignes_tej_annulees": nb_tej_annulees,
        "lignes_tej_montant_illisible": nb_tej_montant_illisible,
        "certificats_annules_detectes": sum(1 for r in results if r.statut == "CERTIFICAT_TEJ_ANNULE"),
        "operations_hors_taux_detectees": sum(1 for r in results if r.statut == "HORS_TAUX_SELECTIONNE"),
        "operations_hors_perimetre_type": sum(1 for r in results if r.statut == "HORS_PERIMETRE_TYPE"),
        "delais_paiement_depasses": nb_delais_depasses,
        "scope": scope,
        "tej_dans_periode_ds": tej_dans_periode,
        "tej_hors_periode_ds": tej_hors_periode,
        "tej_hors_periode_utilises_comme_candidats": tej_hors_periode_utilises_comme_candidats,
    }
    return results, stats
