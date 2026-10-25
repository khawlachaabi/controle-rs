"""Règles de saisie et de nettoyage : taux, montants, dates, noms."""
from datetime import date

import pandas as pd
import pytest

from core.normalizer import clean_amount, clean_date, clean_text
from core.rules import parser_taux_saisi, check_taux_conforme, check_payment_delay


@pytest.mark.parametrize("saisie, attendu", [
    ("1", 0.01),
    ("1%", 0.01),
    ("7,5", 0.075),
    ("7.5 %", 0.075),
    ("0.5", 0.005),
    ("100", 1.0),
])
def test_taux_saisi_toujours_en_pourcentage(saisie, attendu):
    assert parser_taux_saisi(saisie) == pytest.approx(attendu)


@pytest.mark.parametrize("saisie", ["0", "-5", "abc", "", "150"])
def test_taux_saisi_invalide(saisie):
    with pytest.raises(ValueError):
        parser_taux_saisi(saisie)


def test_taux_conforme_a_la_tolerance():
    assert check_taux_conforme(1000, 10, 0.01) == (True, 0.01)
    assert check_taux_conforme(1000, 100, 0.01)[0] is False
    assert check_taux_conforme(None, 10, 0.01) == (None, None)  # HTVA absent : non vérifiable, pas un rejet


def test_delai_paiement():
    assert check_payment_delay(date(2026, 1, 1), date(2026, 2, 1)) == (True, 31)
    assert check_payment_delay(date(2026, 1, 1), date(2026, 2, 2)) == (False, 32)
    assert check_payment_delay(None, date(2026, 2, 2)) == (None, None)


def test_montant_espace_insecable_et_arrondi():
    assert clean_amount("1\xa0207.655") == 1207.655
    assert clean_amount("12,5 DT") == 12.5
    assert clean_amount(99.99999999999999) == 100.0
    assert clean_amount(None) is None
    assert clean_amount("illisible") is None


def test_date_excel_non_inversee():
    # Une vraie date Excel ne doit jamais être re-parsée en "dayfirst" (12 janvier ≠ 1er décembre)
    assert clean_date(pd.Timestamp("2026-01-12")) == date(2026, 1, 12)
    assert clean_date("15/01/2026") == date(2026, 1, 15)
    assert clean_date(None) is None


def test_nom_sans_date_ni_mots_parasites():
    assert clean_text("RS/IMPRIMEX 16-01-2026") == "IMPRIMEX"
    assert clean_text("STE 4 SEASONS ADV") == "4 SEASONS ADV"


def test_noms_arabes_conserves_et_distincts():
    a, b = clean_text("سلمى الطرابلسي"), clean_text("كريم حداد")
    assert a and b and a != b
