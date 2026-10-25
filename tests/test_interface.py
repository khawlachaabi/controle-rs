"""Interface (Qt hors écran) : analyse en arrière-plan, affichage, export."""
import os
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from core.parser import map_tej_columns
from utils.exporter import export_reconciliation_results, COLONNES_RAPPORT

from conftest import tej_df, ds_df, MAP_DS


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_worker_puis_affichage(app, registry, monkeypatch, tmp_path):
    from ui.main_window import AnalyseWorker, MainWindow

    tej = tej_df([("IMPRIMEX", 1000, 10.0, "15/01/2026", "REÇUE", None),
                  ("INCONNU", 500, 5.0, "16/01/2026", "REÇUE", None)])
    taches = [("RS 1%", 0.01, ds_df([("RS/IMPRIMEX", 10.0, date(2026, 1, 15)),
                                      ("RS/SANS NOM", 7.0, date(2026, 1, 16))]), MAP_DS)]
    worker = AnalyseWorker(taches, tej, map_tej_columns(tej, "achat"), registry)
    recu, erreurs = [], []
    worker.termine.connect(lambda res, st, n: recu.append((res, st, n)))
    worker.echec.connect(erreurs.append)
    worker.run()  # exécution synchrone : même code que dans le fil de travail
    assert not erreurs
    results, stats, nb = recu[0]
    assert nb == 1 and {r.feuille for r in results} == {"RS 1% (1.00%)"}

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    fenetre = MainWindow()
    fenetre.registry = registry
    fenetre.progression = type("P", (), {"close": lambda self: None})()
    fenetre._on_analyse_terminee(results, stats, nb)
    assert fenetre.table_widget.rowCount() == len(results)
    assert fenetre.table_widget.columnCount() == len(COLONNES_RAPPORT)
    assert fenetre.btn_export.isEnabled()

    sortie = tmp_path / "rapport.xlsx"
    export_reconciliation_results(results, str(sortie))
    assert sortie.stat().st_size > 0


def test_worker_signale_les_erreurs(app, registry):
    from ui.main_window import AnalyseWorker

    tej = tej_df([("IMPRIMEX", 1000, 10.0, "15/01/2026", "REÇUE", None)])
    taches = [("Vide", 0.01, ds_df([("RS/IMPRIMEX", 10.0, None)]), MAP_DS)]  # aucune date DS
    worker = AnalyseWorker(taches, tej, map_tej_columns(tej, "achat"), registry)
    erreurs = []
    worker.echec.connect(erreurs.append)
    worker.run()
    assert erreurs and "Feuille DS « Vide »" in erreurs[0]


def test_dialogue_feuilles(app):
    from ui.sheet_taux_dialog import SheetTauxDialog, deviner_taux_feuille

    assert deviner_taux_feuille("Loyers 10%") == pytest.approx(0.10)
    assert deviner_taux_feuille("RS 1,5 %") == pytest.approx(0.015)
    assert deviner_taux_feuille("Feuil1") is None
    dlg = SheetTauxDialog(["RS 1%", "Loyers 10%", "Feuil3"], 0.03)
    dlg._on_accept()
    assert dlg.get_selection() == [("RS 1%", 0.01), ("Loyers 10%", 0.10), ("Feuil3", 0.03)]
