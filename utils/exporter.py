import pandas as pd
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# Styles communs (Palette Pastel Professionnelle)
FONT_HEADER = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
FILL_HEADER = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")       # Bleu foncé institutionnel

FONT_BODY = Font(name="Calibri", size=10)
ALIGN_LEFT = Alignment(horizontal="left", vertical="center")
ALIGN_CENTER = Alignment(horizontal="center", vertical="center")
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")

BORDER_THIN = Border(
    left=Side(style='thin', color='D3D3D3'),
    right=Side(style='thin', color='D3D3D3'),
    top=Side(style='thin', color='D3D3D3'),
    bottom=Side(style='thin', color='D3D3D3')
)

# ============================================================================
# Étape 10 — export du moteur de rapprochement bidirectionnel
# (core/reconciliation.py). CHANGEMENT (nettoyage) : l'ancien
# export_reconciliation_report (format de l'ancien moteur reconcile_data,
# statuts CONFORME/AMBIGU/ECART_ABSENT_*) a été retiré avec ce moteur.
# ============================================================================

FILL_MATCH_CERTAIN = PatternFill(start_color="D4EDDA", end_color="D4EDDA", fill_type="solid")   # Vert
FILL_MATCH_PROBABLE = PatternFill(start_color="D1E7F0", end_color="D1E7F0", fill_type="solid")  # Bleu clair
FILL_REVIEW = PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid")          # Orange/Jaune clair
FILL_ONLY = PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")            # Rouge clair
# CHANGEMENT (décision métier confirmée, "cas ANNULÉ/RECTIFIÉ") : distincte
# de FILL_ONLY — signale une anomalie identifiée, pas une simple absence.
FILL_CERTIFICAT_ANNULE = PatternFill(start_color="FFE0B2", end_color="FFE0B2", fill_type="solid")  # Orange vif
# CHANGEMENT (décision métier confirmée, "distinguer COMPTA_ONLY réellement
# inexpliqué de HORS_TAUX_SELECTIONNE") : gris neutre — hors du périmètre
# de l'analyse (autre taux), pas une anomalie comptable.
FILL_HORS_TAUX = PatternFill(start_color="E2E3E5", end_color="E2E3E5", fill_type="solid")
# CHANGEMENT (décision métier confirmée, "Chantier 3 — DMI/RECTIF et
# filtrage métier") : violet clair — exclusion métier volontaire (DMI/
# RECTIF côté DS), jamais une anomalie, distinct de toutes les autres
# catégories.
FILL_HORS_PERIMETRE_TYPE = PatternFill(start_color="D6D0E8", end_color="D6D0E8", fill_type="solid")

STATUT_LABELS_V2 = {
    "MATCH_CERTAIN": "✓ Correspondance certaine",
    "MATCH_PROBABLE": "✓ Correspondance probable",
    "REVIEW": "? À vérifier",
    "FISCAL_ONLY": "✗ Fiscal uniquement (absent de la comptabilité)",
    "COMPTA_ONLY": "✗ Comptabilité uniquement (absent de la plateforme fiscale)",
    "CERTIFICAT_TEJ_ANNULE": "⚠ Certificat TEJ trouvé mais ANNULÉ",
    "HORS_TAUX_SELECTIONNE": "↷ Hors du taux RS sélectionné",
    "HORS_PERIMETRE_TYPE": "○ Hors périmètre RS (exclusion métier volontaire)",
}


# CHANGEMENT (demande explicite) : "le rapport Excel doit contenir
# exactement le même tableau affiché au début [dans l'interface], et ajoute
# ce petit tableau [récapitulatif] à la fin". Colonnes identiques à
# ui/main_window.py::display_results, dans le même ordre, une seule feuille
# (au lieu des 5 feuilles séparées de la version précédente), avec le
# résumé par statut ajouté en bas de la même feuille plutôt qu'en feuille à part.
# CHANGEMENT (analyse multi-feuilles DS) : colonne "Feuille DS (taux)" —
# indique de quelle feuille/taux provient chaque ligne quand plusieurs
# feuilles DS sont fusionnées dans un même rapport.
COLONNES_RAPPORT = [
    "Statut", "Feuille DS (taux)", "Fournisseur TEJ (fiscal)", "Fournisseur DS (comptable)",
    "Date TEJ", "Date DS", "Montant RS", "Score", "Commentaire",
]


def _ligne_tableau_principal(r) -> dict:
    """Reproduit exactement une ligne du tableau affiché dans l'interface (voir ui/main_window.py::display_results)."""
    f, a = r.fiscal_op, r.accounting_op
    montant = f.amount if f else (a.amount if a else None)
    return {
        "Statut": STATUT_LABELS_V2.get(r.statut, r.statut),
        "Feuille DS (taux)": r.feuille or "-",
        "Fournisseur TEJ (fiscal)": (f.raw_name or "-") if f else "-",
        "Fournisseur DS (comptable)": (a.raw_name or "-") if a else "-",
        "Date TEJ": f.op_date if f else None,
        "Date DS": a.op_date if a else None,
        "Montant RS": montant,
        "Score": f"{r.scores.global_score:.1f}%" if r.scores else "-",
        "Commentaire": r.commentaire,
    }


def export_reconciliation_results(results: list, output_file_path: str):
    """
    Exporte le résultat du moteur de rapprochement (core/reconciliation.py,
    liste de ReconciliationResult) vers un classeur Excel à une seule
    feuille : exactement le tableau affiché dans l'interface (mêmes
    colonnes, même ordre, mêmes lignes), suivi du petit tableau
    récapitulatif (décompte par statut) en bas de la même feuille.
    """
    if not results:
        raise ValueError("Aucun résultat à exporter.")

    df = pd.DataFrame([_ligne_tableau_principal(r) for r in results])
    df = df.reindex(columns=COLONNES_RAPPORT)

    counts = {}
    for r in results:
        counts[r.statut] = counts.get(r.statut, 0) + 1
    # CHANGEMENT (rectification sémantique demandée, "Chantier 3 —
    # rectification finale") : ligne vide (None, None) avant "Hors
    # périmètre" pour la séparer visuellement des catégories de
    # rapprochement (MATCH_*/REVIEW/FISCAL_ONLY/COMPTA_ONLY) — c'est une
    # catégorie de PÉRIMÈTRE (exclusion métier volontaire), jamais une
    # anomalie, donc jamais mêlée aux autres lignes du récapitulatif.
    lignes_resume = [
        ("Correspondances certaines", counts.get("MATCH_CERTAIN", 0)),
        ("Correspondances probables", counts.get("MATCH_PROBABLE", 0)),
        ("À vérifier", counts.get("REVIEW", 0)),
        ("Fiscal uniquement", counts.get("FISCAL_ONLY", 0)),
        ("Comptabilité uniquement", counts.get("COMPTA_ONLY", 0)),
        ("Certificat TEJ trouvé mais annulé", counts.get("CERTIFICAT_TEJ_ANNULE", 0)),
        ("Hors du taux RS sélectionné", counts.get("HORS_TAUX_SELECTIONNE", 0)),
        (None, None),
        ("Hors périmètre RS (exclusion métier volontaire)", counts.get("HORS_PERIMETRE_TYPE", 0)),
        (None, None),
        ("TOTAL", len(results)),
    ]

    with pd.ExcelWriter(output_file_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Rapport de rapprochement")
        ws = writer.sheets["Rapport de rapprochement"]

        # En-tête du tableau principal
        for col_idx in range(1, len(COLONNES_RAPPORT) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = FONT_HEADER
            cell.fill = FILL_HEADER
            cell.alignment = ALIGN_CENTER
            cell.border = BORDER_THIN

        # Lignes du tableau principal, colorées par statut (même logique que
        # ui/main_window.py::display_results)
        for row_idx, r in enumerate(results, start=2):
            if r.statut == "MATCH_CERTAIN":
                fill = FILL_MATCH_CERTAIN
            elif r.statut == "MATCH_PROBABLE":
                fill = FILL_MATCH_PROBABLE
            elif r.statut == "REVIEW":
                fill = FILL_REVIEW
            elif r.statut == "CERTIFICAT_TEJ_ANNULE":
                fill = FILL_CERTIFICAT_ANNULE
            elif r.statut == "HORS_TAUX_SELECTIONNE":
                fill = FILL_HORS_TAUX
            elif r.statut == "HORS_PERIMETRE_TYPE":
                fill = FILL_HORS_PERIMETRE_TYPE
            else:
                fill = FILL_ONLY
            for col_idx, col_name in enumerate(COLONNES_RAPPORT, start=1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.font = FONT_BODY
                cell.fill = fill
                cell.border = BORDER_THIN
                if col_name in ("Date TEJ", "Date DS"):
                    cell.number_format = 'DD/MM/YYYY'
                    cell.alignment = ALIGN_CENTER
                elif col_name == "Montant RS":
                    cell.number_format = '#,##0.000 "DT"'
                    cell.alignment = ALIGN_RIGHT
                elif col_name in ("Statut", "Score", "Feuille DS (taux)"):
                    cell.alignment = ALIGN_CENTER
                else:
                    cell.alignment = ALIGN_LEFT

        # Petit tableau récapitulatif, ajouté à la fin (2 lignes vides après
        # le tableau principal)
        debut_resume = len(results) + 3
        cell = ws.cell(row=debut_resume, column=1, value="Catégorie")
        cell.font = FONT_HEADER
        cell.fill = FILL_HEADER
        cell.border = BORDER_THIN
        cell = ws.cell(row=debut_resume, column=2, value="Nombre")
        cell.font = FONT_HEADER
        cell.fill = FILL_HEADER
        cell.alignment = ALIGN_CENTER
        cell.border = BORDER_THIN

        for i, (label, count) in enumerate(lignes_resume, start=1):
            row_idx = debut_resume + i
            if label is None:
                continue  # ligne vide de séparation (None, None) -- volontairement sans bordure/contenu
            c1 = ws.cell(row=row_idx, column=1, value=label)
            c2 = ws.cell(row=row_idx, column=2, value=count)
            c1.font = FONT_BODY
            c2.font = FONT_BODY
            c1.border = BORDER_THIN
            c2.border = BORDER_THIN
            c2.alignment = ALIGN_CENTER

        for col in ws.columns:
            max_len = max((len(str(c.value)) for c in col if c.value is not None), default=10)
            ws.column_dimensions[get_column_letter(col[0].column)].width = max(max_len + 3, 12)