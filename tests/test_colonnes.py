"""Détection des colonnes et choix des feuilles (achats/ventes)."""
from datetime import date

from core.ds_column_mapper import detect_ds_mapping, colonne_contredit_sens
from core.parser import map_tej_columns, choisir_feuille_par_sens

from conftest import tej_df, ds_df


def test_tej_contrepartie_selon_le_sens():
    df = tej_df([("IMPRIMEX", 1000, 10, "15/01/2026", "REÇUE", None)])
    assert map_tej_columns(df, "achat")["fournisseur"] == "Raison social du bénéficiaire"
    assert map_tej_columns(df, "vente")["fournisseur"] == "Raison social du déclarant"
    mapping = map_tej_columns(df)
    assert mapping["htva"] == "totalMontantHT" and mapping["rs"] == "totalMontantRS"
    assert mapping["statut"] == "État" and mapping["date_creation"] == "Date de création"


def test_choix_feuille_tej_par_sens():
    assert choisir_feuille_par_sens(["Achats", "Ventes"], "achat") == "Achats"
    assert choisir_feuille_par_sens(["Achats", "Ventes"], "vente") == "Ventes"
    assert choisir_feuille_par_sens(["Feuil1"], "vente") == "Feuil1"
    assert choisir_feuille_par_sens(["A", "B"], "achat") is None  # ambigu : l'UI demande


def test_ds_colonne_montant_selon_le_sens():
    df = ds_df([("RS/IMPRIMEX", 10.0, date(2026, 1, 12))])
    assert detect_ds_mapping(df, sens="achat")["rs"] == "Crédit"
    assert detect_ds_mapping(df, sens="vente")["rs"] == "Débit"
    assert detect_ds_mapping(df, sens="achat")["date_paiement"] == "Date"


def test_colonne_contredit_sens():
    assert colonne_contredit_sens("Débit", "achat")
    assert colonne_contredit_sens("Montant crédit", "vente")
    assert not colonne_contredit_sens("Crédit", "achat")
    assert not colonne_contredit_sens("Montant RS", "achat")
