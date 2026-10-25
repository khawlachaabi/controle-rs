import unicodedata

import pandas as pd
from rapidfuzz import process, fuzz

# Ordre volontaire : les roles aux mots-cles les plus specifiques sont traites
# en premier (date_paiement avant date_creation) pour que le role le plus
# "generique" ne vole pas une colonne qui appartient clairement a un role
# plus precis. Voir _retirer_colonne_utilisee ci-dessous.
#
# CHANGEMENT (logique métier corrigée, 2e itération) : ajout du rôle
# "statut" (pour repérer et exclure les opérations "annulé" côté TEJ) et
# élargissement des mots-clés de date_creation pour couvrir "date de
# saisie/écriture/génération", qui n'apparaissaient pas avant.
#
# CHANGEMENT (décision métier confirmée sur données réelles) : "echeance"
# retiré des mots-clés de date_paiement, "date" (bare) ajouté à la place.
# Sur les écritures DS Comptable de type "OD" (opérations diverses) servant
# à enregistrer une RS, la colonne "Échéance" a été vérifiée sur un fichier
# réel comme étant systématiquement décorrélée de la vraie date TEJ (parfois
# plus d'un an d'écart) — elle ne représente pas la date de l'opération RS.
# La colonne "Date" (date de l'écriture comptable, 1ère colonne du grand
# livre) est la bonne référence pour le rapprochement avec la date de
# paiement TEJ. Avant ce changement, "Date" et "Échéance" scoraient à
# égalité (100) dans detect_columns() et le choix ne dépendait que de
# l'ordre des colonnes dans le fichier — un hasard, pas une décision.
KEYWORDS_MAP = {
    "fournisseur": ["nom", "tiers", "fournisseur", "client", "raison sociale", "frs", "clt", "nom/prenom", "beneficiaire"],
    "statut": ["statut", "status", "etat"],
    "date_paiement": ["date paie", "date reglement", "date paiement", "paie", "date"],
    "date_creation": ["date fac", "date creation", "date saisie", "date ecriture", "date generation", "date piece", "date devis", "date doc"],
    "htva": ["ht", "htva", "montant ht", "base impot", "montant hors taxe", "base"],
    "rs": ["rs", "retenue", "montant rs", "impot", "retenue a la source", "m-rs"],
}

SEUIL_MIN_ACCEPTABLE = 70


def _normaliser(texte: str) -> str:
    """
    Minuscule + suppression des accents.

    CHANGEMENT : appliqué à la fois aux en-têtes du fichier ET aux
    mots-clés de KEYWORDS_MAP, pour que la comparaison ne dépende plus de
    la présence exacte d'un accent des deux côtés (avant, "Date de Création"
    ne matchait que si le mot-clé était écrit avec le même accent).
    """
    texte = str(texte).strip().lower()
    texte = unicodedata.normalize('NFD', texte).encode('ascii', 'ignore').decode('ascii')
    return texte


def detect_columns(df: pd.DataFrame, roles: list = None) -> dict:
    """
    Analyse les colonnes d'un DataFrame et associe chaque rôle à la colonne
    la plus pertinente. Retourne {'fournisseur': 'Nom_Col_Excel', ...}.

    :param roles: sous-ensemble de rôles à détecter (clés de KEYWORDS_MAP).
        None = tous les rôles. CHANGEMENT (logique métier corrigée, 2e
        itération) : ajouté pour que ui/main_window.py puisse ne détecter
        QUE les rôles pertinents pour un fichier donné (DS Comptable n'a
        plus besoin de htva/date_creation, par exemple), au lieu d'exiger
        systématiquement les 5 rôles historiques pour tous les fichiers.

    Un rôle peut rester à None si aucune colonne ne dépasse le seuil de
    confiance minimal — c'est volontaire, voir load_and_map_excel().
    """
    roles_a_traiter = roles if roles is not None else list(KEYWORDS_MAP.keys())
    detected_mapping = {}
    colonnes_disponibles = {_normaliser(col): col for col in df.columns}

    for role in roles_a_traiter:
        mots_cles_bruts = KEYWORDS_MAP[role]
        mots_cles = [_normaliser(m) for m in mots_cles_bruts]
        best_match = None
        highest_score = 0

        # 1) Correspondance LITTÉRALE (mot-clé en sous-chaîne du nom de
        #    colonne) — passée en revue sur TOUTES les colonnes disponibles
        #    avant d'envisager un repli flou (voir point 2).
        for col_clean, col_original in colonnes_disponibles.items():
            for kw in mots_cles:
                if kw in col_clean:
                    score = 90 + (len(kw) / len(col_clean)) * 10
                    if score > highest_score:
                        highest_score = score
                        best_match = col_original

        # 2) Repli flou (rapidfuzz), seulement si AUCUNE colonne n'a atteint
        #    un score littéral suffisant. CHANGEMENT (correction de bug,
        #    "détection de colonnes TEJ — HTVA vs Montant") : ce repli était
        #    auparavant évalué À L'INTÉRIEUR de la boucle par colonne
        #    ci-dessus, une fois par colonne — un score flou précoce sur une
        #    colonne non pertinente (ex: "Montant" ~ "montant ht" à 100% via
        #    partial_ratio, "montant" étant un préfixe exact du mot-clé)
        #    pouvait alors l'emporter sur une correspondance LITTÉRALE
        #    exacte trouvée plus tard dans l'itération (ex: "HTVA" == "htva",
        #    également 100%), la comparaison stricte (`score > highest_score`)
        #    ne départageant jamais une égalité : la colonne trouvée EN
        #    PREMIER gagnait, par simple ordre des colonnes plutôt que par
        #    pertinence réelle. Déplacé ici : n'intervient plus qu'après
        #    épuisement des correspondances littérales sur l'ensemble des
        #    colonnes, comme déjà corrigé pour le mapping DS (voir
        #    core/ds_column_mapper.py, qui supprime même ce repli flou
        #    entièrement pour les mots-clés courts).
        if highest_score < 80 and mots_cles:
            for col_clean, col_original in colonnes_disponibles.items():
                match, score, _ = process.extractOne(col_clean, mots_cles, scorer=fuzz.partial_ratio)
                if score > highest_score and score >= 70:
                    highest_score = score
                    best_match = col_original

        # CHANGEMENT : rien n'est retenu si le score reste sous le seuil minimal
        # (avant, un score de 0 pouvait quand même laisser best_match=None sans
        # que ce soit distingué d'un score faible mais non nul et douteux).
        if highest_score < SEUIL_MIN_ACCEPTABLE:
            best_match = None

        detected_mapping[role] = best_match

        # CHANGEMENT : exclusivité des colonnes. Avant, rien n'empêchait deux
        # rôles différents de pointer vers la même colonne (ex : une seule
        # colonne "Date" générique était assignée à la fois à date_creation
        # ET date_paiement, ce qui aurait rendu le contrôle de délai de
        # paiement toujours égal à 0 jour, silencieusement).
        if best_match is not None:
            cle_utilisee = next(k for k, v in colonnes_disponibles.items() if v == best_match)
            del colonnes_disponibles[cle_utilisee]

    return detected_mapping


def load_excel(file_path: str) -> pd.DataFrame:
    """
    Charge le fichier Excel et nettoie les noms de colonnes, sans détection
    de mapping. Séparé de load_and_map_excel pour que l'appelant (ui/main_window.py)
    puisse relire le même DataFrame et proposer un mapping manuel via
    ui/mapping_dialog.py si detect_columns() échoue, sans avoir à recharger
    le fichier depuis le disque.
    """
    return load_excel_sheet(file_path, 0)


def list_sheets(file_path: str) -> list:
    """Noms des feuilles du classeur, dans l'ordre (voir analyse multi-feuilles DS et feuilles achats/ventes TEJ)."""
    with pd.ExcelFile(file_path) as xls:
        return list(xls.sheet_names)


def load_excel_sheet(file_path: str, sheet) -> pd.DataFrame:
    """Comme load_excel, pour une feuille précise (nom ou index)."""
    df = pd.read_excel(file_path, sheet_name=sheet)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def choisir_feuille_par_sens(sheet_names: list, sens: str):
    """
    TEJ sépare achats et ventes en deux feuilles distinctes (voir
    Documentation §1). Retourne le nom de la feuille dont l'intitulé
    correspond au sens choisi dans l'UI ("achat" ou "vente"), la seule
    feuille s'il n'y en a qu'une, ou None si le choix est ambigu (l'UI
    demande alors à l'utilisateur).
    """
    if len(sheet_names) == 1:
        return sheet_names[0]
    cle = "achat" if sens == "achat" else "vente"
    trouvees = [s for s in sheet_names if cle in _normaliser(s)]
    return trouvees[0] if len(trouvees) == 1 else None


def load_and_map_excel(file_path: str) -> tuple[pd.DataFrame, dict]:
    """Charge le fichier Excel et retourne le DataFrame ainsi que le mapping des colonnes."""
    df = load_excel(file_path)
    mapping = detect_columns(df)

    # CHANGEMENT : avant, un rôle non détecté restait à None sans avertissement,
    # et le reste du pipeline (matcher.py) l'utilisait silencieusement en
    # récupérant des valeurs manquantes via row.get(None). Maintenant l'échec
    # est explicite, avec la liste des colonnes disponibles pour diagnostiquer
    # — et c'est ce cas qui doit déclencher ui/mapping_dialog.py pour une
    # confirmation manuelle plutôt qu'un plantage plus loin dans le code.
    manquants = [role for role, col in mapping.items() if col is None]
    if manquants:
        raise ValueError(
            f"Colonnes non détectées automatiquement pour : {manquants}. "
            f"Colonnes disponibles dans le fichier : {list(df.columns)}. "
            f"Une confirmation manuelle est nécessaire."
        )

    return df, mapping


# CHANGEMENT (logique métier corrigée, 2e itération) : DS Comptable n'a
# plus besoin que de 3 rôles — le montant HTVA et la date de création n'y
# sont plus utilisés (voir core/matcher.py et core/rules.py).
ROLES_DS = ["fournisseur", "rs", "date_paiement"]

# CHANGEMENT (logique métier corrigée, 2e itération) : contrairement à DS
# Comptable (dont les intitulés de colonnes varient d'une société à
# l'autre), le fichier exporté par TEJ est un format standard, identique
# pour toutes les sociétés. On fixe donc ses noms de colonnes en dur au
# lieu de les redétecter par mots-clés à chaque chargement — plus fiable.
# Si un intitulé ne correspond pas exactement (le format TEJ a changé, ou
# diffère légèrement), on retombe automatiquement sur la détection par
# mots-clés pour CE rôle uniquement, pour ne pas bloquer l'utilisateur.
#
# CHANGEMENT (colonnes confirmées sur export réel) : "fournisseur" corrigé
# en "Raison social du bénéficiaire" (sans -e à "social" dans l'export réel
# — l'ancienne valeur "Raison sociale du bénéficiaire" ne correspondait
# jamais exactement et forçait un repli par mots-clés risqué : le fichier
# TEJ contient AUSSI "Raison social du déclarant" et surtout "Raison
# sociale de l'émetteur", qui matche mieux le mot-clé "raison sociale" —
# sans nom exact, la détection automatique pouvait donc silencieusement
# prendre le nom de l'émetteur/déclarant à la place de celui du
# fournisseur réel). "date_creation" ajoutée avec le nom confirmé "Date de
# création" (avant : aucun nom standard connu, rôle optionnel détecté à
# l'aveugle par mots-clés, contrôle de délai silencieusement inactif tant
# qu'il n'était pas trouvé).
COLONNES_TEJ_STANDARD = {
    "fournisseur": "Raison social du bénéficiaire",
    "htva": "totalMontantHT",
    "rs": "totalMontantRS",
    "date_paiement": "Date de paiement",
    "statut": "État",
    "date_creation": "Date de création",
}

# Rôle non obligatoire : son absence ne bloque pas le chargement du fichier
# (voir ui/main_window.py::roles_obligatoires, qui ne le liste pas), mais
# il a désormais un nom standard confirmé comme les autres rôles ci-dessus.
ROLES_TEJ_OPTIONNELS = ["date_creation"]


# CHANGEMENT (câblage achat/vente) : sur un certificat TEJ, le DÉCLARANT est
# celui qui retient l'impôt et le BÉNÉFICIAIRE celui qui est payé. Pour les
# achats, ABC est déclarant et la contrepartie (fournisseur) est le
# bénéficiaire ; pour les ventes c'est l'inverse — la contrepartie (client)
# est le déclarant. Comparer le bénéficiaire en mode ventes reviendrait à
# comparer chaque ligne DS au nom d'ABC elle-même.
COLONNE_CONTREPARTIE_TEJ = {
    "achat": "Raison social du bénéficiaire",
    "vente": "Raison social du déclarant",
}


def map_tej_columns(df: pd.DataFrame, sens: str = "achat") -> dict:
    """
    Détermine le mapping des colonnes pour un fichier TEJ : colonnes fixes
    en priorité (COLONNES_TEJ_STANDARD), repli sur la détection par
    mots-clés pour tout rôle dont le nom exact ne correspond pas au fichier
    chargé. `sens` ("achat"/"vente") choisit la colonne de la contrepartie
    (voir COLONNE_CONTREPARTIE_TEJ).
    """
    mapping = {}
    roles_a_detecter = []

    colonnes_standard = dict(COLONNES_TEJ_STANDARD)
    colonnes_standard["fournisseur"] = COLONNE_CONTREPARTIE_TEJ.get(sens, colonnes_standard["fournisseur"])

    for role, nom_colonne in colonnes_standard.items():
        if nom_colonne in df.columns:
            mapping[role] = nom_colonne
        else:
            roles_a_detecter.append(role)

    if roles_a_detecter:
        # CHANGEMENT (correction bug) : la détection de repli est restreinte
        # aux colonnes pas déjà attribuées par un nom exact ci-dessus.
        # Avant, seule la détection de date_creation excluait ces colonnes ;
        # un rôle parmi les 5 historiques nécessitant un repli pouvait donc
        # reprendre une colonne déjà assignée en dur à un autre rôle (ex :
        # matcher un mot-clé sur "État", déjà pris par "statut").
        colonnes_libres = [c for c in df.columns if c not in mapping.values()]
        df_restant = df[colonnes_libres] if colonnes_libres else df.iloc[:, 0:0]
        mapping.update(detect_columns(df_restant, roles=roles_a_detecter))

    return mapping