"""
Calcul du score multi-critères d'un candidat de rapprochement — Étape 6.

Le montant est une hard rule (déjà garanti égal au millime par
candidate_generator) — il n'entre PAS dans le calcul du score global, car il
n'a plus de pouvoir discriminant entre candidats déjà admissibles (voir
core/rules.py, commentaire sur le retrait de POIDS_MONTANT). Il reste
affiché dans l'explication pour la transparence. Le fournisseur et la date
sont les seuls signaux "soft" combinés en un score global pondéré (voir
core/rules.py pour les poids et seuils).
"""

from rapidfuzz import fuzz

from core.models import Operation, SignalScores
from core.operation_types import is_rectification
from core.rules import (
    date_score as _date_score,
    calculer_taux,
    montant_en_milliemes,
    POIDS_FOURNISSEUR,
    POIDS_DATE,
    TOLERANCE_TAUX,
)
from core.supplier_registry import SupplierRegistry

SEUIL_NOM_SIMILAIRE = 0.75  # rapidfuzz normalisé 0-1 (75%, cohérent avec l'ancien SEUIL_FOURNISSEUR)


def supplier_score(fiscal_op: Operation, accounting_op: Operation, registry: SupplierRegistry) -> tuple:
    """
    Retourne (score 0-1, raison lisible, identity). Priorité, du plus fiable
    au moins fiable (voir règles métier validées, §8, et "validation
    fournisseur persistante et propagation automatique", §11) :
      1. alias validé dans le référentiel fournisseurs (résolution exacte) ;
      2. nom identique après normalisation (core/normalizer.py::clean_text) ;
      3. fuzzy matching en dernier recours (rapidfuzz token_sort_ratio).

    `identity` (voir core/models.py::SignalScores.supplier_identity) est
    PUREMENT INFORMATIF — jamais utilisé pour une décision ici ni ailleurs,
    seul le score (float) reste comparé à SUPPLIER_MIN_SCORE dans
    core/reconciliation.py::_classifier, inchangé. Distingue notamment
    "VALIDATED_ALIAS" (connaissance humaine déjà acquise, jamais réinventée
    par le fuzzy matching) de "FUZZY_SIMILAR_UNVALIDATED" (proximité
    textuelle qui n'a jamais été confirmée par un humain — Niveau 2 des
    règles métier validées, jamais suffisant seul pour écrire un alias
    permanent dans le Supplier Registry : seule une validation humaine
    explicite le fait, voir ui/review_dialog.py).
    """
    canon_fiscal = registry.resolve(fiscal_op.raw_name)
    canon_accounting = registry.resolve(accounting_op.raw_name)
    if canon_fiscal and canon_accounting and canon_fiscal == canon_accounting:
        return 1.0, f"✓ Fournisseur identifié via alias validé : {fiscal_op.raw_name} = {accounting_op.raw_name}", "VALIDATED_ALIAS"

    # CHANGEMENT (décision métier confirmée, "rien n'est obligatoire à part
    # les champs qui contribuent aux calculs") : le fournisseur devient
    # optionnel (voir core/matcher.py::_extraire_fournisseur) — un nom
    # absent d'un côté ou des deux donne clean_name="". Sans ce garde-fou,
    # fuzz.token_sort_ratio("", "") vaut 100 (chaînes vides "identiques"),
    # ce qui aurait fait matcher à tort deux opérations sans nom fournisseur
    # disponible d'AUCUN côté, juste parce qu'elles partagent un montant.
    # Score explicitement à 0 (jamais un rejet — juste "non comparable"),
    # laissé au garde-fou SUPPLIER_MIN_SCORE pour forcer REVIEW.
    if not fiscal_op.clean_name or not accounting_op.clean_name:
        return 0.0, "Nom fournisseur non disponible d'un côté (ou des deux) — impossible à comparer", "NOT_COMPARABLE"

    if fiscal_op.clean_name == accounting_op.clean_name:
        return 1.0, "Nom fournisseur identique après normalisation", "EXACT_NAME"

    ratio = fuzz.token_sort_ratio(fiscal_op.clean_name, accounting_op.clean_name) / 100.0
    if ratio >= SEUIL_NOM_SIMILAIRE:
        return ratio, f"Fournisseur similaire (score {ratio * 100:.0f}%) — jamais validé humainement", "FUZZY_SIMILAR_UNVALIDATED"
    return ratio, f"Fournisseur peu similaire (score {ratio * 100:.0f}%)", "FUZZY_DIFFERENT_UNVALIDATED"


def _taux_info(fiscal_op: Operation, accounting_op: Operation) -> tuple:
    """
    Taux RS (rs/htva) de chaque côté, quand HTVA est disponible — PUREMENT
    INFORMATIF (voir core/models.py::SignalScores et règles métier
    validées, "gestion de plusieurs taux RS dans une même analyse" : ne pas
    inventer de règle métier de pénalisation/rejet tant qu'elle n'est pas
    validée). Ne filtre jamais un candidat, ne pondère jamais le score.

    Retourne (taux_tej, taux_ds, coherent, raison_ou_None). coherent reste
    None si l'un des deux taux est indisponible (rien à comparer — absence
    de donnée, pas une alerte).
    """
    taux_tej = calculer_taux(fiscal_op.extra.get("htva"), fiscal_op.amount)
    taux_ds = calculer_taux(accounting_op.extra.get("htva"), accounting_op.amount)

    if taux_tej is None and taux_ds is None:
        return None, None, None, None
    if taux_tej is not None and taux_ds is not None:
        coherent = abs(taux_tej - taux_ds) <= TOLERANCE_TAUX
        if coherent:
            raison = f"ℹ Taux RS cohérents (TEJ {taux_tej * 100:.2f}% ≈ DS {taux_ds * 100:.2f}%)"
        else:
            raison = (
                f"ℹ Taux RS différents (TEJ {taux_tej * 100:.2f}% vs DS {taux_ds * 100:.2f}%) "
                "— information seule, non pénalisant"
            )
        return taux_tej, taux_ds, coherent, raison

    # Un seul côté connu : rien à comparer, mais reste utile pour un auditeur.
    seul = taux_tej if taux_tej is not None else taux_ds
    origine = "TEJ" if taux_tej is not None else "DS"
    return taux_tej, taux_ds, None, f"ℹ Taux RS {origine} ≈ {seul * 100:.2f}% (autre côté non disponible)"


def score_candidate(fiscal_op: Operation, accounting_op: Operation, registry: SupplierRegistry) -> SignalScores:
    """
    Combine supplier_score et date_score (les deux seuls signaux réellement
    discriminants) en un score global pondéré. amount_score ne contribue
    jamais au calcul (voir core/rules.py) — le montant reste affiché dans
    l'explication pour la transparence, mais ne pèse pas dans global_score.

    Le taux RS (voir _taux_info) est calculé et ajouté aux raisons à titre
    PUREMENT INFORMATIF — il n'entre à AUCUN degré dans poids_actifs ni
    global_score, quel que soit son résultat (cohérent, différent, ou
    indisponible).

    CHANGEMENT (décision métier confirmée, "gestion des écarts de montant
    de 0,001 avec validation humaine") : le montant N'EST PLUS forcément
    identique ici — core/candidate_generator.py peut désormais générer un
    candidat avec un écart d'exactement 1 millime (ECART_MONTANT_MAX_MILLIEMES).
    ecart_montant_millimes (0 ou 1) est calculé via montant_en_milliemes
    (comparaison entière, jamais un `abs(a - b) < 0.001` flottant
    approximatif) et exposé dans SignalScores — amount_score reflète cet
    écart pour l'affichage (1.0 si identique, 0.0 sinon) mais, comme avant,
    ne contribue à AUCUN degré à global_score. C'est
    core/reconciliation.py::_classifier qui, à partir de
    ecart_montant_millimes, force le statut REVIEW — jamais ce module.

    CHANGEMENT (décision métier confirmée, "Chantier 3 — DMI/RECTIF et
    filtrage métier") : est_rectification (voir core/operation_types.py::
    is_rectification, appliquée au statut TEJ) est calculé ici et exposé
    dans SignalScores, pour la même raison structurelle que
    ecart_montant_millimes — un déclencheur de classification lu par
    core/reconciliation.py::_classifier, jamais un poids ajouté au score.
    """
    delta = abs((accounting_op.op_date - fiscal_op.op_date).days)
    d_score = _date_score(delta)
    s_score, s_raison, s_identity = supplier_score(fiscal_op, accounting_op, registry)

    ecart_montant = abs(montant_en_milliemes(accounting_op.amount) - montant_en_milliemes(fiscal_op.amount))
    rectification = is_rectification(fiscal_op.extra.get("statut"))

    if ecart_montant == 0:
        raisons = ["✓ Montant identique au millime"]
    else:
        raisons = [
            f"⚠ ECART_MONTANT_0_001 : écart de montant de {ecart_montant} millime(s) entre "
            f"TEJ ({fiscal_op.amount:,.3f}) et DS ({accounting_op.amount:,.3f}) — validation humaine obligatoire"
        ]
    if rectification:
        raisons.append(f"⚠ RECTIFICATION_TEJ : le certificat TEJ retenu (« {fiscal_op.raw_name} ») est une "
                        "rectification (statut RECTIFIÉ) — validation humaine obligatoire")
    if delta == 0:
        raisons.append("✓ Même date exacte")
    else:
        raisons.append(f"Écart de date de {delta} jour(s)")
    raisons.append(s_raison)

    taux_tej, taux_ds, taux_coherent, raison_taux = _taux_info(fiscal_op, accounting_op)
    if raison_taux:
        raisons.append(raison_taux)

    poids_actifs = POIDS_FOURNISSEUR + POIDS_DATE
    global_score = (
        POIDS_FOURNISSEUR * s_score
        + POIDS_DATE * d_score
    ) / poids_actifs

    return SignalScores(
        amount_score=1.0 if ecart_montant == 0 else 0.0,
        supplier_score=s_score,
        date_score=d_score,
        date_delta_days=delta,
        global_score=round(global_score * 100, 1),
        raisons=raisons,
        taux_tej=taux_tej,
        taux_ds=taux_ds,
        taux_coherent=taux_coherent,
        ecart_montant_millimes=ecart_montant,
        supplier_identity=s_identity,
        est_rectification=rectification,
    )
