"""
Règles du moteur de rapprochement (core/reconciliation.py) sur de petits cas
construits à la main. Chaque test fige une règle métier documentée : si l'un
d'eux échoue après une modification, c'est qu'une règle a changé.
"""
from datetime import date

import pytest

from core.reconciliation import reconcile, fusionner_analyses, reappliquer_alias

from conftest import tej_df, ds_df, MAP_DS
from core.parser import map_tej_columns


def _analyser(tej_lignes, ds_lignes, registry, taux=0.01):
    tej = tej_df(tej_lignes)
    return reconcile(ds_df(ds_lignes), MAP_DS, tej, map_tej_columns(tej, "achat"), taux, registry=registry)


def _par_statut(results):
    out = {}
    for r in results:
        out.setdefault(r.statut, []).append(r)
    return out


def test_correspondance_certaine(registry):
    results, _ = _analyser(
        [("IMPRIMEX", 1000, 10.0, "15/01/2026", "REÇUE", None)],
        [("RS/IMPRIMEX 15-01-2026", 10.0, date(2026, 1, 15))],
        registry,
    )
    assert [r.statut for r in results] == ["MATCH_CERTAIN"]


def test_montant_different_pas_de_correspondance(registry):
    results, _ = _analyser(
        [("IMPRIMEX", 1000, 10.0, "15/01/2026", "REÇUE", None)],
        [("RS/IMPRIMEX", 10.5, date(2026, 1, 15))],
        registry,
    )
    assert sorted(r.statut for r in results) == ["COMPTA_ONLY", "FISCAL_ONLY"]


def test_ecart_un_millime_toujours_a_verifier(registry):
    results, _ = _analyser(
        [("IMPRIMEX", 1083.4, 10.834, "15/01/2026", "REÇUE", None)],
        [("RS/IMPRIMEX", 10.835, date(2026, 1, 15))],
        registry,
    )
    assert [r.statut for r in results] == ["REVIEW"]
    assert "ECART_MONTANT_0_001" in results[0].commentaire


def test_hors_fenetre_de_dates(registry):
    # 58 jours max entre DS et TEJ
    results, _ = _analyser(
        [("IMPRIMEX", 1000, 10.0, "15/01/2026", "REÇUE", None)],
        [("RS/IMPRIMEX", 10.0, date(2025, 11, 1)), ("RS/AUTRE", 1.0, date(2026, 1, 20))],
        registry,
    )
    statuts = {(r.statut, (r.fiscal_op or r.accounting_op).raw_name) for r in results}
    assert ("FISCAL_ONLY", "IMPRIMEX") in statuts
    assert ("COMPTA_ONLY", "RS/IMPRIMEX") in statuts


def test_fournisseur_tres_different_force_verification(registry):
    results, _ = _analyser(
        [("COMPAGNIE MENUISERIE ALUMINIUM", 1000, 10.0, "15/01/2026", "REÇUE", None)],
        [("RS/XYZ", 10.0, date(2026, 1, 15))],
        registry,
    )
    assert [r.statut for r in results] == ["REVIEW"]


def test_alias_valide_rend_la_correspondance_certaine(registry):
    registry.add_alias("KARIM HADDAD", "كريم حداد")
    registry.add_alias("KARIM HADDAD", "RS/LOYER KARIM HADDAD")
    results, _ = _analyser(
        [("كريم حداد", 1000, 100.0, "15/01/2026", "REÇUE", None)],
        [("RS/LOYER KARIM HADDAD", 100.0, date(2026, 1, 15))],
        registry, taux=0.10,
    )
    assert [r.statut for r in results] == ["MATCH_CERTAIN"]


def test_certificat_annule_signale(registry):
    results, stats = _analyser(
        [("IMPRIMEX", 1000, 10.0, "15/01/2026", "ANNULÉ", None),
         ("X", 300, 3.0, "30/01/2026", "REÇUE", None)],
        [("RS/IMPRIMEX", 10.0, date(2026, 1, 15)), ("RS/X", 3.0, date(2026, 1, 30))],
        registry,
    )
    assert stats["lignes_tej_annulees"] == 1
    # le certificat annulé n'est jamais un candidat, mais il est signalé sur la ligne DS
    imprimex = [r for r in results if r.accounting_op is not None and r.accounting_op.raw_name == "RS/IMPRIMEX"]
    assert [r.statut for r in imprimex] == ["CERTIFICAT_TEJ_ANNULE"]


def test_autre_taux_signale_hors_taux(registry):
    results, _ = _analyser(
        [("LOYER", 1000, 100.0, "15/01/2026", "REÇUE", None)],  # 10 % alors qu'on analyse 1 %
        [("RS/LOYER", 100.0, date(2026, 1, 15))],
        registry, taux=0.01,
    )
    assert [r.statut for r in results] == ["HORS_TAUX_SELECTIONNE"]


def test_delai_de_paiement_depasse_commentaire_sans_changer_le_statut(registry):
    results, stats = _analyser(
        [("IMPRIMEX", 1000, 10.0, "15/01/2026", "REÇUE", "01/12/2025")],
        [("RS/IMPRIMEX", 10.0, date(2026, 1, 15))],
        registry,
    )
    assert [r.statut for r in results] == ["MATCH_CERTAIN"]
    assert "Risque de pénalité" in results[0].commentaire
    assert stats["delais_paiement_depasses"] == 1


def test_aucune_date_ds_bloque_l_analyse(registry):
    with pytest.raises(ValueError, match="Aucune date DS"):
        _analyser(
            [("IMPRIMEX", 1000, 10.0, "15/01/2026", "REÇUE", None)],
            [("RS/IMPRIMEX", 10.0, None)],
            registry,
        )


def test_fusion_multi_feuilles(registry):
    tej_lignes = [
        ("IMPRIMEX", 1000, 10.0, "15/01/2026", "REÇUE", None),     # 1 %
        ("SALMA TRABELSI", 2000, 200.0, "18/01/2026", "REÇUE", None),  # 10 %
        ("SANS HTVA", None, 33.0, "19/01/2026", "REÇUE", None),     # taux non vérifiable : vu par les 2 feuilles
    ]
    tej = tej_df(tej_lignes)
    map_tej = map_tej_columns(tej, "achat")
    feuille1 = ds_df([("RS/IMPRIMEX", 10.0, date(2026, 1, 12)), ("RS/X", 999.0, date(2026, 1, 20))])
    feuille10 = ds_df([("RS/LOYER SALMA TRABELSI", 200.0, date(2026, 1, 18)), ("RS/Y", 77.0, date(2026, 1, 20))])

    analyses = []
    for libelle, df, taux in [("RS 1% (1.00%)", feuille1, 0.01), ("Loyers 10% (10.00%)", feuille10, 0.10)]:
        res, st = reconcile(df, MAP_DS, tej, map_tej, taux, registry=registry)
        analyses.append((libelle, res, st))
    results, stats = fusionner_analyses(analyses)

    assert {r.feuille for r in results} == {"RS 1% (1.00%)", "Loyers 10% (10.00%)"}
    sans_htva = [r for r in results if r.fiscal_op is not None and r.fiscal_op.raw_name == "SANS HTVA"]
    assert len(sans_htva) == 1 and sans_htva[0].statut == "FISCAL_ONLY"
    assert stats["lignes_tej_annulees"] == 0

    # la réapplication des alias reste cantonnée à chaque feuille (pas d'appariement entre taux)
    reappliquer_alias(results, registry)
    for r in results:
        if r.fiscal_op is not None and r.accounting_op is not None:
            assert r.fiscal_op.extra.get("htva") is None or abs(
                r.fiscal_op.amount / r.fiscal_op.extra["htva"] - (0.01 if "1%" in r.feuille else 0.10)) < 0.005
