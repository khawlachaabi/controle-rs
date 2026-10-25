from datetime import date

TOLERANCE_TAUX = 0.005  # marge unique pour absorber les arrondis de saisie

# CHANGEMENT : delai centralise ici (avant : 30 dans rules.py, mais 31 dans
# la logique dupliquee de matcher.py -- deux sources de verite differentes
# pour la meme regle metier).
DELAI_MAX_PAIEMENT_JOURS = 31

# CHANGEMENT (logique métier corrigée, 2e itération) : le taux n'est plus
# limité à Marchandise 1% / Loyer 10%. Le catalogue ci-dessous reprend les
# catégories officielles de retenue à la source fournies par l'utilisateur
# (chaque ligne : code, libellé, taux en fraction décimale). Il sert à
# peupler la liste déroulante de l'UI ; l'utilisateur garde la possibilité
# de saisir un taux personnalisé si sa catégorie n'y figure pas.
CATALOGUE_TAUX_RS = [
    ("4.1", "Honoraires Commission courtage & loyers (Résident)", 0.10),
    ("4.2", "Honoraires Commission courtage & loyers (non résident)", 0.15),
    ("4.3", "Honoraires Commission courtage & loyers (opérations d'export)", 0.03),
    ("5", "Honoraires PM & PP soumis au régime réel", 0.03),
    ("6", "Honoraires servis aux bureaux d'études exportateurs", 0.03),
    ("7", "Loyers d'hôtels", 0.05),
    ("8", "Intérêts des dépôts dans les comptes d'épargne privés", 0.20),
    ("9", "Revenus des autres capitaux mobiliers", 0.00),
    ("9.1", "Revenus des autres capitaux mobiliers aux résidents", 0.20),
    ("9.2", "Revenus des autres capitaux mobiliers aux non résidents", 0.20),
    ("10", "Revenus VM servis aux non résidents", 0.15),
    ("11", "Revenus des actions", 0.10),
    ("11.1", "Revenus des actions (PP résidents)", 0.10),
    ("11.2", "Revenus des actions (PP et PM non résidents)", 0.10),
    ("12.1", "Jeton de présence (résident)", 0.20),
    ("12.2", "Jeton de présence (non résident)", 0.20),
    ("13", "Rémunérations payées aux salariés", 0.15),
    ("14", "Intérêts des prêts aux banques", 0.03),
    ("15.1", "Prix de cession déclaré (Non Résident PP)", 0.03),
    ("15.2", "Prix de cession déclaré (Non Résident PM)", 0.03),
    ("15.3", "Prix de cession déclaré (biens fonciers)", 0.15),
    ("16.1", "RS/Marchés (IS 10%)", 0.01),
    ("16.2", "RS/Marchés (IS 20%)", 0.01),
    ("16.3", "Autres RS/Marchés", 0.02),
    ("17", "RS/TVA 25%", 0.25),
    ("18", "RS/TVA 100%", 1.00),
    ("19", "Honoraires trav constructions", 0.00),
    ("20", "Avance facturée aux PM à l'IR (régime forfaitaire)", 0.01),
    ("21.1", "Plus value de cession des actions PM", 0.03),
    ("21.2", "Plus value de cession des actions PP", 0.05),
    ("22", "Autres revenus (résidents paradis fiscaux)", 0.00),
    ("23", "Rémunérations/paradis fiscaux", 0.25),
    ("24", "Commissions/distributeur opérateurs de communications", 0.02),
]


def parser_taux_saisi(texte: str) -> float:
    """
    Taux tapé librement dans l'UI -> fraction décimale. La saisie est
    TOUJOURS lue comme un pourcentage : "1" = 1 %, "7,5" = "7.5%" = 7,5 %,
    "0.5" = 0,5 %.

    CORRECTION : avant, une valeur ≤ 1 était prise comme une fraction
    ("1" = 100 %, "0.5" = 50 %) et une valeur > 1 comme un pourcentage — un
    comptable tapant "1" pour 1 % lançait une analyse à 100 % (RS/TVA)
    sans aucune correspondance possible ni message d'erreur.

    Lève ValueError si le texte n'est pas un nombre strictement positif
    (un taux nul ou négatif produisait une analyse "valide" en apparence
    mais sans aucune correspondance possible).
    """
    nettoye = str(texte).strip().replace('%', '').replace(',', '.').strip()
    try:
        valeur = float(nettoye)
    except ValueError:
        raise ValueError(f"Taux invalide : « {texte} ». Tapez un pourcentage, ex: 1, 7.5 ou 7,5%.")
    if valeur <= 0:
        raise ValueError(f"Taux invalide : « {texte} ». Le taux doit être strictement positif.")
    if valeur > 100:
        raise ValueError(f"Taux invalide : « {texte} ». Un taux de retenue ne peut pas dépasser 100 %.")
    return valeur / 100


def calculer_taux(htva: float, rs: float):
    """
    Calcule le taux RS effectif (RS / HTVA) côté TEJ. Retourne None si le
    calcul est impossible (HTVA manquant, nul, ou négatif).

    CHANGEMENT (logique métier corrigée, 2e itération) : remplace
    check_rs_rate(), qui classait un taux en seulement 2 catégories fixes
    (Marchandise 1% / Loyer 10%). Le taux cible est maintenant choisi
    librement par l'utilisateur (liste déroulante CATALOGUE_TAUX_RS ou
    saisie libre) — voir check_taux_conforme() ci-dessous pour la
    comparaison.
    """
    if htva is None or rs is None or htva <= 0:
        return None
    return rs / htva


def check_taux_conforme(htva: float, rs: float, taux_cible: float):
    """
    Vérifie si le taux RS effectif (RS/HTVA) correspond au taux cible
    choisi par l'utilisateur, à TOLERANCE_TAUX près.

    Retourne (conforme: bool | None, taux_calcule: float | None).
    conforme est None si le taux n'a pas pu être calculé (montant invalide).
    """
    taux = calculer_taux(htva, rs)
    if taux is None:
        return None, None
    return abs(taux - taux_cible) <= TOLERANCE_TAUX, taux


def check_payment_delay(date_creation: date, date_paiement: date):
    """
    Vérifie si le paiement a dépassé le délai maximum après la création.

    Retourne (is_valid, delay_jours).

    CHANGEMENT : si une des deux dates manque, retourne (None, None) au
    lieu de (True, 0). Avant, "impossible à vérifier" était confondu avec
    "conforme" — un vrai retard avec une date manquante aurait été marqué
    silencieusement comme sans risque. Avec None explicite, l'appelant peut
    afficher "date manquante, non vérifiable" plutôt que "conforme".
    """
    if not date_creation or not date_paiement:
        return None, None

    delay = (date_paiement - date_creation).days
    is_valid = delay <= DELAI_MAX_PAIEMENT_JOURS
    return is_valid, delay


# ============================================================================
# Moteur de rapprochement bidirectionnel (record linkage) — Étapes 5 à 7.
#
# CHANGEMENT (décision métier confirmée, règles validées) : le montant reste
# une HARD RULE stricte (égalité après normalisation au millime, aucune
# tolérance métier GÉNÉRALE) — voir core/candidate_generator.py. La date
# n'est PAS une clé exacte mais une fenêtre bidirectionnelle + un signal de
# score continu : abs(date_A - date_B) <= DATE_WINDOW_MAX_DAYS (hard rule
# pour la fenêtre), puis date_score() ci-dessous pour la contribution au
# score global (soft signal). Aucun sens temporel n'est imposé entre TEJ et
# DS.
#
# CHANGEMENT (décision métier confirmée, "gestion des écarts de montant de
# 0,001 avec validation humaine") : EXCEPTION UNIQUE et volontairement
# étroite à la hard rule ci-dessus — un écart de EXACTEMENT 1 millime
# (0,001) entre TEJ et DS devient un candidat (au lieu d'être rejeté comme
# avant), mais ne peut JAMAIS devenir MATCH_CERTAIN ou MATCH_PROBABLE
# automatiquement : le classement force REVIEW, avec la raison explicite
# ECART_MONTANT_0_001 (voir core/reconciliation.py::_classifier). Ce n'est
# PAS une tolérance de montant générale : au-delà de 1 millime, le rejet
# reste total, exactement comme avant (voir generate_candidates,
# max_amount_gap_milliemes). Ne remplace ni ne modifie la comparaison
# stricte au millime elle-même — montant_en_milliemes() ci-dessous existe
# justement pour que cette comparaison reste fiable (entiers, pas de
# flottants approximatifs).
ECART_MONTANT_MAX_MILLIEMES = 1


def montant_en_milliemes(montant: float) -> int:
    """
    Représentation entière d'un montant en millièmes (ex: 10.835 -> 10835).
    Comparaison d'écarts de montant fiable, insensible aux imprécisions des
    flottants Python (ne jamais comparer deux montants via `abs(a - b) <
    0.001` directement — voir décision métier "gestion des écarts de
    montant de 0,001", qui exige une comparaison exacte au millime).
    """
    return round(montant * 1000)


DATE_WINDOW_MAX_DAYS = 58  # fenêtre bidirectionnelle — valeur de départ, à recalibrer sur des matches confirmés

# Seuils de classification du score global (0-100), à calibrer sur données réelles.
SEUIL_MATCH_CERTAIN = 90
SEUIL_MATCH_PROBABLE = 75
SEUIL_REVIEW = 50

# Marge minimale (en points de score global) entre le 1er et le 2e candidat
# encore disponible pour qu'une correspondance soit acceptée sans ambiguïté.
# Sous ce seuil : REVIEW, même si le score du 1er candidat est élevé (voir
# règle métier "marge entre 1er et 2e candidat").
MARGE_AMBIGUITE = 5

# CHANGEMENT (décision métier confirmée) : amount_score retiré du score
# global. Le montant reste une hard rule stricte (voir plus haut) — un
# candidat n'existe que si les montants sont déjà identiques au millime —
# mais n'a donc plus aucun pouvoir DISCRIMINANT entre candidats admissibles
# (il vaut toujours 1.0). Lui garder un poids fixe (50%) gonflait
# artificiellement le score de tout candidat même à fournisseur totalement
# différent (~72% de score "garanti" avant même de regarder le nom — vérifié
# par test : nom quasi sans rapport => 77,6% de score global, dans la zone
# MATCH_PROBABLE). Le montant reste affiché dans l'explication ("✓ Montant
# identique au millime") mais ne contribue plus aux points. Le score global
# ne porte donc plus que sur les signaux réellement discriminants :
# fournisseur et date. reference_score reste inactif tant qu'aucune relation
# fiable DS<->TEJ n'est démontrée (voir Documentation) ; son poids est alors
# redistribué (core/scorer.py divise par la somme des poids réellement actifs).
POIDS_FOURNISSEUR = 0.25
POIDS_DATE = 0.15
POIDS_REFERENCE = 0.10

# CHANGEMENT (décision métier confirmée) : garde-fou indépendant du score
# global. Un fournisseur trop peu similaire ne doit JAMAIS produire un
# MATCH_CERTAIN ou MATCH_PROBABLE automatique — même si le score global (qui
# ne porte plus que sur fournisseur+date) est élevé — car un nom très
# différent peut être un simple problème de saisie/langue (français/arabe,
# translittération, alias non encore validé) et pas nécessairement une
# fausse correspondance : voir core/reconciliation.py::_classifier, qui force
# REVIEW (jamais un rejet automatique) sous ce seuil, pour validation
# humaine. Valeur de départ, à calibrer sur des rapprochements confirmés —
# pas une vérité métier figée (voir Documentation).
SUPPLIER_MIN_SCORE = 0.40


def date_score(delta_days: int, max_days: int = DATE_WINDOW_MAX_DAYS) -> float:
    """
    Score de proximité temporelle (0 à 1) : 1.0 si même jour, décroît
    linéairement jusqu'à 0 à la limite de la fenêtre. N'est appelé que sur
    des candidats déjà dans la fenêtre (le rejet hors fenêtre se fait en
    amont, dans core/candidate_generator.py — hard rule, pas un score bas).
    """
    if delta_days is None:
        return 0.0
    return max(0.0, 1.0 - (delta_days / max_days))