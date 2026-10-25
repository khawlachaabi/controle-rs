import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.supplier_registry import SupplierRegistry  # noqa: E402


@pytest.fixture
def registry(tmp_path):
    """Référentiel vide dans un dossier temporaire — ne touche jamais au supplier_registry.json du projet."""
    return SupplierRegistry(path=str(tmp_path / "registry.json"))


def tej_df(lignes):
    """
    DataFrame TEJ au format standard de la plateforme.
    lignes : [(beneficiaire, htva, rs, date_paiement "JJ/MM/AAAA", etat, date_creation ou None), ...]
    """
    return pd.DataFrame({
        "Raison social du déclarant": ["ABC AUDIT"] * len(lignes),
        "Raison social du bénéficiaire": [l[0] for l in lignes],
        "totalMontantHT": [l[1] for l in lignes],
        "totalMontantRS": [l[2] for l in lignes],
        "Date de paiement": [l[3] for l in lignes],
        "État": [l[4] for l in lignes],
        "Date de création": [l[5] for l in lignes],
    })


def ds_df(lignes):
    """DataFrame DS (grand livre). lignes : [(libelle, credit, date), ...]"""
    return pd.DataFrame({
        "Date": [l[2] for l in lignes],
        "Libellé": [l[0] for l in lignes],
        "Débit": [None] * len(lignes),
        "Crédit": [l[1] for l in lignes],
    })


MAP_DS = {"fournisseur": "Libellé", "rs": "Crédit", "date_paiement": "Date"}
