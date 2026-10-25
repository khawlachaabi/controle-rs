"""
Suggestion (indicative uniquement) de mapping des colonnes d'un fichier DS
Comptable, quel que soit le logiciel comptable d'origine — voir décision
confirmée : le contrôle humain reste entier (ui/mapping_dialog.py::MappingDialog
s'ouvre systématiquement pour le DS, voir ui/main_window.py::_load_ds). Ce
module se contente de PRÉ-REMPLIR les listes déroulantes ; il n'applique
jamais de mapping seul et ne bloque jamais l'ouverture du dialogue.

Combine nom de colonne (synonymes extensibles ci-dessous) et quelques
signaux de contenu simples (texte/numérique/date, motif "RS/") pour les 3
rôles réellement requis par le moteur (core/parser.py::ROLES_DS). Rôles
optionnels (htva, tva, ttc, piece, folio) : nom de colonne seul, sans
heuristique de contenu — décision confirmée, "ne pas développer davantage
cette fonctionnalité", elle n'est plus jugée prioritaire.

Le moteur de rapprochement (core/matcher.py, candidate_generator.py,
scorer.py, reconciliation.py, rules.py) reste strictement inchangé et ne
dépend jamais du nom réel des colonnes ni du logiciel comptable d'origine.
"""
import re
import unicodedata
from typing import Optional

import pandas as pd

# Synonymes par rôle métier — EXTENSIBLE : ajouter un nouveau logiciel
# comptable ou un nouvel intitulé de colonne ne nécessite QUE d'ajouter une
# entrée ici. Rôles réellement requis par le moteur : fournisseur, rs,
# date_paiement (voir core/parser.py::ROLES_DS).
SYNONYMES_DS = {
    "fournisseur": [
        "libelle", "libelle de l'ecriture", "libelle ecriture", "description",
        "designation", "intitule", "objet", "nom", "tiers", "fournisseur",
        "client", "raison sociale", "frs", "clt", "nom/prenom", "beneficiaire",
        "compte libelle", "libelle operation", "detail",
    ],
    "date_paiement": [
        "date", "date ecriture", "date de l'ecriture", "date comptable",
        "date operation", "date piece", "date paie", "date reglement",
        "date paiement", "paie",
    ],
    "rs": [
        "rs", "retenue", "montant rs", "impot", "retenue a la source", "m-rs",
        "credit", "debit", "montant credit", "montant", "valeur", "net a payer",
        "montant operation",
    ],
    # Rôles optionnels : affichés dans MappingDialog "si disponibles"
    # (décision confirmée) uniquement comme confort — jamais obligatoires.
    "htva": ["ht", "htva", "montant ht", "base impot", "montant hors taxe", "base"],
    "tva": ["tva", "montant tva"],
    "ttc": ["ttc", "montant ttc", "net a payer ttc"],
    "piece": ["piece", "n piece", "numero piece"],
    "folio": ["folio"],
    # CHANGEMENT (décision métier confirmée, "Chantier 3 — DMI/RECTIF et
    # filtrage métier") : type d'écriture DS (RS/DMI/RECTIF...), exploité
    # par core/operation_types.py::is_rs_reconcilable pour écarter du
    # moteur RS les écritures hors périmètre AVANT generate_candidates —
    # voir core/matcher.py::_preparer_records_ds. Absence de mapping =
    # comportement inchangé (toute ligne reste RS-réconciliable).
    "type_operation": ["type", "type operation", "type d'operation", "nature", "categorie"],
}

RS_PATTERN_STR = r"\bRS\s*/"
MOTIF_OPERATION_RS = re.compile(RS_PATTERN_STR, re.IGNORECASE)


def _normaliser(texte: str) -> str:
    texte = str(texte).strip().lower()
    return unicodedata.normalize('NFD', texte).encode('ascii', 'ignore').decode('ascii')


def _score_nom(col_normalisee: str, synonymes: list) -> float:
    """
    Score 0-100 basé uniquement sur l'intitulé de colonne : correspondance
    exacte d'un synonyme comme sous-chaîne. Volontairement SANS repli flou
    (rapidfuzz) — sur des mots-clés courts ("ht", "rs"...), une similarité
    floue produit trop de faux positifs pour une simple suggestion
    indicative ; le contrôle humain (MappingDialog) reste de toute façon
    systématique.
    """
    meilleur = 0.0
    for syn in synonymes:
        kw = _normaliser(syn)
        if kw and kw in col_normalisee:
            score = 90 + (len(kw) / max(len(col_normalisee), 1)) * 10
            meilleur = max(meilleur, score)
    return min(meilleur, 100.0)


def _est_texte(v) -> bool:
    return not isinstance(v, (int, float)) and not hasattr(v, "strftime")


def _est_numerique(v) -> bool:
    if isinstance(v, (int, float)):
        return True
    if hasattr(v, "strftime"):
        return False
    try:
        float(str(v).replace('\xa0', '').replace(' ', '').replace(',', '.'))
        return True
    except ValueError:
        return False


def _est_date(v) -> bool:
    return hasattr(v, "strftime")


def _ratio(serie: pd.Series, predicat) -> float:
    non_nulles = serie.dropna()
    if len(non_nulles) == 0:
        return 0.0
    return sum(predicat(v) for v in non_nulles) / len(non_nulles)


def _ratio_motif_rs(serie: pd.Series) -> float:
    non_nulles = serie.dropna().astype(str)
    if len(non_nulles) == 0:
        return 0.0
    return sum(bool(MOTIF_OPERATION_RS.search(v)) for v in non_nulles) / len(non_nulles)


def _score_contenu_fournisseur(serie: pd.Series, fournisseur_resolue: Optional[pd.Series]) -> float:
    ratio_texte = _ratio(serie, _est_texte)
    ratio_rs = _ratio_motif_rs(serie)
    return min((ratio_texte * 0.6 + ratio_rs * 0.4) * 100, 100.0)


def _score_contenu_rs(serie: pd.Series, fournisseur_resolue: Optional[pd.Series]) -> float:
    score = _ratio(serie, _est_numerique) * 70
    if fournisseur_resolue is not None:
        masque_rs = fournisseur_resolue.astype(str).str.contains(RS_PATTERN_STR, case=False, regex=True, na=False)
        if masque_rs.any():
            score += serie[masque_rs].notna().mean() * 30
    return min(score, 100.0)


def _score_contenu_date(serie: pd.Series, fournisseur_resolue: Optional[pd.Series]) -> float:
    return _ratio(serie, _est_date) * 100


# Registre rôle -> fonction de score de contenu — EXTENSIBLE, limité aux 3
# rôles requis par le moteur (voir docstring du module). Un rôle absent de
# ce registre (rôles optionnels) est classé sur son seul nom de colonne.
SCORERS_CONTENU = {
    "fournisseur": _score_contenu_fournisseur,
    "rs": _score_contenu_rs,
    "date_paiement": _score_contenu_date,
}


# CHANGEMENT (câblage achat/vente) : dans le grand livre DS, le sens d'une
# écriture RS est donné par la colonne où elle apparaît — créditeur = achat
# (RS retenue par ABC, dette envers l'État), débiteur = vente (RS retenue par
# le client, créance). La colonne montant RS doit donc être "Crédit" pour une
# analyse des achats et "Débit" pour une analyse des ventes ; les lignes dont
# cette colonne est vide (écritures de l'autre sens) sont alors exclues par
# core/matcher.py::_preparer_records_ds (montant illisible), ce qui réalise
# le filtrage par sens.
MOT_COLONNE_SENS = {"achat": "credit", "vente": "debit"}


def colonne_contredit_sens(nom_colonne: str, sens: str) -> bool:
    """Vrai si la colonne montant RS choisie porte le nom du sens opposé (ex: "Débit" pour une analyse des achats)."""
    if not nom_colonne or sens not in MOT_COLONNE_SENS:
        return False
    oppose = MOT_COLONNE_SENS["vente" if sens == "achat" else "achat"]
    return oppose in _normaliser(nom_colonne)


def detect_ds_mapping(df: pd.DataFrame, roles: list = None, sens: str = None) -> dict:
    """
    Retourne {role: nom_de_colonne ou None} — une SUGGESTION indicative pour
    pré-remplir ui/mapping_dialog.py::MappingDialog, jamais un mapping
    appliqué automatiquement (voir docstring du module).

    roles=None -> les 3 rôles requis par le moteur (core/parser.py::ROLES_DS).
    fournisseur est résolu en premier : ses valeurs servent de signal de
    cohérence pour noter le rôle "rs" (le montant doit être renseigné sur
    les lignes où la colonne fournisseur retenue contient "RS/").
    """
    from core.parser import ROLES_DS

    roles = list(roles) if roles is not None else list(ROLES_DS)
    ordre_prioritaire = ["fournisseur", "rs", "date_paiement"]
    roles_a_traiter = [r for r in ordre_prioritaire if r in roles] + [r for r in roles if r not in ordre_prioritaire]

    colonnes_disponibles = list(df.columns)
    mapping = {}
    fournisseur_resolue = None

    for role in roles_a_traiter:
        synonymes = SYNONYMES_DS.get(role, [])
        scorer_contenu = SCORERS_CONTENU.get(role)

        meilleure_colonne, meilleur_score = None, 0.0
        for col in colonnes_disponibles:
            if role == "rs" and colonne_contredit_sens(col, sens):
                continue
            score_nom = _score_nom(_normaliser(col), synonymes)
            if role == "rs" and sens in MOT_COLONNE_SENS and MOT_COLONNE_SENS[sens] in _normaliser(col):
                score_nom = 100.0
            if scorer_contenu is not None:
                score = 0.5 * score_nom + 0.5 * scorer_contenu(df[col], fournisseur_resolue)
            else:
                score = score_nom
            if score > meilleur_score:
                meilleur_score, meilleure_colonne = score, col

        mapping[role] = meilleure_colonne
        if meilleure_colonne is not None:
            colonnes_disponibles.remove(meilleure_colonne)
            if role == "fournisseur":
                fournisseur_resolue = df[meilleure_colonne]

    return mapping
