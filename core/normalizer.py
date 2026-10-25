import re
import unicodedata
import datetime
import pandas as pd
from dateutil import parser as date_parser

# Mots parasites courants à ignorer lors du matching des fournisseurs
STOP_WORDS = [
    r'\bSTE\b', r'\bSTÉ\b', r'\bSOCIETE\b', r'\bSOCIÉTÉ\b', 
    r'\bSARL\b', r'\bSUARL\b', r'\bSA\b', r'\bFRS\b', r'\bCLT\b', 
    r'\bRS\b', r'\bMR\b', r'\bMME\b', r'\bETAB\b'
]

def clean_text(text: str) -> str:
    """Nettoie le nom du fournisseur pour la comparaison (majuscules, sans accents, sans mots parasites)."""
    if not isinstance(text, str) or pd.isna(text):
        return ""

    # Pass en majuscules
    text = text.upper()

    # CHANGEMENT (correction bug) : retrait des dates au format JJ-MM-AAAA
    # ou JJ/MM/AAAA parfois accolées au libellé côté DS (ex: "RS/IMPRIMEX
    # 16-01-2026"). Sans ce retrait, la date survit sous forme de jetons
    # numériques isolés ("IMPRIMEX 16 01 2026") et fait chuter le score de
    # similarité de nom sous le seuil de 75% face au "IMPRIMEX" côté TEJ,
    # rejetant à tort une correspondance par ailleurs correcte (vérifié :
    # 62% au lieu de 100%). Motif volontairement restreint aux dates
    # complètes JJ-MM-AAAA pour ne pas retirer de vrais chiffres faisant
    # partie du nom (ex: "STE 4 SEASONS ADV").
    text = re.sub(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', ' ', text)

    # CHANGEMENT (correction bug) : retrait des accents limité aux marques
    # diacritiques combinantes (catégorie Unicode "Mn"), au lieu de
    # ré-encoder en ASCII strict (.encode('ascii', 'ignore')). Le latin
    # accentué se comporte à l'identique (Sté -> STE, la lettre de base
    # survit à la décomposition NFD). Mais l'écriture arabe n'a PAS cette
    # décomposition base+diacritique — l'ancien .encode('ascii','ignore')
    # supprimait donc INTÉGRALEMENT tout nom arabe, réduisant par exemple
    # "سلمى الطرابلسي" ET "كريم حداد" (deux fournisseurs différents) au même
    # clean_name vide (""). Cette collision faisait matcher à tort deux
    # fournisseurs arabes sans rapport (clean_name identique -> score 1.0)
    # et cassait aussi les alias arabes dans core/supplier_registry.py (clé
    # de résolution basée sur clean_text, donc elle aussi collisionnée sur
    # ""). Conserver les caractères arabes ne les traduit ni ne les
    # rapproche du latin par magie — seul le Supplier Registry (alias
    # validés) fait le lien entre écritures, volontairement (voir
    # Documentation : pas de correspondance phonétique inventée).
    text = ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    )

    # Retrait des caractères spéciaux : garde les lettres latines, les
    # chiffres, l'espace, ET les caractères arabes (bloc Unicode principal
    # ؀-ۿ + supplément ݐ-ݿ) — tout le reste (ponctuation,
    # slash, tirets...) devient un espace.
    text = re.sub(r'[^A-Z0-9؀-ۿݐ-ݿ\s]', ' ', text)

    # Retrait des mots parasites (Stop Words)
    for word in STOP_WORDS:
        text = re.sub(word, '', text)
        
    # Suppression des espaces multiples et des espaces de début/fin
    text = re.sub(r'\s+', ' ', text).strip()
    return text

PRECISION_MONTANT = 3  # dinar tunisien : précision au millime (0,001)


def clean_amount(val):
    r"""
    Convertit n'importe quel montant (string, int, float) en float strict,
    arrondi au millime (3 décimales, précision comptable réelle du dinar
    tunisien — voir utils/exporter.py, format '#,##0.000 "DT"').

    CHANGEMENT (correction bug) : retourne None quand le montant est
    manquant ou illisible, au lieu de 0.0. matcher.py teste explicitement
    `htva is None or rs is None` pour écarter une ligne du rapprochement
    (voir _preparer_records / montant_invalide) — tant que cette fonction
    retournait 0.0 dans ces cas, ce test ne se déclenchait jamais et deux
    montants illisibles pouvaient matcher entre eux via 0.0 == 0.0.

    CHANGEMENT (correction bug) : arrondi au millime ajouté. matcher.py
    compare les montants RS par égalité stricte (`==`) entre DS et TEJ ;
    deux montants comptablement identiques peuvent arriver ici comme deux
    floats très légèrement différents (ex: 99.99999999999999 au lieu de
    100.0) selon la façon dont Excel/pandas a stocké la valeur, ce qui
    aurait fait rater un rapprochement pourtant valide. Un montant
    comptable réel ne varie jamais de moins de 0,001 — arrondir au millime
    absorbe donc uniquement le bruit flottant, jamais un écart réel.

    CHANGEMENT (correction bug) : suppression de TOUTE espace (regex \s),
    pas seulement l'espace normal. Sur un export TEJ réel, les montants
    ≥ 1000 sont stockés en texte avec une espace INSÉCABLE ('\xa0', pas
    l'espace clavier normal '\x20') comme séparateur de milliers, ex.
    "1\xa0207.655". `.replace(' ', '')` ne supprimait que l'espace normal
    et laissait l'espace insécable intact, faisant échouer `float()` et
    classant à tort le montant comme illisible — constaté en pratique :
    la grande majorité des lignes TEJ ≥ 1000 DT d'un fichier réel étaient
    exclues silencieusement de l'analyse à cause de ce seul caractère.
    """
    if pd.isna(val):
        return None
    if isinstance(val, (int, float)):
        return round(float(val), PRECISION_MONTANT)

    # Si c'est une chaîne, retrait des symboles monétaires et de toute
    # forme d'espace (normale ou insécable) avant conversion.
    val_str = str(val).replace('DT', '').replace('TND', '').replace(',', '.')
    val_str = re.sub(r'\s+', '', val_str)
    try:
        return round(float(val_str), PRECISION_MONTANT)
    except ValueError:
        return None

def clean_date(val):
    """
    Uniformise la date au format AAAA-MM-JJ ou retourne None si invalide.

    CHANGEMENT (correction bug) : une valeur déjà lue par pandas/openpyxl
    comme une vraie date Excel (`datetime`/`pd.Timestamp`, cas normal pour
    une colonne de date native, ex. "Échéance" côté DS Comptable) est
    utilisée directement, SANS repasser par `date_parser.parse(str(val),
    dayfirst=True)`. Avant : `str(pd.Timestamp('2026-01-12'))` donne le
    texte ISO "2026-01-12 00:00:00", que `dayfirst=True` réinterprète à
    tort en inversant jour et mois dès que les deux sont ≤ 12 (12 janvier
    devenait silencieusement 1er décembre) — un objet date déjà sans
    ambiguïté n'a jamais besoin d'être re-parsé comme du texte. Le
    ré-parsing avec `dayfirst=True` reste utilisé uniquement pour les
    valeurs réellement textuelles (ex. dates TEJ au format "15/01/2026").
    """
    if pd.isna(val) or not val:
        return None
    if isinstance(val, datetime.datetime):
        return val.date()
    if isinstance(val, datetime.date):
        return val
    try:
        dt = date_parser.parse(str(val), dayfirst=True)
        return dt.date()
    except Exception:
        return None