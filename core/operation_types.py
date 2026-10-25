"""
Classification métier centralisée des opérations DS/TEJ (décision métier
confirmée, "Chantier 3 — DMI/RECTIF et filtrage métier") : détermine si une
opération appartient au périmètre du moteur de rapprochement RS, AVANT la
génération de candidats.

Ne modifie AUCUNE règle de scoring/seuils/poids/fenêtre de dates/montant/
Supplier Registry/solveur — ce module répond à une question strictement
antérieure et indépendante ("cette opération doit-elle même être proposée
au moteur RS ?"), jamais à "avec quel score/statut correspond-elle ?".

Vocabulaire couvert (après normalisation via core.normalizer.clean_text,
insensible à la casse/aux accents/aux espaces) :
  - DS  : "RS" (opération RS normale, seule valeur reconciliable), "DMI",
    "RECTIF" (écritures comptables hors périmètre RS — une RECTIF côté DS
    est une écriture de correction, jamais une déclaration RS primaire),
    colonne optionnelle "type_operation" (voir core/ds_column_mapper.py).
    Absence de colonne mappée (type_value=None, cas de loin le plus
    fréquent sur des fichiers réels, y compris client de test) => is_rs_reconcilable
    reste True : comportement historique inchangé, zéro régression.
  - TEJ : "REÇUE" (actif, statut déjà obligatoire — voir
    core/parser.py::COLONNES_TEJ_STANDARD), "ANNULÉ" (déjà exclu du
    matching normal par core/matcher.py::_est_annule, qui délègue
    désormais à is_cancelled ci-dessous — comportement inchangé),
    "RECTIFIE"/"RECTIFIÉ" (rectification — reste un candidat de matching
    valide, mais ne peut jamais devenir MATCH_CERTAIN/MATCH_PROBABLE
    automatiquement, voir core/reconciliation.py::_classifier et
    core/scorer.py::score_candidate).

IMPORTANT : is_rs_reconcilable() est conçue pour la colonne DS
"type_operation" (vocabulaire RS/DMI/RECTIF/vide) — NE PAS l'appliquer au
statut TEJ ("REÇUE"/"ANNULÉ"/"RECTIFIE"), vocabulaire différent qui
produirait des faux exclus. Voir core/reconciliation.py::_filtrer_hors_perimetre,
qui applique la bonne fonction selon la source (DS vs TEJ).
"""

from core.normalizer import clean_text


def _normaliser_type(valeur) -> str:
    if valeur is None:
        return ""
    texte = str(valeur).strip()
    if not texte:
        return ""
    return clean_text(texte)


def is_dmi(type_value) -> bool:
    """Vrai si la valeur de type/statut désigne une opération DMI (hors périmètre RS)."""
    return _normaliser_type(type_value) == "DMI"


def is_rectification(type_value) -> bool:
    """
    Vrai si la valeur désigne une rectification (DS "RECTIF" ou TEJ
    "RECTIFIE"/"RECTIFIÉ" — le "É" accentué est normalisé par clean_text,
    donc tester le préfixe commun "RECTIF" suffit pour les deux graphies).
    """
    return "RECTIF" in _normaliser_type(type_value)


def is_cancelled(statut_value) -> bool:
    """Vrai si le statut TEJ désigne un certificat annulé (même règle que core/matcher.py::_est_annule)."""
    return "ANNUL" in _normaliser_type(statut_value)


def is_rs_reconcilable(type_value) -> bool:
    """
    Vrai si l'opération DS doit être proposée au moteur de rapprochement RS
    normal (candidate_generator inclus). Réservée à la colonne DS
    "type_operation" — voir docstring du module pour le vocabulaire TEJ,
    géré séparément.

    - type_value None ou vide (colonne "type_operation" non mappée, cas de
      loin le plus fréquent) => True, comportement historique inchangé.
    - "RS" => True.
    - "DMI", "RECTIF", ou toute valeur non reconnue => False, jamais
      envoyée automatiquement au moteur RS (voir statut
      HORS_PERIMETRE_TYPE, core/reconciliation.py::reconcile_operations) —
      "type inconnu => jamais RS-réconciliable" est une décision métier
      volontairement prudente, pas une omission.
    """
    normalise = _normaliser_type(type_value)
    return normalise in ("", "RS")
