"""
Analyse du dataset realiste : charge TEST_REALISTIC_TEJ.xlsx /
TEST_REALISTIC_DS.xlsx, execute le pipeline reel (candidate_generator ->
scorer -> reconciliation), croise avec GROUND_TRUTH_REALISTIC.xlsx (jamais
utilise par le moteur — uniquement pour evaluer apres coup), et produit
toutes les metriques demandees.
"""
import sys
sys.path.insert(0, r"c:\Users\user\Desktop\project")

import pandas as pd
from collections import defaultdict

from core.matcher import _preparer_records_ds, _preparer_records_tej
from core.reconciliation import _to_operations, reconcile_operations
from core.candidate_generator import generate_candidates
from core.scorer import score_candidate
from core.supplier_registry import SupplierRegistry
from core.rules import MARGE_AMBIGUITE

# ----------------------------------------------------------------------
# 1. Chargement + mapping manuel (colonnes connues, generees par
#    generate_realistic_dataset.py) — la colonne "ID" (verite terrain)
#    n'est PAS mappee : elle atterrit dans Operation.extra["ID"] sans
#    influencer le moteur.
# ----------------------------------------------------------------------
df_ds = pd.read_excel("TEST_REALISTIC_DS.xlsx")
df_tej = pd.read_excel("TEST_REALISTIC_TEJ.xlsx")
df_gt = pd.read_excel("GROUND_TRUTH_REALISTIC.xlsx")

map_ds = {"fournisseur": "Fournisseur", "rs": "Montant RS", "date_paiement": "Date"}
map_tej = {"fournisseur": "Fournisseur", "htva": "totalMontantHT", "rs": "totalMontantRS",
           "date_paiement": "Date de paiement", "statut": "État", "date_creation": None}

ds_records, nb_ds_illisible, nb_ds_non_identifiables = _preparer_records_ds(df_ds, map_ds)
tej_records, nb_tej_annulees, nb_tej_illisible = _preparer_records_tej(df_tej, map_tej)
assert nb_ds_illisible == 0 and nb_ds_non_identifiables == 0 and nb_tej_illisible == 0 and nb_tej_annulees == 0, "dataset genere corrompu"

accounting_ops = _to_operations(ds_records, source="DS")
fiscal_ops = _to_operations(tej_records, source="TEJ")

# CHANGEMENT (script d'analyse) : core/matcher.py::_preparer_records_* ne
# copie que les champs mappes (fournisseur/rs/date/...) dans les records,
# pas les colonnes non mappees comme "ID" — donc Operation.extra ne contient
# PAS l'ID de verite terrain. En revanche Operation.index == l'index de
# ligne du DataFrame d'origine (orig_idx) est toujours preserve : on s'en
# sert pour retrouver l'ID via le DataFrame source directement.
tej_id_par_index = df_tej["ID"].to_dict()
ds_id_par_index = df_ds["ID"].to_dict()
for op in fiscal_ops:
    op.extra["ID"] = tej_id_par_index[op.index]
for op in accounting_ops:
    op.extra["ID"] = ds_id_par_index[op.index]

# index rapide ID verite terrain -> Operation
tej_by_id = {op.extra["ID"]: op for op in fiscal_ops}
ds_by_id = {op.extra["ID"]: op for op in accounting_ops}

# ----------------------------------------------------------------------
# 2. Registry : enregistrer les alias connus AVANT le rapprochement (cas E2/E6).
#    Le cas E3 reste volontairement SANS alias.
# ----------------------------------------------------------------------
registry = SupplierRegistry(path="_realistic_registry.json")
t_alias, d_alias = tej_by_id["T057"], ds_by_id["D057"]
registry.add_alias("KARIM HADDAD CANONIQUE", t_alias.raw_name, persist=False)
registry.add_alias("KARIM HADDAD CANONIQUE", d_alias.raw_name, persist=False)
t_translit, d_translit = tej_by_id["T062"], ds_by_id["D062"]
registry.add_alias("KHALED BEN SALAH CANONIQUE", t_translit.raw_name, persist=False)
registry.add_alias("KHALED BEN SALAH CANONIQUE", d_translit.raw_name, persist=False)

# ----------------------------------------------------------------------
# 3. Candidats bruts (avant resolution globale) — pour les metriques de
#    candidature (recall, rang) demandees au point 3/4/6.
# ----------------------------------------------------------------------
candidates_map = generate_candidates(fiscal_ops, accounting_ops)
candidats_scores = {}  # fop -> [(aop, score), ...] tries desc
for fop, pool in candidates_map.items():
    scored = [(aop, score_candidate(fop, aop, registry)) for aop in pool]
    scored.sort(key=lambda t: t[1].global_score, reverse=True)
    candidats_scores[fop] = scored

# ----------------------------------------------------------------------
# 4. Resultat final du moteur complet (resolution globale + classification).
# ----------------------------------------------------------------------
results = reconcile_operations(fiscal_ops, accounting_ops, registry)
result_by_tej_id = {}
result_by_ds_id = {}
for r in results:
    if r.fiscal_op is not None:
        result_by_tej_id[r.fiscal_op.extra["ID"]] = r
    if r.accounting_op is not None:
        result_by_ds_id[r.accounting_op.extra["ID"]] = r
    for c in r.candidates:
        # les candidats "tenus" en REVIEW n'ont pas de r.accounting_op direct
        # mais doivent aussi etre reperables par DS_ID
        result_by_ds_id.setdefault(c.accounting_op.extra["ID"], r)

# ----------------------------------------------------------------------
# 5. Metriques de candidature : pour chaque TEJ ayant un vrai DS attendu.
# ----------------------------------------------------------------------
candidature_rows = []
gt_matches = df_gt[df_gt["DS_ID_ATTENDU"].notna() & df_gt["TEJ_ID"].notna()]

for _, row in gt_matches.iterrows():
    tid = row["TEJ_ID"]
    vrais_ds_ids = str(row["DS_ID_ATTENDU"]).split("|")  # AMBIGUOUS peut avoir plusieurs DS acceptables
    tej_op = tej_by_id[tid]
    scored = candidats_scores.get(tej_op, [])
    scored_ids = [aop.extra["ID"] for aop, _ in scored]

    present = any(vid in scored_ids for vid in vrais_ds_ids)
    rang = None
    score_vrai = None
    if present:
        for i, (aop, sc) in enumerate(scored):
            if aop.extra["ID"] in vrais_ds_ids:
                rang = i + 1
                score_vrai = sc.global_score
                break

    best_score = scored[0][1].global_score if scored else None
    final_statut = result_by_tej_id[tid].statut if tid in result_by_tej_id else "?"

    raison_absence = None
    if not present:
        # diagnostic de la raison (point 5)
        ds_candidats_meme_montant = [
            d for d in accounting_ops
            if d.amount == tej_op.amount and d.extra["ID"] in vrais_ds_ids
        ]
        vrai_ds_ops = [ds_by_id[vid] for vid in vrais_ds_ids if vid in ds_by_id]
        montant_ok = any(v.amount == tej_op.amount for v in vrai_ds_ops)
        if not montant_ok:
            raison_absence = "montant different (rejet HARD RULE attendu, pas un echec du generateur)"
        else:
            deltas = [abs((v.op_date - tej_op.op_date).days) for v in vrai_ds_ops if v.op_date and tej_op.op_date]
            if deltas and min(deltas) > 58:
                raison_absence = f"date hors fenetre ({min(deltas)} jours > 58)"
            else:
                raison_absence = "AUTRE / A INVESTIGUER (montant+date compatibles mais absent des candidats)"

    candidature_rows.append({
        "TEJ_ID": tid, "DS_ID_ATTENDU": row["DS_ID_ATTENDU"], "TYPE_GT": row["TYPE"],
        "NUMBER_OF_CANDIDATES": len(scored),
        "TRUE_COUNTERPART_IN_CANDIDATES": present,
        "CANDIDATE_RANK": rang,
        "BEST_SCORE": round(best_score, 1) if best_score is not None else None,
        "TRUE_COUNTERPART_SCORE": round(score_vrai, 1) if score_vrai is not None else None,
        "FINAL_STATUS": final_statut,
        "RAISON_ABSENCE": raison_absence,
    })

df_candidature = pd.DataFrame(candidature_rows)

n_total_avec_vrai_ds = len(df_candidature)
n_present = df_candidature["TRUE_COUNTERPART_IN_CANDIDATES"].sum()
candidate_recall = n_present / n_total_avec_vrai_ds * 100 if n_total_avec_vrai_ds else 0.0

# exclut les rejets legitimes (montant different) du calcul "objectif >= 95%"
# comme demande au point 5 (ce ne sont pas des echecs du candidate_generator)
df_hors_rejet_legitime = df_candidature[
    ~df_candidature["RAISON_ABSENCE"].fillna("").str.startswith("montant different")
]
n_total_pertinent = len(df_hors_rejet_legitime)
n_present_pertinent = df_hors_rejet_legitime["TRUE_COUNTERPART_IN_CANDIDATES"].sum()
candidate_recall_pertinent = n_present_pertinent / n_total_pertinent * 100 if n_total_pertinent else 0.0

# ranking (top-1/3/5) parmi les cas ou le vrai candidat EST present
df_present = df_candidature[df_candidature["TRUE_COUNTERPART_IN_CANDIDATES"]]
n_present_total = len(df_present)
top1 = (df_present["CANDIDATE_RANK"] <= 1).sum() / n_present_total * 100 if n_present_total else 0.0
top3 = (df_present["CANDIDATE_RANK"] <= 3).sum() / n_present_total * 100 if n_present_total else 0.0
top5 = (df_present["CANDIDATE_RANK"] <= 5).sum() / n_present_total * 100 if n_present_total else 0.0

# ----------------------------------------------------------------------
# 6. False positives : MATCH_CERTAIN/MATCH_PROBABLE dont le DS choisi
#    diverge de la verite terrain.
# ----------------------------------------------------------------------
false_positives = []
for _, row in gt_matches.iterrows():
    tid = row["TEJ_ID"]
    vrais_ds_ids = str(row["DS_ID_ATTENDU"]).split("|")
    r = result_by_tej_id.get(tid)
    if r is None or r.statut not in ("MATCH_CERTAIN", "MATCH_PROBABLE"):
        continue
    ds_choisi_id = r.accounting_op.extra["ID"] if r.accounting_op else None
    if ds_choisi_id not in vrais_ds_ids:
        false_positives.append({
            "TEJ_ID": tid, "DS_choisi": ds_choisi_id, "DS_attendu": row["DS_ID_ATTENDU"],
            "montant": tej_by_id[tid].amount,
            "date_tej": tej_by_id[tid].op_date, "date_ds_choisi": r.accounting_op.op_date if r.accounting_op else None,
            "supplier_score": round(r.scores.supplier_score * 100, 1) if r.scores else None,
            "total_score": r.scores.global_score if r.scores else None,
            "candidats_disponibles": len(candidats_scores.get(tej_by_id[tid], [])),
        })

# ----------------------------------------------------------------------
# 7. Analyse des REVIEW.
# ----------------------------------------------------------------------
review_rows = []
gt_by_tej = {row["TEJ_ID"]: row for _, row in df_gt.iterrows() if pd.notna(row["TEJ_ID"])}
for r in results:
    if r.statut != "REVIEW" or r.fiscal_op is None:
        continue
    tid = r.fiscal_op.extra["ID"]
    gt_row = gt_by_tej.get(tid)
    vrais_ds_ids = str(gt_row["DS_ID_ATTENDU"]).split("|") if gt_row is not None and pd.notna(gt_row["DS_ID_ATTENDU"]) else []
    scored = candidats_scores.get(r.fiscal_op, [])
    present = any(aop.extra["ID"] in vrais_ds_ids for aop, _ in scored)
    rang = None
    score_vrai = None
    for i, (aop, sc) in enumerate(scored):
        if aop.extra["ID"] in vrais_ds_ids:
            rang, score_vrai = i + 1, sc.global_score
            break
    best = scored[0][1].global_score if scored else None
    second = scored[1][1].global_score if len(scored) > 1 else None
    review_rows.append({
        "TEJ_ID": tid, "TYPE_GT": gt_row["TYPE"] if gt_row is not None else "?",
        "vrai_present": present, "rang_vrai": rang, "score_vrai": round(score_vrai, 1) if score_vrai else None,
        "best_score": round(best, 1) if best else None, "second_score": round(second, 1) if second else None,
        "marge": round(best - second, 1) if best and second else None,
        "n_candidats": len(scored),
        "raison": r.commentaire[:100],
    })
df_review = pd.DataFrame(review_rows)

# une REVIEW est "correcte" si soit (a) ambiguite reelle en verite terrain,
# soit (b) le vrai candidat n'a pas un score nettement dominant / n'est pas
# fiable (coherent avec la prudence voulue) ; "inutile" si le vrai candidat
# est present, seul, tres largement devant tout le reste ET aurait du etre
# un MATCH direct sans intervention humaine.
def review_est_correcte(row):
    if row["TYPE_GT"] in ("AMBIGUOUS", "REVIEW_NOM_DIFFERENT", "MATCH_CONTENTION_GAGNANT", "FISCAL_ONLY_CONTENTION_PERDANT"):
        return True
    if row["marge"] is not None and row["marge"] < MARGE_AMBIGUITE:
        return True
    return False

if not df_review.empty:
    df_review["review_correcte"] = df_review.apply(review_est_correcte, axis=1)
else:
    df_review["review_correcte"] = pd.Series(dtype=bool)

# ----------------------------------------------------------------------
# 8. Comptes finaux + export du rapport.
# ----------------------------------------------------------------------
compte_final = defaultdict(int)
for r in results:
    compte_final[r.statut] += 1

print("=" * 70)
print("RAPPORT — VALIDATION SUR DATASET REALISTE")
print("=" * 70)
print()
print("DATASET")
print(f"  TEJ total            : {len(fiscal_ops)}")
print(f"  DS total              : {len(accounting_ops)}")
print(f"  Verite terrain MATCH (dont contention/ambigus): {len(gt_matches)}")
print(f"  Verite terrain FISCAL_ONLY (purs + montant 1 millime): "
      f"{(df_gt['TYPE'].isin(['FISCAL_ONLY', 'FISCAL_ONLY_MONTANT_1_MILLIME', 'FISCAL_ONLY_CONTENTION_PERDANT'])).sum()}")
print(f"  Verite terrain COMPTA_ONLY (purs + montant 1 millime): "
      f"{(df_gt['TYPE'].isin(['COMPTA_ONLY', 'COMPTA_ONLY_MONTANT_1_MILLIME'])).sum()}")
print(f"  Verite terrain AMBIGUOUS : {(df_gt['TYPE'] == 'AMBIGUOUS').sum()}")
print()
print("CANDIDATE GENERATION")
print(f"  Candidate recall (brut)              : {n_present}/{n_total_avec_vrai_ds} = {candidate_recall:.1f}%")
print(f"  Candidate recall (hors rejets montant): {n_present_pertinent}/{n_total_pertinent} = {candidate_recall_pertinent:.1f}%")
print(f"  Top-1 : {top1:.1f}%  |  Top-3 : {top3:.1f}%  |  Top-5 : {top5:.1f}%  (parmi les {n_present_total} cas ou le vrai candidat est present)")
print()
print("FINAL MATCHING")
for statut in ("MATCH_CERTAIN", "MATCH_PROBABLE", "REVIEW", "FISCAL_ONLY", "COMPTA_ONLY"):
    print(f"  {statut:<15}: {compte_final.get(statut, 0)}")
print()
print("QUALITY")
print(f"  False positives (MATCH mais DS attendu different) : {len(false_positives)}")
print(f"  REVIEW totales   : {len(df_review)}")
print(f"  REVIEW correctes : {int(df_review['review_correcte'].sum()) if not df_review.empty else 0}")
print(f"  REVIEW inutiles  : {int((~df_review['review_correcte']).sum()) if not df_review.empty else 0}")
print()

if false_positives:
    print("-" * 70)
    print("FALSE POSITIVES DETAILLES")
    for fp in false_positives:
        print(fp)
else:
    print("Aucun false positive detecte.")

print()
print("-" * 70)
print("CAS DE CANDIDATURE AVEC PROBLEME (vrai candidat absent)")
absents = df_candidature[~df_candidature["TRUE_COUNTERPART_IN_CANDIDATES"]]
print(absents[["TEJ_ID", "DS_ID_ATTENDU", "TYPE_GT", "RAISON_ABSENCE"]].to_string(index=False))

print()
print("-" * 70)
print("REVIEW DETAILLEES")
if not df_review.empty:
    print(df_review.to_string(index=False))

# Export Excel du rapport complet
with pd.ExcelWriter("RAPPORT_VALIDATION_REALISTE.xlsx", engine="openpyxl") as writer:
    df_candidature.to_excel(writer, index=False, sheet_name="Candidature")
    df_review.to_excel(writer, index=False, sheet_name="Review")
    pd.DataFrame(false_positives).to_excel(writer, index=False, sheet_name="False positives")
    pd.DataFrame([
        {"Metrique": "TEJ total", "Valeur": len(fiscal_ops)},
        {"Metrique": "DS total", "Valeur": len(accounting_ops)},
        {"Metrique": "Candidate recall brut (%)", "Valeur": round(candidate_recall, 1)},
        {"Metrique": "Candidate recall hors rejets montant (%)", "Valeur": round(candidate_recall_pertinent, 1)},
        {"Metrique": "Top-1 (%)", "Valeur": round(top1, 1)},
        {"Metrique": "Top-3 (%)", "Valeur": round(top3, 1)},
        {"Metrique": "Top-5 (%)", "Valeur": round(top5, 1)},
        {"Metrique": "MATCH_CERTAIN", "Valeur": compte_final.get("MATCH_CERTAIN", 0)},
        {"Metrique": "MATCH_PROBABLE", "Valeur": compte_final.get("MATCH_PROBABLE", 0)},
        {"Metrique": "REVIEW", "Valeur": compte_final.get("REVIEW", 0)},
        {"Metrique": "FISCAL_ONLY", "Valeur": compte_final.get("FISCAL_ONLY", 0)},
        {"Metrique": "COMPTA_ONLY", "Valeur": compte_final.get("COMPTA_ONLY", 0)},
        {"Metrique": "False positives", "Valeur": len(false_positives)},
        {"Metrique": "REVIEW correctes", "Valeur": int(df_review["review_correcte"].sum()) if not df_review.empty else 0},
        {"Metrique": "REVIEW inutiles", "Valeur": int((~df_review["review_correcte"]).sum()) if not df_review.empty else 0},
    ]).to_excel(writer, index=False, sheet_name="Resume")

print()
print("Rapport exporte : RAPPORT_VALIDATION_REALISTE.xlsx")
