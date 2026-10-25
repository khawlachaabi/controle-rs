"""
Préparation des lignes DS Comptable et TEJ pour le moteur de rapprochement
(core/reconciliation.py) : lecture des colonnes mappées, nettoyage des noms,
montants et dates, et exclusion des lignes inexploitables (montant RS
illisible, ligne de total sans fournisseur ni date, certificat TEJ annulé).

CHANGEMENT (nettoyage) : l'ancien moteur séquentiel reconcile_data() et ses
helpers (seuil de nom 75 %, fenêtre "fin du mois suivant", statuts
CONFORME/AMBIGU/ECART_ABSENT_*) ont été retirés — ils n'étaient plus appelés
depuis le passage au moteur bidirectionnel et décrivaient des règles
contredites par celui-ci. Seules les fonctions de préparation, réutilisées
par core/reconciliation.py, restent ici.
"""

import pandas as pd

from core.normalizer import clean_text, clean_amount, clean_date
from core.operation_types import is_cancelled


def _est_annule(statut_brut) -> bool:
    """
    Détecte un statut "annulé" (accents/casse variables : "Annulée",
    "ANNULE", etc.). CHANGEMENT (décision métier confirmée, "Chantier 3 —
    DMI/RECTIF et filtrage métier") : délègue désormais à
    core/operation_types.py::is_cancelled (même règle exacte, "ANNUL" en
    sous-chaîne après clean_text — comportement strictement inchangé,
    simple centralisation pour éviter deux implémentations de la même
    détection dans le projet).
    """
    return is_cancelled(statut_brut)


def _extraire_fournisseur(row, col_fournisseur) -> tuple:
    """
    CHANGEMENT (décision métier confirmée, "rien n'est obligatoire à part
    les champs qui contribuent aux calculs") : extraction sûre du nom
    fournisseur quand la colonne n'est pas mappée (col_fournisseur=None) ou
    que la cellule est vide (NaN). Avant, `clean_text(str(row.get(None)))`
    donnait `clean_text("None")` = "NONE" (le mot littéral), et une cellule
    vide donnait `clean_text("nan")` = "NAN" — deux fournisseurs sans nom
    disponible se retrouvaient donc avec le MÊME clean_name non vide, ce qui
    aurait produit un faux score fournisseur de 1.0 ("nom identique") entre
    deux opérations sans rapport (même bug que la collision arabe corrigée
    dans core/normalizer.py). Retourne clean_name="" explicitement dans ce
    cas — core/scorer.py::supplier_score() traite un nom vide comme "non
    comparable" (score 0), jamais comme "identique".
    """
    if col_fournisseur is None:
        return None, ""
    valeur = row.get(col_fournisseur)
    if valeur is None or (isinstance(valeur, float) and pd.isna(valeur)):
        return None, ""
    return valeur, clean_text(str(valeur))


def _preparer_records_ds(df: pd.DataFrame, mapping: dict) -> tuple:
    """
    DS Comptable : seul le montant RS est indispensable (hard rule qui fonde
    toute la génération de candidats, voir core/candidate_generator.py).
    Fournisseur et date restent utiles au scoring mais leur absence est
    tolérée (voir _extraire_fournisseur ci-dessus ; une date manquante
    donne simplement aucun candidat pour cette ligne, voir
    core/candidate_generator.py::generate_candidates).

    CHANGEMENT (logique métier corrigée, 4e itération) : une ligne au
    montant RS illisible est désormais complètement écartée ici (comptée,
    pas ajoutée à la liste de records) plutôt que gardée avec un flag
    montant_invalide traité plus loin — cohérent avec le fait qu'elle ne
    doit plus apparaître dans le rapport final du tout.

    CORRECTION (recette de production sur fichier réel, "DS client_test.xlsx") :
    une ligne SANS fournisseur ET SANS date exploitables est également
    écartée (comptée séparément, jamais ajoutée à records). Défaut démontré
    sur un export réel de grand livre comptable : la ligne de pied de page
    "Total Mouvements du compte : ..." (Débit = Crédit = total général, ni
    fournisseur ni date valides) passait jusqu'ici le seul garde-fou
    existant (montant RS lisible) et se retrouvait ajoutée comme une
    opération DS réelle — produisant un faux COMPTA_ONLY fabriqué dans le
    rapport final (montant du total général, fournisseur et date absents),
    ce qu'aucune vraie opération comptable ne peut légitimement être. Une
    ligne avec fournisseur OU date renseigné(e) reste toujours conservée
    (n'exclut jamais une opération réelle mais incomplète) — seule
    l'absence SIMULTANÉE des deux déclenche l'exclusion, une signature
    propre aux lignes de total/sous-total, jamais à une écriture RS réelle.
    """
    records = []
    nb_invalides = 0
    nb_non_identifiables = 0
    col_fournisseur = mapping.get("fournisseur")
    col_date = mapping.get("date_paiement")
    # CHANGEMENT (décision métier confirmée, "gestion de plusieurs taux RS
    # dans une même analyse") : HTVA reste optionnel côté DS (rôle proposé
    # dans MappingDialog mais jusqu'ici jamais lu ici) — capturé UNIQUEMENT
    # à titre informatif pour core/scorer.py (taux RS = rs/htva calculé côté
    # DS quand disponible, exposé sans jamais filtrer ni pondérer aucun
    # candidat). Absence de mapping = comportement inchangé (htva=None).
    col_htva = mapping.get("htva")
    # CHANGEMENT (décision métier confirmée, "Chantier 3 — DMI/RECTIF et
    # filtrage métier") : type d'opération DS optionnel (RS/DMI/RECTIF, voir
    # core/ds_column_mapper.py et core/operation_types.py) — capturé
    # UNIQUEMENT pour que core/reconciliation.py puisse écarter du moteur RS
    # les opérations hors périmètre (DMI, RECTIF côté DS...) avant même la
    # génération de candidats. Absence de mapping = comportement inchangé
    # (type_operation=None => is_rs_reconcilable(None) = True, aucune ligne
    # n'est filtrée).
    col_type = mapping.get("type_operation")

    for idx, row in df.iterrows():
        rs = clean_amount(row.get(mapping["rs"]))
        if rs is None:
            nb_invalides += 1
            continue
        raw_name, clean_name = _extraire_fournisseur(row, col_fournisseur)
        date_paiement = clean_date(row.get(col_date)) if col_date else None
        if not clean_name and date_paiement is None:
            nb_non_identifiables += 1
            continue
        records.append({
            "orig_idx": idx,
            "raw_name": raw_name,
            "clean_name": clean_name,
            "rs": rs,
            "htva": clean_amount(row.get(col_htva)) if col_htva else None,
            "date_paiement": date_paiement,
            "type_operation": row.get(col_type) if col_type else None,
            "matched": False,
        })
    return records, nb_invalides, nb_non_identifiables


def _preparer_records_tej(df: pd.DataFrame, mapping: dict) -> tuple:
    """
    Côté TEJ, seul le montant RS est indispensable (comme côté DS).
    Fournisseur, HTVA, date de paiement, statut et date de création restent
    utiles mais optionnels — voir core/reconciliation.py pour comment
    l'absence de HTVA est traitée au niveau du filtre de taux (taux "non
    vérifiable" plutôt qu'exclusion).

    CHANGEMENT (décision métier confirmée, "rien n'est obligatoire à part
    les champs qui contribuent aux calculs") : HTVA manquant (colonne non
    mappée) n'exclut plus la ligne. Avant, `htva is None` (vrai pour TOUTES
    les lignes si la colonne HTVA n'était pas mappée) faisait rejeter
    l'intégralité du fichier TEJ comme "montant illisible" — un champ
    optionnel manquant ne doit jamais avoir cet effet, seul le montant RS
    (hard rule) doit exclure une ligne.

    CHANGEMENT (logique métier corrigée, 4e itération) : les lignes au
    statut "annulé" sont écartées ici (comptées, pas gardées avec un flag).
    """
    records = []
    nb_annulees = 0
    nb_invalides = 0
    col_date_creation = mapping.get("date_creation")
    col_htva = mapping.get("htva")
    col_fournisseur = mapping.get("fournisseur")
    col_date = mapping.get("date_paiement")
    col_statut = mapping.get("statut")

    for idx, row in df.iterrows():
        if _est_annule(row.get(col_statut)):
            nb_annulees += 1
            continue

        rs = clean_amount(row.get(mapping["rs"]))
        if rs is None:
            nb_invalides += 1
            continue

        htva = clean_amount(row.get(col_htva)) if col_htva else None
        raw_name, clean_name = _extraire_fournisseur(row, col_fournisseur)

        records.append({
            "orig_idx": idx,
            "raw_name": raw_name,
            "clean_name": clean_name,
            "htva": htva,
            "rs": rs,
            "date_paiement": clean_date(row.get(col_date)) if col_date else None,
            "date_creation": clean_date(row.get(col_date_creation)) if col_date_creation else None,
            # CHANGEMENT (décision métier confirmée, "cas ANNULÉ/RECTIFIÉ") :
            # statut brut conservé pour traçabilité (ex: un certificat
            # RECTIFIÉ reste visible dans Operation.extra["statut"], voir
            # core/reconciliation.py::_to_operations) — n'influence AUCUNE
            # décision de matching, seul _est_annule() ci-dessus exclut une
            # ligne, sur le mot "ANNUL" uniquement.
            "statut": row.get(col_statut),
            "matched": False,
        })
    return records, nb_annulees, nb_invalides


def _extraire_certificats_annules(df: pd.DataFrame, mapping: dict) -> list:
    """
    Certificats TEJ au statut ANNULÉ, normalement exclus du rapprochement
    normal par _preparer_records_tej (voir _est_annule) — capturés ICI
    séparément, à titre d'information, pour que core/reconciliation.py
    puisse signaler explicitement qu'un certificat correspondant existe
    côté TEJ mais est annulé (voir statut CERTIFICAT_TEJ_ANNULE), plutôt
    que de fondre ce cas dans un COMPTA_ONLY générique indiscernable d'une
    opération réellement absente côté TEJ.

    Fonction séparée de _preparer_records_tej (plutôt que d'en changer la
    signature) : ne modifie donc AUCUN appelant existant. Cette liste ne
    participe JAMAIS au matching normal (jamais passée à
    generate_candidates aux côtés des opérations TEJ actives) — utilisée
    uniquement, après coup, pour enrichir un COMPTA_ONLY déjà déterminé.
    """
    records = []
    col_fournisseur = mapping.get("fournisseur")
    col_date = mapping.get("date_paiement")
    col_statut = mapping.get("statut")
    col_htva = mapping.get("htva")

    for idx, row in df.iterrows():
        statut_brut = row.get(col_statut)
        if not _est_annule(statut_brut):
            continue
        rs = clean_amount(row.get(mapping["rs"]))
        if rs is None:
            continue
        raw_name, clean_name = _extraire_fournisseur(row, col_fournisseur)
        records.append({
            "orig_idx": idx,
            "raw_name": raw_name,
            "clean_name": clean_name,
            "rs": rs,
            "htva": clean_amount(row.get(col_htva)) if col_htva else None,
            "date_paiement": clean_date(row.get(col_date)) if col_date else None,
            "statut": statut_brut,
        })
    return records
