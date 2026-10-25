"""
Génère un jeu de données réaliste pour valider le moteur de rapprochement :
TEST_REALISTIC_TEJ.xlsx, TEST_REALISTIC_DS.xlsx, GROUND_TRUTH_REALISTIC.xlsx.

L'identifiant de vérité terrain (colonne "ID") n'est JAMAIS utilisé par le
moteur de rapprochement : il n'appartient à aucun rôle mappé
(fournisseur/rs/date_paiement/htva/statut), il atterrit donc dans
Operation.extra sans influencer le candidate_generator/scorer/reconciliation
— il ne sert qu'à l'évaluation post-hoc (voir analyze_realistic_dataset.py).
"""
import random
from datetime import date, timedelta

import pandas as pd

random.seed(42)

tej_rows = []
ds_rows = []
ground_truth = []

t_counter = 0
d_counter = 0


def next_tej_id():
    global t_counter
    t_counter += 1
    return f"T{t_counter:03d}"


def next_ds_id():
    global d_counter
    d_counter += 1
    return f"D{d_counter:03d}"


def add_match(fournisseur_tej, fournisseur_ds, montant, date_tej, date_ds, statut="RECU", tva_rate=0.19):
    tid, did = next_tej_id(), next_ds_id()
    htva = round(montant / 0.01, 3) if montant > 0 else 0.0  # HT arbitraire cohérent avec un taux 1% pour rester simple
    tva = round(htva * tva_rate, 3)
    tej_rows.append({"ID": tid, "Fournisseur": fournisseur_tej, "totalMontantHT": htva, "totalMontantRS": montant,
                      "totalMontantTVA": tva, "Date de paiement": date_tej, "État": statut})
    ds_rows.append({"ID": did, "Fournisseur": fournisseur_ds, "Montant RS": montant, "Date": date_ds})
    ground_truth.append({"TEJ_ID": tid, "DS_ID_ATTENDU": did, "TYPE": "MATCH"})
    return tid, did


def add_fiscal_only(fournisseur, montant, d, statut="RECU"):
    tid = next_tej_id()
    htva = round(montant / 0.01, 3) if montant > 0 else 0.0
    tva = round(htva * 0.19, 3)
    tej_rows.append({"ID": tid, "Fournisseur": fournisseur, "totalMontantHT": htva, "totalMontantRS": montant,
                      "totalMontantTVA": tva, "Date de paiement": d, "État": statut})
    ground_truth.append({"TEJ_ID": tid, "DS_ID_ATTENDU": None, "TYPE": "FISCAL_ONLY"})
    return tid


def add_compta_only(fournisseur, montant, d):
    did = next_ds_id()
    ds_rows.append({"ID": did, "Fournisseur": fournisseur, "Montant RS": montant, "Date": d})
    ground_truth.append({"TEJ_ID": None, "DS_ID_ATTENDU": did, "TYPE": "COMPTA_ONLY"})
    return did


def add_ambiguous(fournisseur_tej, ds_variants, montant, date_tej):
    """ds_variants: liste de (fournisseur_ds, date_ds) -> plusieurs candidats DS pour un seul TEJ."""
    tid = next_tej_id()
    htva = round(montant / 0.01, 3)
    tva = round(htva * 0.19, 3)
    tej_rows.append({"ID": tid, "Fournisseur": fournisseur_tej, "totalMontantHT": htva, "totalMontantRS": montant,
                      "totalMontantTVA": tva, "Date de paiement": date_tej, "État": "RECU"})
    d_ids = []
    for f_ds, d_ds in ds_variants:
        did = next_ds_id()
        ds_rows.append({"ID": did, "Fournisseur": f_ds, "Montant RS": montant, "Date": d_ds})
        d_ids.append(did)
    ground_truth.append({"TEJ_ID": tid, "DS_ID_ATTENDU": "|".join(d_ids), "TYPE": "AMBIGUOUS"})
    return tid, d_ids


BASE = date(2026, 1, 5)

# --- A. Correspondances propres, meme date (15) ---
noms_a = ["IMPRIMEX", "VERRERIE AZUR", "ALU HORIZON", "COMPTOIR CENTRAL ALUM", "ESPACE CREATION",
          "FLEXIPACK MEDITERRANEE", "SOMIPEX", "ALUSTAR", "COMPTOIR DES METAUX", "OLIVIA DISTRIBUTION",
          "METAL PLUS", "PRIME ALUMINIUM", "LE COMPTOIR DE L ALUMIN", "EL AMEL", "FRERES MANSOUR"]
for i, nom in enumerate(noms_a):
    d = BASE + timedelta(days=i)
    add_match(nom, nom, round(10 + i * 7.531, 3), d, d)

# --- B. Correspondances, date decalee de quelques jours (15), les deux sens ---
noms_b = ["STE ZENITH", "COMPTOIR MODERNE", "ATELIER CENTRAL", "PLASTIC INDUSTRIE", "TEXTILE UNION",
          "IMPRIMERIE NATIONALE", "MENUISERIE DU NORD", "QUINCAILLERIE SUD", "ELECTRO SERVICE", "AUTO PIECES PLUS",
          "CHIMIE INDUSTRIELLE", "PAPETERIE CENTRALE", "TRANSPORT RAPIDE", "LOGISTIQUE EXPRESS", "BATIMENT PRO"]
for i, nom in enumerate(noms_b):
    d_tej = BASE + timedelta(days=20 + i)
    offset = (i % 7) - 3  # -3..+3, dans les deux sens
    d_ds = d_tej + timedelta(days=offset)
    add_match(nom, nom, round(50 + i * 3.217, 3), d_tej, d_ds)

# --- C. Correspondances, decalage de plusieurs semaines (10) ---
noms_c = ["SOCIETE ALPHA", "SOCIETE BETA", "SOCIETE GAMMA", "SOCIETE DELTA IND", "SOCIETE EPSILON",
          "SOCIETE ZETA", "SOCIETE ETA", "SOCIETE THETA", "SOCIETE IOTA", "SOCIETE KAPPA"]
for i, nom in enumerate(noms_c):
    d_tej = BASE + timedelta(days=40 + i * 2)
    offset = 15 + i * 3  # 15 a 42 jours, dans la fenetre de 58j
    sens = 1 if i % 2 == 0 else -1
    d_ds = d_tej + timedelta(days=sens * offset)
    add_match(nom, nom, round(100 + i * 11.111, 3), d_tej, d_ds)

# --- D. Variantes de nom (typos, prefixes, casse, ponctuation) (15) ---
variantes = [
    ("IMPRIMEX SERVICES", "RS/IMPRIMEX SERVICES"),
    ("STE NOVA DESIGN", "NOVA DESIGN"),
    ("comptoir central elec", "COMPTOIR CENTRAL ELEC"),
    ("STE  OFFICE   BUREAU", "OFFICE BUREAU"),
    ("SOCIETE GATEWAY CONSEIL", "RS/GATEWAY CONSEIL"),
    ("PHARMALIS", "PHARMALIS SARL"),
    ("GRAPHIC ATELIER", "STE GRAPHIC ATELIER"),
    ("MEDISUD EQUIPEMENT", "RS/MEDISUD EQUIPEMENT"),
    ("BIOLIXA LIFE SCIENCE", "BIOLYXA LIFE SCIENCE"),  # faute de frappe
    ("CHIMEDEX", "STE CHIMEDEX SARL"),
    ("COMPTOIR CENTRAL ALUM", "COMPTOIR CENTRAL ALUM"),  # faute de frappe
    ("NADER JLASSI MOTORS", "RS/NADER JLASSI MOTORS"),
    ("GENERALE DES EMBALLAGE", "STE GENERALE DES EMBALLAGE"),
    ("S-K-Y-E TRAVEL", "SKYE TRAVEL"),
    ("ORIGNIAL DRIVE CAR", "ORIGINAL DRIVE CAR"),  # faute de frappe
]
for i, (nom_tej, nom_ds) in enumerate(variantes):
    d = BASE + timedelta(days=60 + i)
    add_match(nom_tej, nom_ds, round(20 + i * 5.4, 3), d, d + timedelta(days=1))

# --- E. Cas arabes (10) ---
d = BASE + timedelta(days=80)
# E1: arabe <-> arabe identique
add_match("سلمى الطرابلسي", "سلمى الطرابلسي", 111.111, d, d)
# E2: arabe <-> latin AVEC alias (l'alias sera ajoute au registry par le script d'analyse)
d2 = d + timedelta(days=1)
tid_alias, did_alias = add_match("كريم حداد", "RS/LOYER KARIM HADDAD", 175.000, d2, d2)
# E3: arabe <-> latin SANS alias -> doit rester REVIEW
d3 = d + timedelta(days=2)
tid_noalias, did_noalias = add_match("فاطمة الزهراء", "RS/LOYER FATMA ZOHRA SANS ALIAS", 88.888, d3, d3)
# E4: deux fournisseurs arabes proches mais PAS equivalents (montants differents pour eviter tout candidat croise)
d4 = d + timedelta(days=3)
add_match("شركة النور للتجارة", "شركة النور للتجارة", 63.500, d4, d4)
d5 = d + timedelta(days=4)
add_match("شركة النور للصناعة", "شركة النور للصناعة", 74.200, d5, d5)
# E5: arabe + prefixe RS
d6 = d + timedelta(days=5)
add_match("RS/محمد الأمين", "محمد الأمين", 45.000, d6, d6)
# E6: translitteration differente (deux variantes latines du meme nom arabe, alias a fournir)
d7 = d + timedelta(days=6)
tid_translit, did_translit = add_match("KHALED BEN SALAH", "RS/LOYER KHALED BENSALAH", 199.999, d7, d7)

# --- F. Difference de montant d'un millime (5) : ne doivent PAS matcher entre eux ---
noms_f = ["STE MILLIME UN", "STE MILLIME DEUX", "STE MILLIME TROIS", "STE MILLIME QUATRE", "STE MILLIME CINQ"]
for i, nom in enumerate(noms_f):
    dte = BASE + timedelta(days=90 + i)
    montant_tej = round(200 + i * 10, 3)
    montant_ds = round(montant_tej + 0.001, 3)  # 1 millime d'ecart -> doit etre rejete
    tid = add_fiscal_only(nom, montant_tej, dte)
    did = add_compta_only(nom, montant_ds, dte)
    # ecrase les 2 dernieres lignes de verite terrain generiques par des entrees explicites de type REJECT
    ground_truth[-2] = {"TEJ_ID": tid, "DS_ID_ATTENDU": None, "TYPE": "FISCAL_ONLY_MONTANT_1_MILLIME"}
    ground_truth[-1] = {"TEJ_ID": None, "DS_ID_ATTENDU": did, "TYPE": "COMPTA_ONLY_MONTANT_1_MILLIME"}

# --- G. Meme montant, fournisseur different (5) -> REVIEW attendu ---
paires_g = [
    ("SOCIETE ROUGE", "SOCIETE BLEUE SANS RAPPORT"),
    ("ENTREPRISE VERTE", "ENTREPRISE JAUNE DIFFERENTE"),
    ("ATELIER NORD", "ATELIER SUD OPPOSE"),
    ("FOURNISSEUR PREMIUM XZ", "FOURNISSEUR ECONOMIQUE QY"),
    ("INDUSTRIE LOURDE KL", "SERVICE LEGER WM"),
]
for i, (nom_tej, nom_ds) in enumerate(paires_g):
    dte = BASE + timedelta(days=100 + i)
    montant = round(300 + i * 15.5, 3)
    tid, did = add_match(nom_tej, nom_ds, montant, dte, dte)
    ground_truth[-1] = {"TEJ_ID": tid, "DS_ID_ATTENDU": did, "TYPE": "REVIEW_NOM_DIFFERENT"}

# --- H. Meme fournisseur, montant different (5) -> deux operations distinctes ---
noms_h = ["STE MONTANT UN", "STE MONTANT DEUX", "STE MONTANT TROIS", "STE MONTANT QUATRE", "STE MONTANT CINQ"]
for i, nom in enumerate(noms_h):
    dte = BASE + timedelta(days=110 + i)
    add_fiscal_only(nom, round(400 + i * 20, 3), dte)
    add_compta_only(nom, round(500 + i * 20, 3), dte)  # montant totalement different, aucun candidat commun

# --- I. Duplicats / contention (scenarios A/B/C du point 9) ---
# I-A : T1->D1, T2->D2 independants (memes fournisseur/montant que I-C pour tester le non-melange)
dA = BASE + timedelta(days=120)
add_match("DUPLICAT INDEPENDANT", "DUPLICAT INDEPENDANT", 77.777, dA, dA)
add_match("DUPLICAT INDEPENDANT BIS", "DUPLICAT INDEPENDANT BIS", 77.777, dA + timedelta(days=25), dA + timedelta(days=25))

# I-B : deux TEJ concurrencent le meme DS (un seul DS disponible)
dB = BASE + timedelta(days=125)
tidB1 = next_tej_id()
tej_rows.append({"ID": tidB1, "Fournisseur": "CONTENTION UNIQUE", "totalMontantHT": 8000.0, "totalMontantRS": 88.000,
                  "totalMontantTVA": 1520.0, "Date de paiement": dB, "État": "RECU"})
tidB2 = next_tej_id()
tej_rows.append({"ID": tidB2, "Fournisseur": "CONTENTION UNIQUE", "totalMontantHT": 8000.0, "totalMontantRS": 88.000,
                  "totalMontantTVA": 1520.0, "Date de paiement": dB + timedelta(days=1), "État": "RECU"})
didB = next_ds_id()
ds_rows.append({"ID": didB, "Fournisseur": "CONTENTION UNIQUE", "Montant RS": 88.000, "Date": dB})
ground_truth.append({"TEJ_ID": tidB1, "DS_ID_ATTENDU": didB, "TYPE": "MATCH_CONTENTION_GAGNANT"})
ground_truth.append({"TEJ_ID": tidB2, "DS_ID_ATTENDU": None, "TYPE": "FISCAL_ONLY_CONTENTION_PERDANT"})

# I-C : 3 TEJ et 3 DS meme fournisseur/montant, dates tres espacees (doit se resoudre sans REVIEW)
dC = BASE + timedelta(days=130)
noms_c3 = "TRIPLICAT FOURNISSEUR"
ids_tej_c, ids_ds_c = [], []
for i in range(3):
    d_i = dC + timedelta(days=i * 20)  # tres espacees : 0, 20, 40 jours d'ecart mutuel
    tid, did = add_match(noms_c3, noms_c3, 66.660, d_i, d_i)
    ids_tej_c.append(tid)
    ids_ds_c.append(did)

# I-D : plusieurs operations identiques/quasi-identiques (ambiguite reelle, 2 candidats)
dD = BASE + timedelta(days=140)
add_ambiguous("SOCIETE TEST A", [("SOCIETE TEST A BIS", dD - timedelta(days=1)),
                                  ("SOCIETE TEST A TER", dD + timedelta(days=1))],
              200.000, dD)

# --- J. FISCAL_ONLY purs (10) ---
noms_j = ["SEUL TEJ UN", "SEUL TEJ DEUX", "SEUL TEJ TROIS", "SEUL TEJ QUATRE", "SEUL TEJ CINQ",
          "SEUL TEJ SIX", "SEUL TEJ SEPT", "SEUL TEJ HUIT", "SEUL TEJ NEUF", "SEUL TEJ DIX"]
for i, nom in enumerate(noms_j):
    d = BASE + timedelta(days=150 + i)
    add_fiscal_only(nom, round(600 + i * 13.7, 3), d)

# --- K. COMPTA_ONLY purs (10) ---
noms_k = ["SEUL DS UN", "SEUL DS DEUX", "SEUL DS TROIS", "SEUL DS QUATRE", "SEUL DS CINQ",
          "SEUL DS SIX", "SEUL DS SEPT", "SEUL DS HUIT", "SEUL DS NEUF", "SEUL DS DIX"]
for i, nom in enumerate(noms_k):
    d = BASE + timedelta(days=160 + i)
    add_compta_only(nom, round(700 + i * 9.3, 3), d)

# --- L. Ambiguite reelle supplementaire (dates tres proches, 3 cas) ---
for i in range(3):
    dL = BASE + timedelta(days=170 + i * 2)
    add_ambiguous(f"AMBIGU MULTI {i+1}", [(f"AMBIGU MULTI {i+1} VARIANTE A", dL - timedelta(days=1)),
                                            (f"AMBIGU MULTI {i+1} VARIANTE B", dL + timedelta(days=1))],
                  round(150 + i * 5, 3), dL)

# --- M. Montant zero (1 cas) ---
dM = BASE + timedelta(days=180)
add_match("STE MONTANT ZERO", "STE MONTANT ZERO", 0.000, dM, dM)

# --- N. Remplissage jusqu'a ~100/100 avec des correspondances propres supplementaires ---
i = 0
while len(tej_rows) < 100 or len(ds_rows) < 100:
    d = BASE + timedelta(days=190 + i)
    add_match(f"FOURNISSEUR REMPLISSAGE {i+1}", f"FOURNISSEUR REMPLISSAGE {i+1}", round(15 + i * 4.44, 3), d, d)
    i += 1

df_tej = pd.DataFrame(tej_rows)
df_ds = pd.DataFrame(ds_rows)
df_gt = pd.DataFrame(ground_truth)

df_tej.to_excel("TEST_REALISTIC_TEJ.xlsx", index=False)
df_ds.to_excel("TEST_REALISTIC_DS.xlsx", index=False)
df_gt.to_excel("GROUND_TRUTH_REALISTIC.xlsx", index=False)

print(f"TEJ: {len(df_tej)} lignes | DS: {len(df_ds)} lignes | Ground truth: {len(df_gt)} lignes")
print(f"Types de verite terrain: {df_gt['TYPE'].value_counts().to_dict()}")
print()
print("Alias a enregistrer manuellement dans le registry pour les cas 'avec alias':")
print(f"  E2 (alias connu)      : TEJ={tid_alias!r} DS={did_alias!r}")
print(f"  E3 (SANS alias)       : TEJ={tid_noalias!r} DS={did_noalias!r}")
print(f"  E6 (translitteration) : TEJ={tid_translit!r} DS={did_translit!r}")
