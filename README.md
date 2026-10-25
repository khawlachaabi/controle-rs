# Contrôle RS — rapprochement des retenues à la source

Application de bureau qui automatise le **contrôle des retenues à la source (RS)** pour un cabinet d'audit tunisien : elle rapproche les écritures du **grand livre comptable** (DS Comptable) des **certificats déclarés sur la plateforme fiscale TEJ**, et produit un rapport Excel des correspondances et des écarts.

Développée pendant un stage chez ABC Audit. Les données de ce dépôt (noms, montants) sont **fictives**.

## Le problème

Chaque mois, l'auditeur doit vérifier que chaque retenue comptabilisée a bien été déclarée sur TEJ, et inversement. Les deux fichiers sont saisis à la main par des personnes différentes : les noms ne concordent pas (« RS/IMPRIMEX 16-01-2026 » d'un côté, « IMPRIMEX » de l'autre, parfois un nom en arabe d'un côté et en latin de l'autre), les dates sont décalées, et plusieurs opérations peuvent partager le même montant. Le rapprochement manuel est long et source d'erreurs.

## Comment le moteur rapproche les opérations

1. **Règles strictes** : le montant RS doit être identique au millime, et les dates à moins de 58 jours d'écart. Un écart d'exactement 0,001 crée un candidat, mais toujours soumis à validation humaine.
2. **Filtre de taux** : le taux réel (`RS / HTVA`) côté TEJ doit correspondre au taux de la catégorie choisie (catalogue officiel de 33 catégories).
3. **Score** de chaque candidat : similarité du fournisseur (alias validés, sinon nom normalisé, sinon similarité floue avec `rapidfuzz`) et proximité des dates.
4. **Affectation optimale 1-à-1** : résolution exacte du couplage biparti de poids maximal par composante connexe. Une opération ne peut être utilisée qu'une fois, et un vrai cas d'ambiguïté (alternatives quasi équivalentes) est signalé au lieu d'être tranché au hasard.
5. **Classification** : correspondance certaine / probable / à vérifier / absente d'un côté, plus des statuts métier explicites (certificat annulé, autre taux, hors périmètre).

Chaque décision est **expliquée** dans le rapport (raisons du score, motif du renvoi en vérification, certificat annulé ou d'un autre taux trouvé au même montant), et les validations humaines alimentent un **référentiel fournisseurs** persistant, réutilisé dans les analyses suivantes.

## Fonctionnalités

- Interface PySide6 avec glisser-déposer des fichiers Excel ; validation humaine du mapping des colonnes, avec une suggestion automatique basée sur l'intitulé et le contenu des colonnes.
- Analyse **achats / ventes** (choix de la feuille TEJ et de la colonne crédit/débit selon le sens).
- Classeur DS à **plusieurs feuilles, une par taux**, analysées séparément puis fusionnées dans un même rapport.
- Fenêtre de **vérification humaine** des cas incertains, avec mémorisation des alias fournisseurs.
- Contrôle du **délai de paiement** côté TEJ (risque de pénalité au-delà de 31 jours).
- Export Excel mis en forme (une couleur par statut, récapitulatif).
- Analyse en arrière-plan (l'interface reste réactive) et journal d'exécution pour le support.

## Stack

Python 3 · PySide6 (Qt) · pandas · openpyxl · rapidfuzz · pytest · PyInstaller

## Structure

```
core/       moteur : préparation des données, génération de candidats, score,
            affectation optimale, classification, référentiel fournisseurs
ui/         fenêtres Qt (principale, mapping, vérification, feuilles/taux)
utils/      export Excel, journal
tests/      tests automatiques (règles métier, colonnes, interface hors écran)
generate_realistic_dataset.py / analyze_realistic_dataset.py
            jeu de données synthétique de ~100 opérations avec vérité terrain,
            et mesure du moteur dessus (rappel des candidats, faux positifs)
```

## Lancer le projet

```bash
python -m venv venv
venv\Scripts\pip install -r requirements-dev.txt
venv\Scripts\python main.py          # application
venv\Scripts\python -m pytest tests  # tests
build_exe.bat                        # exécutable Windows autonome : dist\ControleRS.exe
```

## Résultats sur le jeu de données synthétique

Sur 100 certificats TEJ et 103 écritures DS couvrant les cas difficiles (fautes de frappe, noms arabes, décalages de dates, montants dupliqués, écarts d'un millime, ambiguïtés) : **100 % de rappel des candidats** et **aucun faux positif**. Les cas incertains sont envoyés en vérification humaine au lieu d'être validés automatiquement.

Pour le détail des règles métier et de leur évolution, voir [Documentation_Projet_Controle_RS_1.md](Documentation_Projet_Controle_RS_1.md).
