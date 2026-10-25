# Plateforme de Contrôle des Retenues à la Source (RS) — ABC Audit

Documentation complète du projet : contexte métier, architecture, logique de rapprochement, historique des corrections, et code source intégral documenté.

> **⚠️ Note pour qui reprend ce projet (y compris Claude Code)** : les sections 1 à 4 ci-dessous retracent l'**historique** des itérations successives du projet, y compris des règles qui ont depuis été remplacées. Pour l'état **actuellement en vigueur** du code, lire le résumé ci-dessous puis se référer au code en section 5 (source de vérité).

## Résumé de l'état actuel (à jour au 2026-10-11)

> La section 5 (code source recopié) date d'avant le moteur bidirectionnel : **le code du dépôt fait foi**. Ce résumé décrit le code réellement en vigueur.

- **Moteur** : `core/reconciliation.py::reconcile` (moteur bidirectionnel). L'ancien moteur séquentiel `core/matcher.py::reconcile_data` (seuil de nom 75 %, fenêtre « fin du mois suivant », statuts CONFORME/AMBIGU/ECART_ABSENT_*) et l'ancien export `export_reconciliation_report` ont été **supprimés** le 2026-10-11 : plus appelés, et leurs règles contredisaient le moteur actuel. `core/matcher.py` ne contient plus que la préparation des lignes.
- **Hard rules** : montant RS identique au millime (exception unique : un écart d'exactement 0,001 crée un candidat, toujours forcé en « À vérifier ») + date dans une fenêtre bidirectionnelle de **58 jours** (`DATE_WINDOW_MAX_DAYS`).
- **Score** (fournisseur 25 % + date 15 %, normalisé) : fournisseur via alias validé (Référentiel Fournisseurs), sinon nom identique, sinon flou `token_sort_ratio`. Classement : ≥ 90 → correspondance certaine, ≥ 75 → probable, sinon « À vérifier ». Un fournisseur sous 40 % de similarité, une rectification TEJ ou un écart de 0,001 forcent toujours « À vérifier ».
- **Affectation** : 1-à-1 optimale par composante connexe ; ambiguïté réelle (alternatives à moins de 5 points du total optimal) → « À vérifier » avec la liste des candidats.
- **Taux de retenue** : filtre strict côté TEJ (`RS/HTVA` à ±0,5 point du taux choisi ; HTVA absent = non vérifiable, jamais exclu). Un DS sans correspondant mais dont le montant existe sous un autre taux → « Hors du taux RS sélectionné ».
- **Plusieurs feuilles DS (une par taux)** — *implémenté le 2026-10-11* : si le classeur DS a plusieurs feuilles, un dialogue (`ui/sheet_taux_dialog.py`) demande quelles feuilles analyser et le taux de chacune (deviné depuis le nom de feuille, ex. « Loyers 10% »). Chaque feuille est rapprochée séparément avec son taux, puis `fusionner_analyses` regroupe tout dans un même rapport (colonne « Feuille DS (taux) »). Le mapping des colonnes n'est redemandé que si la structure de la feuille diffère. Un certificat TEJ au taux non vérifiable vu par plusieurs feuilles n'apparaît qu'une fois.
- **Achats / Ventes** — *câblé le 2026-10-11* :
  - TEJ : la feuille dont le nom contient « achat » / « vente » est choisie automatiquement (sinon l'utilisateur la désigne). La contrepartie est `Raison social du bénéficiaire` pour les achats et **`Raison social du déclarant` pour les ventes** (le client qui retient l'impôt). *À confirmer sur un export TEJ ventes réel.*
  - DS : règle créditeur = achat, débiteur = vente. La colonne montant RS suggérée est « Crédit » (achats) ou « Débit » (ventes). Les lignes de l'autre sens, vides dans cette colonne, sont exclues. Choisir une colonne du sens opposé demande confirmation.
- **Période d'analyse** : bornée par les dates DS. Un certificat TEJ hors période peut matcher (fenêtre de 58 j), mais n'est jamais signalé « Fiscal uniquement ».
- **Annulés / DMI / rectifications** : annulés exclus du matching mais signalés (« Certificat TEJ trouvé mais ANNULÉ ») ; DMI/RECTIF côté DS → « Hors périmètre RS » (si la colonne type est mappée).
- **Délai de paiement TEJ** (> 31 jours entre création et paiement) : **rebranché le 2026-10-11** (il n'était plus appelé depuis le passage au nouveau moteur). Avertissement ajouté au commentaire, statut inchangé, compteur dans la notification de fin d'analyse.
- **Colonnes du rapport** (écran et Excel identiques) : Statut, Feuille DS (taux), Fournisseur TEJ, Fournisseur DS, Date TEJ, Date DS, Montant RS, Score, Commentaire + récapitulatif par statut.
- **Exécutable** : `build_exe.bat` → `dist\ControleRS.exe` (PyInstaller, `ControleRS.spec`, un seul fichier, sans console). Dans l'exe, le référentiel fournisseurs est stocké dans `%APPDATA%\ControleRS\supplier_registry.json`, initialisé au premier lancement avec celui du dépôt (auparavant il aurait été écrit dans un dossier temporaire supprimé à chaque fermeture).

- **Saisie libre du taux** : toujours lue comme un pourcentage (« 1 » = 1 %, « 7,5 » = 7,5 %). Avant, « 1 » était compris comme 100 %.
- **Analyse en arrière-plan** : le rapprochement tourne dans un fil séparé avec une fenêtre de progression ; l'interface ne se fige plus.
- **Journal** : `%APPDATA%\ControleRS\controle_rs.log` (diagnostics de chargement, périmètre, erreurs avec leur trace complète). C'est le premier fichier à demander en cas de problème chez un utilisateur.
- **Tests automatiques** : `tests/` (pytest, 35 tests) figent les règles métier ci-dessus. `venv\Scripts\python.exe -m pytest tests`. `build_exe.bat` refuse de construire l'exe si un test échoue.

### Points encore en attente

- Confirmer sur un export TEJ **ventes** réel que la contrepartie est bien `Raison social du déclarant`.

---


## 1. Contexte et objectif métier

Le logiciel est développé dans le cadre d'un stage chez **ABC Audit**, cabinet d'audit comptable. Il vise à automatiser le **contrôle des retenues à la source (RS)**, un impôt qui doit être déclaré chaque mois sur la plateforme officielle **TEJ**, sous peine de pénalités en cas de retard.

**Règles métier confirmées :**

- La RS concerne deux sens d'opération :
  - **Achats** (factures **émises** par le fournisseur, payées par ABC) → impôt payé **mensuellement**.
  - **Ventes** (factures **reçues**, RS retenue par le client d'ABC) → impôt accumulé et déduit **en fin d'année**.
- Le **taux de RS** dépend de la **nature de l'opération**, pas du sens achat/vente. Le taux réel se retrouve en divisant `Montant RS / Montant HTVA`.
  - **Évolution finale (3e itération)** : le taux n'est plus limité à Marchandise 1% / Loyer 10%. L'utilisateur choisit le taux attendu du fichier DS Comptable chargé dans une **liste déroulante couvrant ~32 catégories officielles de RS** (honoraires, loyers, jetons de présence, plus-values, etc. — voir `core/rules.py::CATALOGUE_TAUX_RS`), avec possibilité de **taper un taux personnalisé** si sa catégorie n'y figure pas.
  - Ce taux sert de **filtre strict** : seules les lignes TEJ dont `RS/HTVA` calculé correspond exactement (à 0,5 point près) au taux choisi sont candidates au rapprochement. Une ligne TEJ d'un autre taux est traitée comme une **opération différente par construction**, pas comme une anomalie de la même opération.
  - DS Comptable ne fournit **plus de montant HTVA du tout** — seul le montant RS y est comparé (HTVA reste nécessaire côté TEJ pour calculer le taux).
- Dans **DS Comptable** (logiciel comptable interne), le sens d'une ligne est déterminé par la colonne où elle apparaît : **créditeur → achat**, **débiteur → vente**. Le taux n'est *pas* garanti par le compte comptable utilisé (le même compte peut contenir des taux différents) → il doit être **recalculé**, pas déduit du compte. *(Note : cette règle de sens créditeur/débiteur n'est pas encore câblée dans le code — voir section 4.7.)*
- Dans **TEJ**, achats et ventes sont **déjà séparés en deux feuilles distinctes** ; c'est l'utilisateur qui indique, via un bouton dans le logiciel, quel sens est chargé.
- Le rapprochement ne se fait **jamais** entre un sens et l'autre (achat comparé à achat, vente à vente uniquement) — la séparation est faite **avant** l'appel à `reconcile_data()`, jamais à l'intérieur.
- Aucun identifiant fiable commun entre les deux fichiers (pas de matricule fiscal fiable) : tout est saisi manuellement, donc le rapprochement doit se faire sur une combinaison de **nom fournisseur (flou) + montants (stricts) + date de paiement (stricte)**.
- **Égalité stricte** sur les montants (pas de tolérance d'arrondi) et sur la date de paiement.
- Le nom du fournisseur, en revanche, peut varier dans l'écriture (ex. `AHMED MAJDOUB` vs `AHMAD MEJDOUB`, `STE X` vs `SOCIETE X`) → matching **approximatif (fuzzy)**, insensible à l'ordre des mots, aux accents et aux mots parasites (SARL, STE, MR, etc.).
- Une opération présente d'un seul côté (DS ou TEJ) doit être signalée comme écart à part entière : *"opération manquante côté TEJ"* / *"opération manquante côté DS Comptable"*.
- Contrôle de conformité indépendant du rapprochement : le **délai entre date de création et date de paiement ne doit pas dépasser un mois**, sous peine de risque de pénalité. Ce contrôle doit être vérifié **des deux côtés indépendamment** (DS a les deux dates sous des noms différents ; TEJ a les deux dates aussi), et toute différence entre les deux dates de création doit être signalée même si le rapprochement a réussi par ailleurs.
- Pas de base de données ni d'historique nécessaire : l'analyse se fait **à la volée**, à chaque chargement des deux fichiers, pour **un seul client** pour l'instant.
- Utilisateur unique : le comptable interne. Le logiciel doit être **autonome** — l'utilisateur ne modifie rien, il charge les deux fichiers et le logiciel fait le nettoyage, la détection de colonnes et l'analyse.
- Les noms de colonnes des fichiers sources (DS Comptable et TEJ) ne sont **pas fixes** et peuvent changer avec le temps → détection **automatique et flexible** des colonnes (nom fournisseur, montants, dates), avec confirmation manuelle en cas de doute, plutôt que des mappings figés en dur.
- Fichiers toujours au format **.xlsx** (jamais CSV).
- Livrable attendu : un **logiciel de bureau autonome** (pas un site à héberger), destiné à être **vendu au cabinet**, donc packagé en `.exe` à terme (via PyInstaller).

**Stack technique retenue :**

| Rôle | Outil |
|---|---|
| IDE | VS Code + extensions Python / Pylance |
| Langage | Python 3.11+ |
| Lecture/écriture Excel | `pandas` + `openpyxl` |
| Matching approximatif des noms | `rapidfuzz` |
| Parsing flexible des dates | `python-dateutil` |
| Interface graphique | `PySide6` |
| Packaging final en `.exe` | `PyInstaller` |
| Isolation des dépendances | `venv` |

---

## 2. Structure du projet

Les imports du code (`from core.parser import ...`, `from ui.main_window import ...`, `from utils.exporter import ...`) imposent l'arborescence suivante — **obligatoire** pour que l'application se lance :

```
project/
├── main.py                     # point d'entrée
├── core/                        # logique métier pure, sans dépendance à l'UI
│   ├── __init__.py
│   ├── normalizer.py            # nettoyage texte / montants / dates
│   ├── parser.py                # lecture Excel + détection auto des colonnes
│   ├── rules.py                 # règles métier centralisées (taux RS, délai)
│   └── matcher.py               # rapprochement DS <-> TEJ, classification
├── ui/                           # interface graphique (PySide6)
│   ├── __init__.py
│   ├── main_window.py            # fenêtre principale
│   ├── mapping_dialog.py         # confirmation manuelle du mapping colonnes
│   └── styles.py                 # thème visuel (QSS)
└── utils/
    ├── __init__.py
    └── exporter.py                # génération du rapport Excel final
```

**Séparation des responsabilités :**

- **`core/`** ne connaît rien de Qt/PySide6 : c'est la logique métier pure, testable indépendamment de l'interface.
- **`ui/`** orchestre l'expérience utilisateur et appelle `core/` et `utils/` ; ne contient aucune règle métier elle-même.
- **`utils/`** s'occupe uniquement de la présentation finale (export Excel stylisé), en aval du résultat produit par `core/matcher.py`.

**Flux de données à l'exécution :**

1. L'utilisateur dépose/sélectionne les deux fichiers `.xlsx` (DS Comptable et TEJ) et choisit Achats ou Ventes dans `ui/main_window.py`.
2. Chaque fichier est chargé et son mapping de colonnes est déterminé automatiquement par `core/parser.py::detect_columns()`. En cas d'échec de détection sur un rôle, `ui/mapping_dialog.py` s'ouvre pour confirmation manuelle.
3. `core/matcher.py::reconcile_data()` reçoit les deux DataFrames + leurs mappings, nettoie chaque ligne via `core/normalizer.py`, et effectue le rapprochement ligne à ligne en s'appuyant sur `core/rules.py` pour valider le taux RS et le délai de paiement.
4. Le résultat (liste de dictionnaires, un par ligne d'écart ou de correspondance) est affiché dans le tableau coloré de `ui/main_window.py`.
5. Sur demande, `utils/exporter.py::export_reconciliation_report()` génère le rapport Excel stylisé final à partir de ce même résultat.

---

## 3. Logique de rapprochement (`core/matcher.py::reconcile_data`)

Pour chaque ligne DS (déjà filtrée sur un seul sens, achat *ou* vente) :

1. **Montant illisible** (`montant_invalide`) → ligne exclue immédiatement du rapprochement, statut `ERREUR_MONTANT`.
2. Sinon, on cherche parmi les lignes TEJ **non encore utilisées** celles qui ont :
   - le **même montant HTVA** (égalité stricte),
   - le **même montant RS** (égalité stricte),
   - la **même date de paiement** (égalité stricte),
   - un **score de similarité de nom** (`rapidfuzz.fuzz.token_sort_ratio`) **≥ 75** (`SEUIL_FOURNISSEUR`).
3. **Aucun candidat** → ligne signalée comme absente côté TEJ (`ECART_ABSENT_TEJ`).
4. **Plusieurs candidats, dont les deux meilleurs scores sont à moins de 5 points d'écart** (`ECART_SCORE_AMBIGU`) → aucun choix automatique, statut `AMBIGU`, tous les candidats concernés sont retirés du pool (ils ne redeviennent pas disponibles pour une autre ligne DS).
5. **Un candidat clairement meilleur** → rapprochement retenu (`_ligne_rapprochee`) :
   - Le **taux RS** de chaque côté est validé via `core/rules.py::check_rs_rate()` (1 % marchandise / 10 % loyer, tolérance unique de 0,5 point).
   - Si le type détecté diffère entre DS et TEJ, ou si un taux est hors normes → `AVERTISSEMENT`.
   - Le **délai de paiement** est vérifié indépendamment côté DS et côté TEJ via `check_payment_delay()` (max 31 jours) → `AVERTISSEMENT` si dépassé d'un côté ou de l'autre.
   - Une différence de **date de création** entre les deux fichiers est signalée en commentaire, même si le rapprochement a réussi.
6. En fin de traitement, toute ligne TEJ **jamais matchée** (et dont le montant est lisible) est signalée comme absente côté DS Comptable (`ECART_ABSENT_DS_COMPTABLE`).

**Limite connue (non corrigée, à garder en tête)** : le traitement se fait dans l'ordre des lignes du fichier DS. Un cas `AMBIGU` détecté tôt "consomme" les candidats TEJ concernés, ce qui peut priver une ligne DS traitée plus tard d'un candidat qui lui aurait correspondu sans ambiguïté. Le résultat peut donc légèrement dépendre de l'ordre des lignes dans le fichier — à surveiller si ça devient gênant en usage réel.

---

## 4. Historique des corrections apportées

### 4.1 Constat initial : fichiers `.pyc` obsolètes

Les fichiers `matcher_cpython-314.pyc`, `parser_cpython-314.pyc` et `main_window_cpython-314.pyc` fournis initialement ont été inspectés (désérialisation `marshal`, comparaison des en-têtes source-size/mtime avec les `.py` actuels) :

| Fichier compilé | Constat |
|---|---|
| `main_window_cpython-314.pyc` | En phase avec le `.py` actuel (taille source identique, mêmes noms de fonctions/classes). |
| `parser_cpython-314.pyc` | **Obsolète** : version sans normalisation Unicode des accents (`_normaliser` absent), avec doublons manuels accentués/non-accentués dans `KEYWORDS_MAP` — l'ancienne approche que le `.py` actuel a remplacée. |
| `matcher_cpython-314.pyc` | **Compilé à partir d'un fichier source vide** (0 octet, aucune fonction) — inutilisable tel quel. |

→ Recommandation appliquée : ne jamais distribuer ces `.pyc`, supprimer tout `__pycache__` avant packaging PyInstaller pour éviter qu'une version obsolète du bytecode ne soit embarquée silencieusement dans l'exécutable final.

### 4.2 Bug corrigé — `core/normalizer.py::clean_amount`

**Avant** : retournait `0.0` pour toute valeur manquante (`NaN`) ou illisible (texte non convertible), au lieu de `None`.
**Conséquence** : `core/matcher.py` teste `montant_invalide = htva is None or rs is None` pour écarter une ligne du rapprochement — ce test ne se déclenchait donc **jamais**, et deux montants illisibles pouvaient matcher entre eux via `0.0 == 0.0`, exactement le bug que le code prétendait déjà avoir corrigé (voir le commentaire `CHANGEMENT` original dans `matcher.py`).
**Correction** : `clean_amount` retourne désormais `None` sur `NaN` ou erreur de conversion.
**Vérifié par test** : une ligne DS avec un montant illisible produit maintenant bien le statut `ERREUR_MONTANT` au lieu d'être silencieusement traitée comme `0.0`.

### 4.3 Bug corrigé — `MappingDialog` jamais utilisé

**Avant** : `core/parser.py::load_and_map_excel()` levait une `ValueError` si une colonne n'était pas détectée avec assez de confiance, en précisant dans son propre docstring que ce cas devait déclencher `ui/mapping_dialog.py::MappingDialog` pour une confirmation manuelle. Mais `ui/main_window.py` n'importait ni n'utilisait jamais cette classe : l'exception remontait directement jusqu'à une simple boîte d'erreur générique, sans offrir de correction possible à l'utilisateur.
**Correction** :
- `core/parser.py` : extraction de la lecture du fichier dans une nouvelle fonction `load_excel()`, séparée de la détection de colonnes (`load_and_map_excel()` reste disponible pour un usage non-interactif/tests).
- `ui/main_window.py` : nouvelle méthode `_load_with_mapping()` qui charge le fichier, tente la détection automatique, et — si des rôles restent non détectés — ouvre `MappingDialog` pour que l'utilisateur confirme ou corrige le mapping colonne par colonne avant de poursuivre l'analyse.

### 4.4 Bug corrigé — colonnes du rapport Excel désynchronisées

**Avant** : `utils/exporter.py` définissait `columns_order` avec une colonne `"Taux RS"`, qui n'existe dans **aucun** des dictionnaires retournés par `core/matcher.py` (les clés réelles sont `"Score nom (%)"` et `"Type retenue"`). Résultat : `df.reindex(columns=columns_order)` générait une colonne `Taux RS` systématiquement vide dans le rapport exporté, et supprimait silencieusement `Score nom (%)` et `Type retenue`, deux informations utiles pour le comptable.
**Correction** : `columns_order` aligné sur les clés réelles produites par `matcher.py` ; alignement des cellules mis à jour en conséquence (`Type retenue` et `Score nom (%)` centrés comme `Statut`).
**Vérifié par test** : les colonnes exportées sont désormais `['Statut', 'Fournisseur DS', 'Fournisseur TEJ', 'Score nom (%)', 'Montant HTVA', 'Montant RS', 'Type retenue', 'Commentaire']`.

### 4.5 Correction de logique métier — détermination du type marchandise/loyer

**Constat (signalé par toi après relecture)** : la première version du code déterminait le type (marchandise 1% / loyer 10%) **des deux côtés** via `RS/HTVA` (`check_rs_rate` appliqué à DS *et* à TEJ), et signalait un `AVERTISSEMENT` si les deux types calculés différaient. Or côté **DS Comptable**, marchandise et loyer sont dans des **comptes séparés** : un fichier chargé est **entièrement d'un seul type**, connu d'avance — ce n'est pas à l'application de le deviner ligne par ligne via une formule qui peut être ambiguë sur une ligne isolée. Côté **TEJ**, en revanche, les deux types sont réellement mélangés dans la même feuille, donc `RS/HTVA` y reste le seul moyen de déterminer le type.

**Correction appliquée :**

- **`core/rules.py`** : ajout des constantes `TYPE_MARCHANDISE`, `TYPE_LOYER` et du dictionnaire `TAUX_PAR_TYPE` pour retrouver le taux attendu à partir du type déclaré.
- **`ui/main_window.py`** : ajout d'un second groupe de boutons radio *"Type de retenue du fichier DS Comptable : Marchandises (1%) / Loyer (10%)"*, à côté du groupe Achats/Ventes déjà existant. La sélection est transmise à `reconcile_data()`.
- **`core/matcher.py::reconcile_data`** : nouveau paramètre obligatoire `type_ds_declare`. Lors de la recherche de candidats TEJ, une ligne TEJ n'est retenue que si son type **calculé** (`RS/HTVA`) correspond au type déclaré côté DS, **ou** si son taux est `ANORMAL` (conservé volontairement comme candidat pour ne pas perdre une vraie anomalie de saisie plutôt que de la classer à tort en "absente"). Une ligne TEJ d'un type **différent et cohérent** (ex : réellement 10% alors que DS déclare marchandise) est considérée comme une **opération différente par construction**, pas une variante douteuse de la même opération — elle n'est donc plus proposée comme candidat du tout.
- **`core/matcher.py::_ligne_rapprochee`** : ne calcule plus le type côté DS. Elle **valide** à la place le taux réellement saisi côté DS par rapport au type déclaré (tolérance identique, 0,5 point) et signale une incohérence si besoin ; elle continue de signaler séparément un taux `ANORMAL` côté TEJ.

**Vérifié par test** (voir table de résultats ci-dessous, rejouée avec le nouveau paramètre) :

| Cas | Résultat |
|---|---|
| Ligne DS et ligne TEJ toutes deux à 1%, DS déclare "Marchandise" | `CONFORME` |
| Ligne DS et TEJ identiques mais à un taux anormal (5%), DS déclare "Marchandise" | `AVERTISSEMENT` — incohérence de taux signalée des deux côtés, mais la ligne est bien rapprochée (pas perdue) |
| Seule ligne TEJ disponible est à 1% (marchandise réelle) alors que DS déclare "Loyer" | `ECART_ABSENT_TEJ` — traité comme une opération distincte, pas comme une anomalie de la même opération |
| Suite de test complète (`DS_Comptable_TEST_Achats.xlsx` / `TEJ_TEST_Achats.xlsx`, rejouée avec `type_ds_declare=MARCHANDISE`) | Les 6 statuts (`CONFORME`, `AVERTISSEMENT` ×2, `AMBIGU`, `ERREUR_MONTANT`, `ECART_ABSENT` ×2) toujours corrects, avec un message d'incohérence de taux plus précis pour la ligne SARL DELTA LOYER |

### 4.6 Point observé mais **non corrigé** (à valider avec toi)

Côté TEJ, une ligne dont le montant est illisible est actuellement **totalement absente** du rapport final (ni `ERREUR_MONTANT`, ni `ECART_ABSENT`) — elle est filtrée silencieusement dans la boucle finale de `reconcile_data()` (`if not tej["matched"] and not tej["montant_invalide"]`), contrairement au côté DS qui génère bien une ligne `ERREUR_MONTANT`. Non modifié pour l'instant, en attente de ta confirmation.

### 4.7 Gap identifié — le filtrage achat/vente n'est pas câblé

Le bouton radio *"Achats / Ventes"* existe dans l'interface depuis le départ, mais **rien dans le code actuel ne filtre réellement** les lignes DS Comptable selon la colonne créditeur/débiteur (la règle métier confirmée : créditeur → achat, débiteur → vente). `reconcile_data()` reçoit aujourd'hui les DataFrames tels quels, sans séparation par sens — le radio bouton ne pilote donc pour l'instant que l'affichage, pas la logique. À traiter dans une prochaine session : ajouter la détection de la colonne créditeur/débiteur (nouveau rôle dans `core/parser.py::KEYWORDS_MAP`) et filtrer `df_ds` en conséquence avant l'appel à `reconcile_data()`.

### 4.8 Autres pistes d'amélioration identifiées (non appliquées)

- `core/parser.py::detect_columns` : appel redondant à `process.extractOne(...)` répété inutilement à chaque mot-clé dans une boucle où le résultat ne dépend pas du mot-clé — à sortir de la boucle interne pour la performance.
- `core/parser.py::detect_columns` : risque de `ZeroDivisionError` si un en-tête Excel se normalise en chaîne vide.
- `core/normalizer.py::clean_date` : parsing avec `dayfirst=True` fixe — un même événement pourrait être interprété différemment si DS et TEJ n'utilisent pas la même convention de date, faisant échouer l'égalité stricte sur la date de paiement.
- `ui/main_window.py` : `QProgressBar` importé mais jamais utilisé — pas de retour visuel pendant l'analyse d'un gros fichier.
- Performance : `core/matcher.py` est en O(n×m) et `core/parser.py::_preparer_records` utilise `df.iterrows()` — correct pour des volumes actuels, à surveiller si les fichiers grossissent significativement.

### 4.9 Colonnes TEJ confirmées sur export réel + bug corrigé — `core/parser.py::map_tej_columns`

**Constat (capture d'écran d'un export TEJ réel fournie par toi, 2026-08-18)** : le fichier contient plusieurs colonnes "raison sociale" proches :
`Raison social du déclarant`, `Raison social du bénéficiaire` (celle qui nous intéresse — remarque : sans "e" à "social", contrairement à ce que supposait le code), et `Raison sociale de l'émetteur` (celle-ci avec l'accord correct). La colonne de date de saisie, jusque-là non confirmée, existe bien sous le nom exact `Date de création`.

**Bug identifié** : l'ancien nom en dur `"Raison sociale du bénéficiaire"` ne correspondait donc **jamais exactement** au fichier réel, ce qui déclenchait systématiquement le repli par mots-clés. Or le mot-clé `"raison sociale"` matche **exactement** `Raison sociale de l'émetteur` (accord correct) mais pas `Raison social du bénéficiaire` (accord manquant) — seul le mot-clé plus faible `"beneficiaire"` matchait cette dernière. Les deux colonnes obtenaient un score de confiance très proche, avec un risque réel que la détection automatique retienne silencieusement le nom de l'**émetteur** (probablement ABC Audit elle-même) à la place du **fournisseur** réel, faussant tout le rapprochement sans erreur visible.

**Correction appliquée (`core/parser.py`)** :
- `COLONNES_TEJ_STANDARD["fournisseur"]` corrigé en `"Raison social du bénéficiaire"` (nom exact confirmé) — la détection par mots-clés ambiguë n'intervient donc plus du tout pour ce rôle en usage normal.
- `COLONNES_TEJ_STANDARD["date_creation"]` ajouté avec le nom confirmé `"Date de création"` — le contrôle de délai de paiement (pénalité si > 31 jours) peut désormais s'activer par défaut au lieu de rester silencieux faute de colonne trouvée.
- `map_tej_columns()` simplifiée : un seul mécanisme de repli par mots-clés pour tous les rôles sans correspondance exacte (au lieu d'un traitement séparé pour `date_creation` seule), restreint aux colonnes pas encore attribuées. Corrige au passage un bug latent similaire pour les 5 rôles historiques : avant, seul le repli de `date_creation` excluait les colonnes déjà utilisées ; un rôle parmi les 5 autres nécessitant un repli aurait pu reprendre une colonne déjà assignée en dur à un autre rôle.

**Vérifié par test** : mapping reconstruit avec les colonnes réelles (y compris `Raison social du déclarant` et `Raison sociale de l'émetteur` comme leurres) — `fournisseur` pointe bien vers `Raison social du bénéficiaire`, `date_creation` vers `Date de création`, aucune confusion.

### 4.10 Bug corrigé — égalité stricte sur les montants sensible au bruit flottant

**Constat (signalé par toi)** : `core/matcher.py::reconcile_data` compare les montants RS entre DS et TEJ par égalité stricte (`==` sur des `float`). Deux montants comptablement identiques peuvent arriver comme deux floats infimement différents selon la façon dont Excel/pandas a stocké la valeur (ex. `99.99999999999999` au lieu de `100.0`), ce qui aurait fait rater silencieusement un rapprochement pourtant valide.

**Contrainte posée par toi** : corriger sans introduire de tolérance qui puisse faire matcher deux montants réellement différents (comptablement, on ne peut pas se permettre une fausse correspondance).

**Correction appliquée (`core/normalizer.py::clean_amount`)** : le montant est désormais arrondi au millime (3 décimales — précision réelle du dinar tunisien, cohérente avec le format d'export `'#,##0.000 "DT"'` dans `utils/exporter.py`) dès son nettoyage, avant toute comparaison. Un montant comptable réel ne varie jamais de moins de 0,001 ; arrondir au millime absorbe donc uniquement le bruit de représentation flottante (de l'ordre de 10⁻¹⁵), jamais un écart réel. Un seul point de correction (à la source), qui profite automatiquement à toutes les comparaisons en aval (`reconcile_data`, `_diagnostiquer_absence`) sans toucher `matcher.py`.

**Vérifié par test** : `clean_amount(99.99999999999999) == clean_amount(100.0)` → `True` (bruit absorbé) ; `clean_amount(100.001) != clean_amount(100.0)` → toujours distincts (écart réel préservé). Suite de test complète du pipeline (chargement → rapprochement → export) rejouée sans régression.

### 4.11 Bug corrigé — inversion silencieuse jour/mois sur les dates Excel natives

**Constat (découvert en testant sur les fichiers réels)** : `core/normalizer.py::clean_date` reconvertissait TOUTE date en texte avant de la parser avec `date_parser.parse(str(val), dayfirst=True)`, même quand `val` était déjà un objet date/`datetime`/`pd.Timestamp` valide (cas normal pour une colonne de date native Excel, ex. "Échéance" côté DS Comptable). Or `str(pd.Timestamp('2026-01-12'))` donne le texte ISO `"2026-01-12 00:00:00"`, et re-parser CE texte avec `dayfirst=True` inverse silencieusement jour et mois dès que les deux sont ≤ 12 : le 12 janvier devenait le 1er décembre. Ce bug touche ~39% des dates (tous les jours du 1er au 12 de chaque mois) et faussait complètement le rapprochement par date.

**Correction appliquée (`core/normalizer.py::clean_date`)** : une valeur déjà reçue comme objet `datetime`/`date` (cas des colonnes de date natives Excel) est utilisée directement, sans repasser par le texte. Le ré-parsing avec `dayfirst=True` reste utilisé uniquement pour les valeurs réellement textuelles (ex. dates TEJ au format `"15/01/2026"`).

**Vérifié par test** : `clean_date(pd.Timestamp('2026-01-12'))` → `2026-01-12` (avant : `2026-12-01`, faux) ; `clean_date('15/01/2026')` (chaîne) → toujours `2026-01-15`, correct.

### 4.12 Décision métier confirmée — colonne de date DS Comptable

**Contexte** : après correction du bug 4.11, le rapprochement sur les fichiers réels de test (`janvier RS DS.xlsx` + `Liste des certificats émis ACH.xlsx`) donnait toujours **0 correspondance sur 51 lignes DS valides**, alors que les vraies paires (même fournisseur, même montant RS, taux conforme) existaient bien côté TEJ. Diagnostic détaillé : l'écart entre la date DS et la "Date de paiement" TEJ de la vraie correspondance était systématique et croissant (de 82 à 497 jours selon la ligne, moyenne 261 jours) — jamais dans le même mois, y compris en essayant "Date" et "Échéance" côté DS. Les écritures DS concernées sont de type journal `"OD"` (opérations diverses), non lettrées (`Lett`=0, pas de `Ref lettrage`/`Date lettrage`).

**Décision confirmée par toi** : **colonne de date DS Comptable à utiliser pour le rapprochement : "Date"** (1ère colonne, date de l'écriture comptable), pas "Échéance" — cette dernière n'a pas de lien fiable avec la date réelle de paiement TEJ sur ce type d'écriture.

**Correction appliquée (`core/parser.py::KEYWORDS_MAP["date_paiement"]`)** : mot-clé `"echeance"` retiré, `"date"` (nu) ajouté. Avant, "Date" et "Échéance" scoraient à égalité dans `detect_columns()` et le choix ne dépendait que de l'ordre des colonnes dans le fichier (un hasard) ; "Date" est maintenant préféré de façon déterministe.

*Note : ce constat a aussi mené, dans un premier temps, à retirer complètement le critère "même mois" du rapprochement (37 correspondances trouvées, mais avec des écarts de plusieurs mois à plus d'un an) — décision revenue en arrière ensuite, voir 4.13 et 4.14.*

### 4.13 Décision métier confirmée — portée de comparaison limitée aux mois communs aux deux fichiers

**Constat** : `janvier RS DS.xlsx` ne couvre que janvier à juin 2026 (par "Date"), alors que `Liste des certificats émis ACH.xlsx` couvre janvier 2025 à juillet 2026 — un TEJ historique bien plus large que la période DS chargée. Sans limite de portée, chaque ligne TEJ de 2025 (hors du périmètre du fichier DS chargé) ressortait à tort comme `ECART_ABSENT_DS_COMPTABLE`, alors que le fichier DS ne prétend même pas couvrir cette période.

**Décision confirmée par toi** : un mois totalement absent d'un des deux fichiers (aucune opération ce mois-là, ex. février 2025 s'il n'existe dans aucun des deux) doit faire éliminer ce mois de **l'autre** fichier aussi, avant le rapprochement — pas de comparaison possible sur une période qu'un des deux fichiers ne couvre pas du tout.

**Correction appliquée (`core/matcher.py::reconcile_data`)** : après nettoyage des lignes DS et TEJ, calcul de l'ensemble des mois `(année, mois)` présents de chaque côté ; toute ligne DONT le mois n'existe pas du tout côté opposé est retirée avant le rapprochement (les lignes sans date exploitable ne sont jamais retirées par cette règle, faute de mois à comparer). Ce filtrage est silencieux — pas de nouveau compteur affiché, cohérent avec 4.16.

**Vérifié par test** : sur les fichiers réels, cette règle élimine correctement les lignes TEJ de 2025 (hors périmètre DS) de la comparaison ; sur la suite de test synthétique, une ligne TEJ dont le mois n'existe pas du tout côté DS n'apparaît plus en `ECART_ABSENT_DS_COMPTABLE` (comportement voulu, pas une régression).

### 4.14 Décision métier confirmée — fenêtre de délai à la place du "même mois" strict

**Contexte** : en combinant 4.12 (retrait complet du critère mois) et 4.13 (portée limitée), le rapprochement retombait à **0 correspondance** — les vraies paires montant+nom trouvées avaient toutes leur Date de paiement TEJ *avant* la date DS de plusieurs mois, ce que 4.13 éliminait légitimement du périmètre. Tu as alors précisé : un écart de plus d'un mois n'est **pas** une vraie correspondance, même à montant et nom identiques — mais un léger délai de déclaration doit être toléré (ex. une opération de juillet 2026 doit pouvoir être déclarée à TEJ jusqu'au 31 août 2026).

**Décision confirmée par toi** : remplacer le "même mois" strict par une fenêtre de délai — la Date de paiement TEJ doit tomber entre le 1er jour du mois de la date DS et le dernier jour du mois **suivant** (jamais avant le 1er du mois DS, jamais après la fin du mois d'après).

**Correction appliquée (`core/matcher.py`)** :
- Nouvelle fonction `_dans_delai_rapprochement(d_ds, d_tej)` (remplace `_meme_mois`, supprimée car devenue inutilisée) : calcule la fenêtre `[1er jour du mois DS ; dernier jour du mois suivant]` via `calendar.monthrange`, gère le passage d'année (décembre → janvier de l'année suivante).
- `reconcile_data` : ce nouveau filtre remplace `_meme_mois` dans la boucle de sélection des candidats — restauré comme critère éliminatoire (comme avant 4.12), mais avec la fenêtre élargie au lieu du mois calendaire strict.
- `_diagnostiquer_absence` : étape "mois" restaurée dans l'escalade de diagnostic (montant → délai → taux → nom), avec message adapté ("hors du délai accepté" plutôt que "autre échéance").
- `_ligne_rapprochee` : le commentaire `AVERTISSEMENT` d'écart de mois ajouté en 4.12 est retiré — redondant maintenant que le délai est de nouveau un critère éliminatoire (un candidat qui atteint `_ligne_rapprochee` est forcément dans la fenêtre acceptée).

**Vérifié par test** : cas limite juillet 2026 → TEJ accepté jusqu'au 31/08/2026 inclus, rejeté au 01/09/2026 et avant le 01/07/2026 ; cas de passage d'année (décembre 2026 → fenêtre jusqu'au 31/01/2027). Suite de test synthétique rejouée sans régression.

### 4.15 Bug corrigé — date collée au libellé faussant le score de similarité de nom

**Constat (signalé par toi, avec capture d'écran de test)** : certaines lignes DS ont un libellé du type `"RS/IMPRIMEX 16-01-2026"` (date collée au nom du fournisseur). `core/normalizer.py::clean_text` ne retirait pas cette date, qui survivait sous forme de jetons numériques isolés (`"IMPRIMEX 16 01 2026"`). Comparé au `"IMPRIMEX"` côté TEJ, le score de similarité de nom tombait à **62%**, sous le seuil de 75% (`SEUIL_FOURNISSEUR`) — une vraie correspondance (même fournisseur, probablement même montant et même date) était donc rejetée pour "nom trop différent".

**Correction appliquée (`core/normalizer.py::clean_text`)** : retrait des dates au format JJ-MM-AAAA ou JJ/MM/AAAA (regex `\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b`) avant le reste du nettoyage. Motif volontairement restreint aux dates complètes pour ne pas retirer de vrais chiffres faisant partie d'un nom (ex. "STE 4 SEASONS ADV" reste intact).

**Vérifié par test** : `clean_text("RS/IMPRIMEX 16-01-2026")` → `"IMPRIMEX"` (score de similarité avec `"IMPRIMEX"` : 100% au lieu de 62%) ; `clean_text("RS/STE 4 SEASONS ADV")` → `"4 SEASONS ADV"` (le "3" n'est pas retiré, ce n'est pas un motif de date). Suite de test synthétique rejouée sans régression.

### 4.16 Décision métier confirmée — notification de fin d'analyse simplifiée

**Décision confirmée par toi** : la popup de fin d'analyse ne doit plus détailler le nombre de lignes exclues (montant RS illisible côté DS, opérations annulées côté TEJ) — ce sont des exclusions attendues et normales (écriture au débit sans montant RS, opération annulée), pas des anomalies à signaler à chaque analyse. "Juste ignorer les lignes et passer au rapport."

**Correction appliquée (`ui/main_window.py::run_reconciliation`)** : la popup affiche uniquement le total d'opérations dans le rapport. Les lignes restent exclues du rapport exactement comme avant (comportement de `reconcile_data` inchangé, `stats` toujours retourné mais plus affiché) — seul l'affichage du décompte dans la popup est retiré.

### 4.17 Décision métier confirmée — correspondance directe sans redondance, même si le nom diverge

**Décision confirmée par toi** : dans une même fenêtre de délai (voir 4.14), si un montant RS identique se retrouve exactement des deux côtés et qu'aucune autre ligne TEJ ne partage ce même montant sur cette même période (pas de redondance pouvant créer un conflit), cette ligne doit être comptée comme une correspondance directement — même si le nom du fournisseur ne franchit pas le seuil de similarité de 75%. Utile pour des libellés DS abrégés/codés (ex. `"RS CMA"` vs `"COMPAGNIE MENUISERIE ALUMINIUM CMA"` côté TEJ, score de nom ≈ 18%) où le montant + la période suffisent déjà à identifier l'opération sans ambiguïté.

**Correction appliquée (`core/matcher.py::reconcile_data`)** : la boucle de sélection des candidats est restructurée en deux temps. D'abord, les candidats sont filtrés uniquement sur montant (strict) + délai (voir 4.14) + taux conforme, **sans filtre de nom**. Si un seul candidat passe ces trois critères, c'est une correspondance directe (`_ligne_rapprochee` appelée avec ce candidat, quel que soit son score de nom) — pas de redondance possible, donc pas besoin du nom pour trancher. Si **plusieurs** candidats passent ces trois critères (redondance réelle), le nom sert alors de désambiguïsateur exactement comme avant (seuil `SEUIL_FOURNISSEUR`, puis `ECART_SCORE_AMBIGU` pour détecter une ambiguïté persistante).

**Correction appliquée (`core/matcher.py::_ligne_rapprochee`)** : quand une ligne arrive avec un score de nom sous le seuil (acceptée via la règle ci-dessus), un commentaire `AVERTISSEMENT` explique pourquoi ("Nom fournisseur peu similaire... mais accepté : seule ligne TEJ avec ce montant sur cette période") — pour que le comptable comprenne un score bas affiché dans le rapport sans le prendre pour une anomalie du logiciel.

**Vérifié par test** : suite de test synthétique rejouée sans régression (les cas `AMBIGU` avec plusieurs candidats à montant identique continuent de nécessiter le nom pour être tranchés). Sur les fichiers réels de test, cette règle ne change rien pour l'instant : aucune ligne DS ne passe encore montant + délai + taux du tout (le problème de fond reste l'écart de plusieurs mois entre DS et TEJ identifié en 4.12-4.14, indépendant du nom) — elle s'activera dès qu'un cas montant+délai+taux unique mais nom divergent se présentera.

---

## 5. Code source complet (version actuelle, documentée)

Chaque fichier ci-dessous est la version **actuellement fonctionnelle**, avec les commentaires `CHANGEMENT` d'origine (expliquant les évolutions déjà faites avant cette session) et les commentaires de correction ajoutés lors de cette session.

### `main.py`

*Point d'entrée de l'application. Initialise Qt, applique le thème, lance MainWindow.*

```python
import sys
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

# Importation de l'interface et des styles du projet
from ui.main_window import MainWindow
from ui.styles import QSS_THEME


def get_asset_path(relative_path: str) -> str:
    """
    Gère la résolution du chemin vers les ressources (icônes, images)
    compatible à la fois en mode développement et une fois compilé avec PyInstaller (.exe).
    """
    if hasattr(sys, '_MEIPASS'):
        # Mode binaire compilé PyInstaller
        return os.path.join(sys._MEIPASS, relative_path)
    # Mode développement standard Python
    return os.path.join(os.path.abspath("."), relative_path)


def main():
    # 1. Initialisation de l'application Qt
    app = QApplication(sys.argv)
    
    # Nom de l'application (affiché dans le gestionnaire des tâches / OS)
    app.setApplicationName("Contrôle RS - ABC Audit")
    app.setOrganizationName("ABC Audit Comptable")

    # 2. Application du style graphique global (QSS)
    app.setStyle("Fusion")  # Style de base uniforme sur tous les OS
    app.setStyleSheet(QSS_THEME)

    # 3. Chargement de l'icône de l'application (si le dossier assets existe)
    icon_path = get_asset_path("assets/icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # 4. Création et affichage de la fenêtre principale
    window = MainWindow()
    
    # Si une icône existe, on l'affecte aussi explicitement à la fenêtre
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))
        
    window.show()

    # 5. Lancement de la boucle d'événements
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

### `core/__init__.py`

*Marqueur de package (vide).*

```python

```

### `core/normalizer.py`

*Nettoyage des données brutes : texte fournisseur, montants, dates.*

```python
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

    # Suppression des accents (ex: Sté -> STE)
    text = unicodedata.normalize('NFD', text).encode('ascii', 'ignore').decode('utf-8')

    # Retrait des caractères spéciaux (garde uniquement lettres et chiffres)
    text = re.sub(r'[^A-Z0-9\s]', ' ', text)

    # Retrait des mots parasites (Stop Words)
    for word in STOP_WORDS:
        text = re.sub(word, '', text)
        
    # Suppression des espaces multiples et des espaces de début/fin
    text = re.sub(r'\s+', ' ', text).strip()
    return text

PRECISION_MONTANT = 3  # dinar tunisien : précision au millime (0,001)


def clean_amount(val):
    """
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
    """
    if pd.isna(val):
        return None
    if isinstance(val, (int, float)):
        return round(float(val), PRECISION_MONTANT)

    # Si c'est une chaîne, retrait des symboles monétaires et espaces
    val_str = str(val).replace('DT', '').replace('TND', '').replace(' ', '').replace(',', '.')
    try:
        return round(float(val_str), PRECISION_MONTANT)
    except ValueError:
        return None

def clean_date(val):
    r"""
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
```

### `core/parser.py`

*Chargement des fichiers Excel et détection automatique des colonnes.*

```python
import unicodedata

import pandas as pd
from rapidfuzz import process, fuzz

# Ordre volontaire : les roles aux mots-cles les plus specifiques sont traites
# en premier (date_paiement avant date_creation) pour que le role le plus
# "generique" ne vole pas une colonne qui appartient clairement a un role
# plus precis. Voir _retirer_colonne_utilisee ci-dessous.
#
# CHANGEMENT (logique métier corrigée, 2e itération) : ajout du rôle
# "statut" (pour repérer et exclure les opérations "annulé" côté TEJ) et
# élargissement des mots-clés de date_creation pour couvrir "date de
# saisie/écriture/génération", qui n'apparaissaient pas avant.
#
# CHANGEMENT (décision métier confirmée sur données réelles, voir 4.12) :
# "echeance" retiré des mots-clés de date_paiement, "date" (bare) ajouté à
# la place. Sur les écritures DS Comptable de type "OD" (opérations
# diverses) servant à enregistrer une RS, la colonne "Échéance" a été
# vérifiée sur un fichier réel comme étant systématiquement décorrélée de
# la vraie date TEJ — la colonne "Date" (date de l'écriture comptable) est
# la bonne référence. Avant, "Date" et "Échéance" scoraient à égalité (100)
# dans detect_columns() et le choix ne dépendait que de l'ordre des
# colonnes dans le fichier — un hasard, pas une décision.
KEYWORDS_MAP = {
    "fournisseur": ["nom", "tiers", "fournisseur", "client", "raison sociale", "frs", "clt", "nom/prenom", "beneficiaire"],
    "statut": ["statut", "status", "etat"],
    "date_paiement": ["date paie", "date reglement", "date paiement", "paie", "date"],
    "date_creation": ["date fac", "date creation", "date saisie", "date ecriture", "date generation", "date piece", "date devis", "date doc"],
    "htva": ["ht", "htva", "montant ht", "base impot", "montant hors taxe", "base"],
    "rs": ["rs", "retenue", "montant rs", "impot", "retenue a la source", "m-rs"],
}

SEUIL_MIN_ACCEPTABLE = 70


def _normaliser(texte: str) -> str:
    """
    Minuscule + suppression des accents.

    CHANGEMENT : appliqué à la fois aux en-têtes du fichier ET aux
    mots-clés de KEYWORDS_MAP, pour que la comparaison ne dépende plus de
    la présence exacte d'un accent des deux côtés (avant, "Date de Création"
    ne matchait que si le mot-clé était écrit avec le même accent).
    """
    texte = str(texte).strip().lower()
    texte = unicodedata.normalize('NFD', texte).encode('ascii', 'ignore').decode('ascii')
    return texte


def detect_columns(df: pd.DataFrame, roles: list = None) -> dict:
    """
    Analyse les colonnes d'un DataFrame et associe chaque rôle à la colonne
    la plus pertinente. Retourne {'fournisseur': 'Nom_Col_Excel', ...}.

    :param roles: sous-ensemble de rôles à détecter (clés de KEYWORDS_MAP).
        None = tous les rôles. CHANGEMENT (logique métier corrigée, 2e
        itération) : ajouté pour que ui/main_window.py puisse ne détecter
        QUE les rôles pertinents pour un fichier donné (DS Comptable n'a
        plus besoin de htva/date_creation, par exemple), au lieu d'exiger
        systématiquement les 5 rôles historiques pour tous les fichiers.

    Un rôle peut rester à None si aucune colonne ne dépasse le seuil de
    confiance minimal — c'est volontaire, voir load_and_map_excel().
    """
    roles_a_traiter = roles if roles is not None else list(KEYWORDS_MAP.keys())
    detected_mapping = {}
    colonnes_disponibles = {_normaliser(col): col for col in df.columns}

    for role in roles_a_traiter:
        mots_cles_bruts = KEYWORDS_MAP[role]
        mots_cles = [_normaliser(m) for m in mots_cles_bruts]
        best_match = None
        highest_score = 0

        for col_clean, col_original in colonnes_disponibles.items():
            for kw in mots_cles:
                if kw in col_clean:
                    score = 90 + (len(kw) / len(col_clean)) * 10
                    if score > highest_score:
                        highest_score = score
                        best_match = col_original

            if highest_score < 80 and mots_cles:
                match, score, _ = process.extractOne(col_clean, mots_cles, scorer=fuzz.partial_ratio)
                if score > highest_score and score >= 70:
                    highest_score = score
                    best_match = col_original

        # CHANGEMENT : rien n'est retenu si le score reste sous le seuil minimal
        # (avant, un score de 0 pouvait quand même laisser best_match=None sans
        # que ce soit distingué d'un score faible mais non nul et douteux).
        if highest_score < SEUIL_MIN_ACCEPTABLE:
            best_match = None

        detected_mapping[role] = best_match

        # CHANGEMENT : exclusivité des colonnes. Avant, rien n'empêchait deux
        # rôles différents de pointer vers la même colonne (ex : une seule
        # colonne "Date" générique était assignée à la fois à date_creation
        # ET date_paiement, ce qui aurait rendu le contrôle de délai de
        # paiement toujours égal à 0 jour, silencieusement).
        if best_match is not None:
            cle_utilisee = next(k for k, v in colonnes_disponibles.items() if v == best_match)
            del colonnes_disponibles[cle_utilisee]

    return detected_mapping


def load_excel(file_path: str) -> pd.DataFrame:
    """
    Charge le fichier Excel et nettoie les noms de colonnes, sans détection
    de mapping. Séparé de load_and_map_excel pour que l'appelant (ui/main_window.py)
    puisse relire le même DataFrame et proposer un mapping manuel via
    ui/mapping_dialog.py si detect_columns() échoue, sans avoir à recharger
    le fichier depuis le disque.
    """
    df = pd.read_excel(file_path)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def load_and_map_excel(file_path: str) -> tuple[pd.DataFrame, dict]:
    """Charge le fichier Excel et retourne le DataFrame ainsi que le mapping des colonnes."""
    df = load_excel(file_path)
    mapping = detect_columns(df)

    # CHANGEMENT : avant, un rôle non détecté restait à None sans avertissement,
    # et le reste du pipeline (matcher.py) l'utilisait silencieusement en
    # récupérant des valeurs manquantes via row.get(None). Maintenant l'échec
    # est explicite, avec la liste des colonnes disponibles pour diagnostiquer
    # — et c'est ce cas qui doit déclencher ui/mapping_dialog.py pour une
    # confirmation manuelle plutôt qu'un plantage plus loin dans le code.
    manquants = [role for role, col in mapping.items() if col is None]
    if manquants:
        raise ValueError(
            f"Colonnes non détectées automatiquement pour : {manquants}. "
            f"Colonnes disponibles dans le fichier : {list(df.columns)}. "
            f"Une confirmation manuelle est nécessaire."
        )

    return df, mapping


# CHANGEMENT (logique métier corrigée, 2e itération) : DS Comptable n'a
# plus besoin que de 3 rôles — le montant HTVA et la date de création n'y
# sont plus utilisés (voir core/matcher.py et core/rules.py).
ROLES_DS = ["fournisseur", "rs", "date_paiement"]

# CHANGEMENT (logique métier corrigée, 2e itération) : contrairement à DS
# Comptable (dont les intitulés de colonnes varient d'une société à
# l'autre), le fichier exporté par TEJ est un format standard, identique
# pour toutes les sociétés. On fixe donc ses noms de colonnes en dur au
# lieu de les redétecter par mots-clés à chaque chargement — plus fiable.
# Si un intitulé ne correspond pas exactement (le format TEJ a changé, ou
# diffère légèrement), on retombe automatiquement sur la détection par
# mots-clés pour CE rôle uniquement, pour ne pas bloquer l'utilisateur.
#
# CHANGEMENT (colonnes confirmées sur export réel, voir 4.9) : "fournisseur"
# corrigé en "Raison social du bénéficiaire" (sans -e à "social" dans
# l'export réel — l'ancienne valeur ne correspondait jamais exactement et
# forçait un repli par mots-clés risqué : le fichier TEJ contient AUSSI
# "Raison social du déclarant" et surtout "Raison sociale de l'émetteur",
# qui matche mieux le mot-clé "raison sociale" — sans nom exact, la
# détection automatique pouvait silencieusement prendre le nom de
# l'émetteur/déclarant à la place de celui du fournisseur réel).
# "date_creation" ajoutée avec le nom confirmé "Date de création" (avant :
# aucun nom standard connu, rôle optionnel détecté à l'aveugle par
# mots-clés, contrôle de délai silencieusement inactif tant qu'il n'était
# pas trouvé).
COLONNES_TEJ_STANDARD = {
    "fournisseur": "Raison social du bénéficiaire",
    "htva": "totalMontantHT",
    "rs": "totalMontantRS",
    "date_paiement": "Date de paiement",
    "statut": "État",
    "date_creation": "Date de création",
}

# Rôle non obligatoire : son absence ne bloque pas le chargement du fichier
# (voir ui/main_window.py::roles_obligatoires, qui ne le liste pas), mais
# il a désormais un nom standard confirmé comme les autres rôles ci-dessus.
ROLES_TEJ_OPTIONNELS = ["date_creation"]


def map_tej_columns(df: pd.DataFrame) -> dict:
    """
    Détermine le mapping des colonnes pour un fichier TEJ : colonnes fixes
    en priorité (COLONNES_TEJ_STANDARD), repli sur la détection par
    mots-clés pour tout rôle dont le nom exact ne correspond pas au fichier
    chargé.
    """
    mapping = {}
    roles_a_detecter = []

    for role, nom_colonne in COLONNES_TEJ_STANDARD.items():
        if nom_colonne in df.columns:
            mapping[role] = nom_colonne
        else:
            roles_a_detecter.append(role)

    if roles_a_detecter:
        # CHANGEMENT (correction bug) : la détection de repli est restreinte
        # aux colonnes pas déjà attribuées par un nom exact ci-dessus.
        # Avant, seule la détection de date_creation excluait ces colonnes ;
        # un rôle parmi les 5 historiques nécessitant un repli pouvait donc
        # reprendre une colonne déjà assignée en dur à un autre rôle (ex :
        # matcher un mot-clé sur "État", déjà pris par "statut").
        colonnes_libres = [c for c in df.columns if c not in mapping.values()]
        df_restant = df[colonnes_libres] if colonnes_libres else df.iloc[:, 0:0]
        mapping.update(detect_columns(df_restant, roles=roles_a_detecter))

    return mapping
```

### `core/rules.py`

*Règles métier centralisées : taux RS attendu, délai de paiement max.*

```python
from datetime import date

TOLERANCE_TAUX = 0.005  # marge unique pour absorber les arrondis de saisie

# CHANGEMENT : delai centralise ici (avant : 30 dans rules.py, mais 31 dans
# la logique dupliquee de matcher.py -- deux sources de verite differentes
# pour la meme regle metier).
DELAI_MAX_PAIEMENT_JOURS = 31

# CHANGEMENT (logique métier corrigée, 2e itération) : le taux n'est plus
# limité à Marchandise 1% / Loyer 10%. Le catalogue ci-dessous reprend les
# catégories officielles de retenue à la source fournies par l'utilisateur
# (chaque ligne : code, libellé, taux en fraction décimale). Il sert à
# peupler la liste déroulante de l'UI ; l'utilisateur garde la possibilité
# de saisir un taux personnalisé si sa catégorie n'y figure pas.
CATALOGUE_TAUX_RS = [
    ("4.1", "Honoraires Commission courtage & loyers (Résident)", 0.10),
    ("4.2", "Honoraires Commission courtage & loyers (non résident)", 0.15),
    ("4.3", "Honoraires Commission courtage & loyers (opérations d'export)", 0.03),
    ("5", "Honoraires PM & PP soumis au régime réel", 0.03),
    ("6", "Honoraires servis aux bureaux d'études exportateurs", 0.03),
    ("7", "Loyers d'hôtels", 0.05),
    ("8", "Intérêts des dépôts dans les comptes d'épargne privés", 0.20),
    ("9", "Revenus des autres capitaux mobiliers", 0.00),
    ("9.1", "Revenus des autres capitaux mobiliers aux résidents", 0.20),
    ("9.2", "Revenus des autres capitaux mobiliers aux non résidents", 0.20),
    ("10", "Revenus VM servis aux non résidents", 0.15),
    ("11", "Revenus des actions", 0.10),
    ("11.1", "Revenus des actions (PP résidents)", 0.10),
    ("11.2", "Revenus des actions (PP et PM non résidents)", 0.10),
    ("12.1", "Jeton de présence (résident)", 0.20),
    ("12.2", "Jeton de présence (non résident)", 0.20),
    ("13", "Rémunérations payées aux salariés", 0.15),
    ("14", "Intérêts des prêts aux banques", 0.03),
    ("15.1", "Prix de cession déclaré (Non Résident PP)", 0.03),
    ("15.2", "Prix de cession déclaré (Non Résident PM)", 0.03),
    ("15.3", "Prix de cession déclaré (biens fonciers)", 0.15),
    ("16.1", "RS/Marchés (IS 10%)", 0.01),
    ("16.2", "RS/Marchés (IS 20%)", 0.01),
    ("16.3", "Autres RS/Marchés", 0.02),
    ("17", "RS/TVA 25%", 0.25),
    ("18", "RS/TVA 100%", 1.00),
    ("19", "Honoraires trav constructions", 0.00),
    ("20", "Avance facturée aux PM à l'IR (régime forfaitaire)", 0.01),
    ("21.1", "Plus value de cession des actions PM", 0.03),
    ("21.2", "Plus value de cession des actions PP", 0.05),
    ("22", "Autres revenus (résidents paradis fiscaux)", 0.00),
    ("23", "Rémunérations/paradis fiscaux", 0.25),
    ("24", "Commissions/distributeur opérateurs de communications", 0.02),
]


def calculer_taux(htva: float, rs: float):
    """
    Calcule le taux RS effectif (RS / HTVA) côté TEJ. Retourne None si le
    calcul est impossible (HTVA manquant, nul, ou négatif).

    CHANGEMENT (logique métier corrigée, 2e itération) : remplace
    check_rs_rate(), qui classait un taux en seulement 2 catégories fixes
    (Marchandise 1% / Loyer 10%). Le taux cible est maintenant choisi
    librement par l'utilisateur (liste déroulante CATALOGUE_TAUX_RS ou
    saisie libre) — voir check_taux_conforme() ci-dessous pour la
    comparaison.
    """
    if htva is None or rs is None or htva <= 0:
        return None
    return rs / htva


def check_taux_conforme(htva: float, rs: float, taux_cible: float):
    """
    Vérifie si le taux RS effectif (RS/HTVA) correspond au taux cible
    choisi par l'utilisateur, à TOLERANCE_TAUX près.

    Retourne (conforme: bool | None, taux_calcule: float | None).
    conforme est None si le taux n'a pas pu être calculé (montant invalide).
    """
    taux = calculer_taux(htva, rs)
    if taux is None:
        return None, None
    return abs(taux - taux_cible) <= TOLERANCE_TAUX, taux


def check_payment_delay(date_creation: date, date_paiement: date):
    """
    Vérifie si le paiement a dépassé le délai maximum après la création.

    Retourne (is_valid, delay_jours).

    CHANGEMENT : si une des deux dates manque, retourne (None, None) au
    lieu de (True, 0). Avant, "impossible à vérifier" était confondu avec
    "conforme" — un vrai retard avec une date manquante aurait été marqué
    silencieusement comme sans risque. Avec None explicite, l'appelant peut
    afficher "date manquante, non vérifiable" plutôt que "conforme".
    """
    if not date_creation or not date_paiement:
        return None, None

    delay = (date_paiement - date_creation).days
    is_valid = delay <= DELAI_MAX_PAIEMENT_JOURS
    return is_valid, delay
```

### `core/matcher.py`

*Cœur du rapprochement : matching DS Comptable <-> TEJ, classification des écarts.*

```python
"""
Rapprochement entre les opérations DS Comptable et TEJ, et classification
des écarts.

Cette fonction doit être appelée une fois pour les achats (df_ds filtré
sens="achat" + fichier TEJ achat) et une fois pour les ventes — jamais en
mélangeant les deux sens, la séparation achat/vente se fait avant l'appel.

CHANGEMENT (logique métier corrigée, 3e itération) :
- "Montant HTVA" retiré du rapport (plus utile désormais, DS ne le fournit
  plus et l'utilisateur n'en a pas besoin dans le résultat final).
- "Date Échéance" ajoutée à chaque ligne de résultat, pour que l'utilisateur
  puisse vérifier lui-même la date retenue côté DS.
- Diagnostic automatique sur les lignes ECART_ABSENT_TEJ (ds sans
  correspondance) : au lieu d'un message générique, on explique la raison
  la plus probable du blocage (montant introuvable / hors délai / taux
  différent / nom trop différent), en assouplissant successivement les
  critères pour trouver où ça bloque. Ajouté après un cas réel où TOUTES
  les lignes ressortaient absentes sans indice exploitable — la cause la
  plus fréquente est un taux cible mal choisi dans l'UI, désormais visible
  immédiatement dans le commentaire plutôt que d'avoir à deviner.

CHANGEMENT (décision métier confirmée sur données réelles, voir 4.12 à
4.14) : le "même mois" a été un temps retiré comme filtre éliminatoire sur
les candidats TEJ, puis restauré sous forme de fenêtre de délai — la Date
de paiement TEJ doit tomber entre le 1er jour du mois de la date DS et la
fin du mois SUIVANT (ex: DS = juillet 2026 -> TEJ accepté jusqu'au 31 août
2026), pas nécessairement le même mois calendaire exact. En complément, un
mois totalement absent d'un des deux fichiers est hors du périmètre de
comparaison : les lignes de l'AUTRE fichier tombant dans ce mois sont
ignorées avant le rapprochement plutôt que signalées à tort comme
"absentes" (voir reconcile_data).
"""

import calendar
from datetime import date

import pandas as pd
from rapidfuzz import fuzz

from core.normalizer import clean_text, clean_amount, clean_date
from core.rules import check_taux_conforme, check_payment_delay, TOLERANCE_TAUX

SEUIL_FOURNISSEUR = 75

# CHANGEMENT : si le 2e meilleur candidat est à moins de ECART_SCORE_AMBIGU
# points du meilleur, on refuse de choisir automatiquement et on marque la
# ligne comme ambiguë plutôt que de prendre un candidat au hasard.
ECART_SCORE_AMBIGU = 5


def _dans_delai_rapprochement(d_ds, d_tej) -> bool:
    """
    CHANGEMENT (décision métier confirmée) : remplace l'exigence stricte du
    "même mois" (ancienne fonction _meme_mois, supprimée car devenue
    inutilisée) par une fenêtre tolérant un délai de déclaration TEJ
    jusqu'à la fin du mois SUIVANT celui de la date DS — ex. une opération
    DS de juillet 2026 est acceptée avec une Date de paiement TEJ jusqu'au
    31 août 2026 inclus, mais pas au-delà (ni avant le 1er juillet).
    """
    if not d_ds or not d_tej:
        return False
    debut_fenetre = d_ds.replace(day=1)
    mois_suivant = d_ds.month % 12 + 1
    annee_mois_suivant = d_ds.year + (1 if d_ds.month == 12 else 0)
    dernier_jour = calendar.monthrange(annee_mois_suivant, mois_suivant)[1]
    fin_fenetre = date(annee_mois_suivant, mois_suivant, dernier_jour)
    return debut_fenetre <= d_tej <= fin_fenetre


def _est_annule(statut_brut) -> bool:
    """
    Détecte un statut "annulé" (accents/casse variables : "Annulée",
    "ANNULE", etc.) via le même nettoyage que pour les noms de fournisseur.
    """
    if statut_brut is None:
        return False
    return "ANNUL" in clean_text(str(statut_brut))


def _preparer_records_ds(df: pd.DataFrame, mapping: dict) -> tuple:
    """
    DS Comptable ne fournit plus que fournisseur + RS + date d'échéance
    (plus de HTVA ni de date de création côté DS, voir docstring du module).

    CHANGEMENT (logique métier corrigée, 4e itération) : une ligne au
    montant RS illisible est désormais complètement écartée ici (comptée,
    pas ajoutée à la liste de records) plutôt que gardée avec un flag
    montant_invalide traité plus loin — cohérent avec le fait qu'elle ne
    doit plus apparaître dans le rapport final du tout.
    """
    records = []
    nb_invalides = 0
    for idx, row in df.iterrows():
        rs = clean_amount(row.get(mapping["rs"]))
        if rs is None:
            nb_invalides += 1
            continue
        records.append({
            "orig_idx": idx,
            "raw_name": row.get(mapping["fournisseur"]),
            "clean_name": clean_text(str(row.get(mapping["fournisseur"]))),
            "rs": rs,
            "date_paiement": clean_date(row.get(mapping["date_paiement"])),
            "matched": False,
        })
    return records, nb_invalides


def _preparer_records_tej(df: pd.DataFrame, mapping: dict) -> tuple:
    """
    Côté TEJ, htva/rs/date_paiement/statut restent nécessaires ; la date de
    création (date de saisie) est optionnelle — utilisée seulement si
    core/parser.py::map_tej_columns() l'a trouvée (voir ROLES_TEJ_OPTIONNELS).

    CHANGEMENT (logique métier corrigée, 4e itération) : les lignes au
    statut "annulé" ET celles au montant illisible sont maintenant
    complètement écartées ici (comptées, pas gardées avec un flag) — plus
    aucune des deux ne doit apparaître dans le rapport final.
    """
    records = []
    nb_annulees = 0
    nb_invalides = 0
    col_date_creation = mapping.get("date_creation")

    for idx, row in df.iterrows():
        if _est_annule(row.get(mapping["statut"])):
            nb_annulees += 1
            continue

        htva = clean_amount(row.get(mapping["htva"]))
        rs = clean_amount(row.get(mapping["rs"]))
        if htva is None or rs is None:
            nb_invalides += 1
            continue

        records.append({
            "orig_idx": idx,
            "raw_name": row.get(mapping["fournisseur"]),
            "clean_name": clean_text(str(row.get(mapping["fournisseur"]))),
            "htva": htva,
            "rs": rs,
            "date_paiement": clean_date(row.get(mapping["date_paiement"])),
            "date_creation": clean_date(row.get(col_date_creation)) if col_date_creation else None,
            "matched": False,
        })
    return records, nb_annulees, nb_invalides


def reconcile_data(df_ds: pd.DataFrame, map_ds: dict, df_tej: pd.DataFrame, map_tej: dict,
                    taux_cible: float) -> tuple:
    """
    Rapproche les lignes entre DS Comptable et TEJ.

    :param taux_cible: taux RS attendu (fraction décimale, ex: 0.01 pour
        1%) pour l'ensemble du fichier DS Comptable chargé — choisi par
        l'utilisateur (liste déroulante core.rules.CATALOGUE_TAUX_RS ou
        saisie libre), PAS déduit automatiquement.

    CHANGEMENT (logique métier corrigée, 4e itération) : les lignes
    réellement exclues du rapprochement (montant RS illisible côté DS,
    opérations "annulé" côté TEJ) ne figurent plus dans le rapport final —
    elles n'apportent rien à un rapport destiné au client, l'important est
    de les compter. Retourne maintenant un tuple (results, stats) au lieu
    d'une simple liste ; stats contient ces compteurs pour affichage dans
    l'UI (message de fin d'analyse).

    CHANGEMENT (décision métier confirmée sur données réelles, voir 4.12 à
    4.14) : le "même mois" a été un temps retiré comme filtre éliminatoire
    sur les candidats TEJ (des correspondances montant+nom existaient avec
    des mois d'écart, parfois plus d'un an), puis restauré sous forme de
    fenêtre de délai — un écart de plus d'un mois-suivant n'est pas une
    vraie correspondance, même à montant et nom identiques. En complément,
    un mois totalement absent d'un des deux fichiers est désormais hors du
    périmètre de comparaison (voir ci-dessous).
    """
    if not isinstance(taux_cible, (int, float)) or taux_cible < 0:
        raise ValueError(f"taux_cible invalide : {taux_cible!r}. Attendu un nombre positif (ex: 0.01 pour 1%).")

    results = []
    ds_records, nb_ds_montant_illisible = _preparer_records_ds(df_ds, map_ds)
    tej_records, nb_tej_annulees, nb_tej_montant_illisible = _preparer_records_tej(df_tej, map_tej)

    # CHANGEMENT (décision métier confirmée) : un mois totalement absent
    # d'un des deux fichiers (aucune opération ce mois-là, ex. un fichier
    # TEJ historique qui déborde largement de la période couverte par le
    # fichier DS chargé) est hors du périmètre de comparaison — les lignes
    # de l'AUTRE fichier tombant dans ce mois sont donc ignorées avant le
    # rapprochement, plutôt que signalées à tort comme "absentes". Une
    # ligne sans date exploitable (date_paiement=None) n'est jamais écartée
    # par cette règle, faute de mois à comparer.
    mois_ds = {(d.year, d.month) for r in ds_records if (d := r["date_paiement"])}
    mois_tej = {(d.year, d.month) for r in tej_records if (d := r["date_paiement"])}
    ds_records = [
        r for r in ds_records
        if r["date_paiement"] is None or (r["date_paiement"].year, r["date_paiement"].month) in mois_tej
    ]
    tej_records = [
        r for r in tej_records
        if r["date_paiement"] is None or (r["date_paiement"].year, r["date_paiement"].month) in mois_ds
    ]

    for ds in ds_records:
        # CHANGEMENT (décision métier confirmée, voir 4.17) : montant + délai
        # + taux d'abord, SANS filtrer sur le nom. Si un seul candidat TEJ
        # passe ces trois critères (aucune redondance possible), c'est une
        # correspondance directe même si le nom fournisseur ne franchit pas
        # le seuil de similarité (ex: "RS CMA" côté DS vs "COMPAGNIE MENUISERIE
        # ALUMINIUM CMA" côté TEJ — score de nom faible, mais aucune autre
        # ligne TEJ ne partage ce montant sur cette période, donc pas
        # d'ambiguïté possible). Le nom ne sert de filtre/désambiguïsateur
        # que s'il y a plusieurs candidats à montant+délai+taux identiques
        # (redondance réelle, voir plus bas).
        candidats_stricts = []
        for tej in tej_records:
            if tej["matched"]:
                continue

            # Égalité stricte sur le montant RS (le seul montant comparé
            # côté DS désormais — voir docstring du module)
            if ds["rs"] != tej["rs"]:
                continue

            # CHANGEMENT (décision métier confirmée, voir 4.14) : "même
            # mois" strict remplacé par une fenêtre de délai — la Date de
            # paiement TEJ est acceptée du 1er jour du mois DS jusqu'à la
            # fin du mois SUIVANT (ex: DS = juillet 2026 -> TEJ accepté
            # jusqu'au 31 août 2026). Un écart plus grand n'est pas
            # considéré comme un rapprochement, même à montant et nom
            # identiques.
            if not _dans_delai_rapprochement(ds["date_paiement"], tej["date_paiement"]):
                continue

            # Filtre strict par taux cible — seules les lignes TEJ dont
            # RS/HTVA correspond exactement (à la tolérance près) au taux
            # choisi sont candidates.
            conforme, _ = check_taux_conforme(tej["htva"], tej["rs"], taux_cible)
            if not conforme:
                continue

            score = fuzz.token_sort_ratio(ds["clean_name"], tej["clean_name"])
            candidats_stricts.append((score, tej))

        if not candidats_stricts:
            results.append(_ligne_absente_ds(ds, tej_records, taux_cible))
            continue

        if len(candidats_stricts) == 1:
            # Montant + délai + taux uniques : correspondance directe, sans
            # exiger le seuil de similarité de nom (voir commentaire ci-dessus).
            score, tej_unique = candidats_stricts[0]
            ds["matched"] = True
            tej_unique["matched"] = True
            results.append(_ligne_rapprochee(ds, tej_unique, score, taux_cible))
            continue

        # Redondance réelle : plusieurs lignes TEJ partagent montant + délai
        # + taux — le nom fournisseur sert ici à désambiguïser, seuil
        # SEUIL_FOURNISSEUR appliqué comme avant.
        candidats = [c for c in candidats_stricts if c[0] >= SEUIL_FOURNISSEUR]
        if not candidats:
            results.append(_ligne_absente_ds(ds, tej_records, taux_cible))
            continue

        candidats.sort(key=lambda c: c[0], reverse=True)
        meilleur_score, meilleur_tej = candidats[0]

        if len(candidats) > 1 and (meilleur_score - candidats[1][0]) < ECART_SCORE_AMBIGU:
            for _, tej_candidat in candidats:
                tej_candidat["matched"] = True
            results.append(_ligne_ambigue(ds, [c[1] for c in candidats]))
            continue

        ds["matched"] = True
        meilleur_tej["matched"] = True
        results.append(_ligne_rapprochee(ds, meilleur_tej, meilleur_score, taux_cible))

    for tej in tej_records:
        if not tej["matched"]:
            results.append(_ligne_absente_tej(tej))

    stats = {
        "lignes_ds_montant_illisible": nb_ds_montant_illisible,
        "lignes_tej_annulees": nb_tej_annulees,
        "lignes_tej_montant_illisible": nb_tej_montant_illisible,
    }
    return results, stats


def _diagnostiquer_absence(ds: dict, tej_records: list, taux_cible: float) -> str:
    """
    CHANGEMENT (logique métier corrigée, 3e itération) : au lieu du message
    générique "introuvable côté TEJ", on assouplit successivement les
    critères (montant -> taux -> nom) pour indiquer PRÉCISÉMENT à quel
    niveau le rapprochement a échoué, afin que l'utilisateur puisse
    diagnostiquer lui-même un taux cible mal choisi, un nom trop différent,
    etc. sans avoir à explorer les fichiers manuellement.

    CHANGEMENT (décision métier confirmée sur données réelles, voir 4.12,
    4.13, 4.14) : l'étape "mois" a été un temps retirée de cet
    assouplissement, puis restaurée sous forme de fenêtre de délai — la
    Date de paiement TEJ doit tomber entre le 1er jour du mois DS et la fin
    du mois suivant (voir _dans_delai_rapprochement), pas nécessairement le
    même mois calendaire exact.
    """
    memes_montant = [t for t in tej_records if t["rs"] == ds["rs"]]
    if not memes_montant:
        return "aucune ligne TEJ avec le même montant RS"

    memes_mois = [t for t in memes_montant if _dans_delai_rapprochement(ds["date_paiement"], t["date_paiement"])]
    if not memes_mois:
        echeances = sorted({
            f"{t['date_paiement'].month:02d}/{t['date_paiement'].year}"
            for t in memes_montant if t["date_paiement"]
        })
        detail = ", ".join(echeances) if echeances else "date TEJ manquante"
        return f"montant RS identique côté TEJ mais hors du délai accepté (mois trouvé(s) côté TEJ : {detail})"

    bon_taux = [t for t in memes_mois if check_taux_conforme(t["htva"], t["rs"], taux_cible)[0]]
    if not bon_taux:
        taux_calcules = sorted({
            f"{(t['rs'] / t['htva'] * 100):.2f}%"
            for t in memes_mois if t["htva"]
        })
        detail = ", ".join(taux_calcules) if taux_calcules else "HTVA manquant côté TEJ"
        return (f"montant RS et date correspondent, mais le taux calculé côté TEJ "
                f"({detail}) diffère du taux sélectionné ({taux_cible * 100:.2f}%) — "
                f"vérifiez le taux choisi dans la liste déroulante")

    meilleur = max(bon_taux, key=lambda t: fuzz.token_sort_ratio(ds["clean_name"], t["clean_name"]))
    score = fuzz.token_sort_ratio(ds["clean_name"], meilleur["clean_name"])
    deja_pris = " (déjà rapprochée avec une autre ligne)" if meilleur["matched"] else ""
    return (f"montant, date et taux correspondent avec « {meilleur['raw_name']} »{deja_pris} "
            f"mais le nom est trop différent (score {score:.0f}%, seuil {SEUIL_FOURNISSEUR}%)")


def _ligne_rapprochee(ds: dict, tej: dict, score_nom: float, taux_cible: float) -> dict:
    """
    Le contrôle de délai de paiement ne se fait plus que côté TEJ (date de
    saisie vs date d'échéance) — DS Comptable n'a plus de date de création.
    Silencieux si TEJ n'a pas de date de saisie détectée (date_creation
    vaut None).
    """
    commentaires = []
    statut = "CONFORME"

    # CHANGEMENT (décision métier confirmée, voir 4.17) : une ligne peut
    # arriver ici avec un score de nom sous SEUIL_FOURNISSEUR — acceptée
    # car seule candidate à montant + délai + taux identiques (aucune
    # redondance possible, voir reconcile_data). Signalé en commentaire
    # pour que le comptable comprenne pourquoi le score affiché est bas.
    if score_nom < SEUIL_FOURNISSEUR:
        statut = "AVERTISSEMENT"
        commentaires.append(
            f"Nom fournisseur peu similaire (score {score_nom:.0f}%) mais accepté : "
            f"seule ligne TEJ avec ce montant sur cette période, pas d'ambiguïté possible"
        )

    valide_tej, delai_tej = check_payment_delay(tej["date_creation"], tej["date_paiement"])
    if valide_tej is False:
        statut = "AVERTISSEMENT"
        commentaires.append(f"Risque de pénalité côté TEJ (paiement à {delai_tej} jours)")

    return {
        "Statut": statut,
        "Fournisseur DS": ds["raw_name"],
        "Fournisseur TEJ": tej["raw_name"],
        "Score nom (%)": round(score_nom, 1),
        "Date Échéance": ds["date_paiement"],
        "Montant RS": ds["rs"],
        "Type retenue": f"{taux_cible * 100:.2f}%",
        "Commentaire": " | ".join(commentaires) if commentaires else "Match conforme",
    }


def _ligne_absente_ds(ds: dict, tej_records: list, taux_cible: float) -> dict:
    """Ligne DS sans correspondance TEJ, avec diagnostic détaillé (voir _diagnostiquer_absence)."""
    return {
        "Statut": "ECART_ABSENT_TEJ",
        "Fournisseur DS": ds["raw_name"],
        "Fournisseur TEJ": "-",
        "Score nom (%)": None,
        "Date Échéance": ds["date_paiement"],
        "Montant RS": ds["rs"],
        "Type retenue": None,
        "Commentaire": f"Introuvable côté TEJ : {_diagnostiquer_absence(ds, tej_records, taux_cible)}",
    }


def _ligne_absente_tej(tej: dict) -> dict:
    """Ligne TEJ (du bon taux, non annulée) sans correspondance DS."""
    return {
        "Statut": "ECART_ABSENT_DS_COMPTABLE",
        "Fournisseur DS": "-",
        "Fournisseur TEJ": tej["raw_name"],
        "Score nom (%)": None,
        "Date Échéance": tej["date_paiement"],
        "Montant RS": tej["rs"],
        "Type retenue": None,
        "Commentaire": "Opération présente côté TEJ, introuvable côté ds comptable",
    }


def _ligne_ambigue(ds: dict, candidats_tej: list) -> dict:
    noms = ", ".join(str(c["raw_name"]) for c in candidats_tej)
    return {
        "Statut": "AMBIGU",
        "Fournisseur DS": ds["raw_name"],
        "Fournisseur TEJ": noms,
        "Score nom (%)": None,
        "Date Échéance": ds["date_paiement"],
        "Montant RS": ds["rs"],
        "Type retenue": None,
        "Commentaire": f"{len(candidats_tej)} candidats TEJ valides trouvés, vérification manuelle nécessaire",
    }
```

### `ui/__init__.py`

*Marqueur de package (vide).*

```python

```

### `ui/styles.py`

*Feuille de style Qt (QSS) globale de l'application.*

```python
QSS_THEME = """
QMainWindow {
    background-color: #F4F6F9;
}

QLabel {
    color: #2C3E50;
    font-family: 'Segoe UI', 'Calibri', sans-serif;
}

QTableWidget {
    background-color: white;
    gridline-color: #E2E8F0;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
}

QHeaderView::section {
    background-color: #1F4E79;
    color: white;
    padding: 6px;
    font-weight: bold;
    border: none;
}
"""
```

### `ui/mapping_dialog.py`

*Boîte de dialogue de confirmation/correction manuelle du mapping de colonnes.*

```python
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QComboBox, QPushButton, QFormLayout, QDialogButtonBox

# CHANGEMENT (logique métier corrigée, 2e itération) : DS Comptable et TEJ
# n'ont plus les mêmes rôles à mapper (DS: 3 rôles, TEJ: jusqu'à 6). Ce
# dialogue construit maintenant ses champs à partir des clés réellement
# présentes dans detected_mapping, au lieu d'une liste fixe des 5 rôles
# historiques qui ne correspond plus à aucun des deux fichiers.
ROLE_LABELS = {
    "fournisseur": "Nom Tiers / Fournisseur",
    "htva": "Montant HTVA",
    "rs": "Montant RS",
    "date_creation": "Date de Saisie / Écriture",
    "date_paiement": "Date d'Échéance / Paiement",
    "statut": "Statut de l'opération",
}


class MappingDialog(QDialog):
    """Pop-up permettant à l'utilisateur de valider ou corriger le mapping automatique des colonnes."""
    
    def __init__(self, df_columns: list[str], detected_mapping: dict, file_label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Validation des Colonnes - {file_label}")
        self.resize(420, 320)
        
        self.combos = {}
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel(f"Veuillez vérifier l'identification des colonnes pour :\n<b>{file_label}</b>"))
        
        form_layout = QFormLayout()

        for role in detected_mapping.keys():
            label_text = ROLE_LABELS.get(role, role.replace("_", " ").title())
            combo = QComboBox()
            combo.addItems(["-- Ignorer / Non présent --"] + df_columns)
            
            # Pré-sélection
            current_detected = detected_mapping.get(role)
            if current_detected in df_columns:
                combo.setCurrentText(current_detected)
                
            self.combos[role] = combo
            form_layout.addRow(label_text, combo)
            
        layout.addLayout(form_layout)
        
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
    def get_validated_mapping(self) -> dict:
        validated = {}
        for role, combo in self.combos.items():
            selected = combo.currentText()
            validated[role] = None if selected.startswith("--") else selected
        return validated
```

### `ui/main_window.py`

*Fenêtre principale : zones de dépôt de fichiers, lancement de l'analyse, tableau de résultats, export.*

```python
import os
import sys
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QPushButton, QFileDialog, QRadioButton, QButtonGroup, 
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, 
    QFrame, QProgressBar, QDialog, QComboBox
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QFont, QIcon

# Import des modules du cœur logique (Core & Utils)
from core.parser import load_excel, detect_columns, map_tej_columns, ROLES_DS
from core.matcher import reconcile_data
from core.rules import CATALOGUE_TAUX_RS
from utils.exporter import export_reconciliation_report
from ui.mapping_dialog import MappingDialog

# CHANGEMENT (logique métier corrigée, 4e itération) : libellés lisibles en
# français pour l'affichage — les statuts internes (ECART_ABSENT_TEJ, etc.)
# restent utilisés pour la logique (couleurs, export), mais n'étaient pas
# clairs à lire tels quels dans le tableau.
STATUT_LABELS = {
    "CONFORME": "✓ Conforme",
    "AVERTISSEMENT": "⚠ Avertissement",
    "AMBIGU": "? Ambigu — à vérifier",
    "ECART_ABSENT_TEJ": "✗ Absent côté TEJ",
    "ECART_ABSENT_DS_COMPTABLE": "✗ Absent côté DS Comptable",
}


class FileDropZone(QFrame):
    """Zone de glisser-déposer stylisée pour charger un fichier Excel."""
    
    def __init__(self, title: str):
        super().__init__()
        self.setAcceptDrops(True)
        self.file_path = None

        self.setStyleSheet("""
            QFrame {
                border: 2px dashed #A0AAB5;
                border-radius: 8px;
                background-color: #F8F9FA;
            }
            QFrame:hover {
                border-color: #1F4E79;
                background-color: #F0F4F8;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.label_title = QLabel(title)
        self.label_title.setFont(QFont("Calibri", 11, QFont.Weight.Bold))
        self.label_title.setStyleSheet("color: #1F4E79; border: none; background: transparent;")

        self.label_status = QLabel("Glissez-déposez un fichier .xlsx ici\nou cliquez pour parcourir")
        self.label_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_status.setFont(QFont("Calibri", 9))
        self.label_status.setStyleSheet("color: #6C757D; border: none; background: transparent;")

        self.btn_browse = QPushButton("Parcourir...")
        self.btn_browse.setStyleSheet("""
            QPushButton {
                background-color: #1F4E79;
                color: white;
                border: none;
                padding: 6px 14px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #153451;
            }
        """)
        self.btn_browse.clicked.connect(self.browse_file)

        layout.addWidget(self.label_title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label_status, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.btn_browse, alignment=Qt.AlignmentFlag.AlignCenter)

    def browse_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Sélectionner un fichier Excel", "", "Fichiers Excel (*.xlsx)")
        if file_path:
            self.set_file(file_path)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if file_path.endswith('.xlsx'):
                self.set_file(file_path)
                break

    def set_file(self, path: str):
        self.file_path = path
        filename = os.path.basename(path)
        self.label_status.setText(f"✓ Fichier chargé :\n{filename}")
        self.label_status.setStyleSheet("color: #28A745; font-weight: bold; border: none; background: transparent;")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Contrôle des Retenues à la Source - ABC Audit")
        self.resize(1100, 700)
        
        self.results_cache = []

        # Widget central et disposition principale
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(15)

        # 1. En-tête / Titre
        header_label = QLabel("Plateforme de Contrôle & Rapprochement des Retenues à la Source (RS)")
        header_label.setFont(QFont("Calibri", 14, QFont.Weight.Bold))
        header_label.setStyleSheet("color: #1F4E79; margin-bottom: 5px;")
        main_layout.addWidget(header_label)

        # 2. Choix du type d'opération (Achat vs Vente)
        type_box = QHBoxLayout()
        type_label = QLabel("Type d'opération à analyser :")
        type_label.setFont(QFont("Calibri", 10, QFont.Weight.Bold))
        
        self.radio_achat = QRadioButton("Achats (Émis - Payés mensuellement)")
        self.radio_vente = QRadioButton("Ventes (Reçus - Déduits en fin d'année)")
        self.radio_achat.setChecked(True)

        self.type_group = QButtonGroup()
        self.type_group.addButton(self.radio_achat)
        self.type_group.addButton(self.radio_vente)

        type_box.addWidget(type_label)
        type_box.addWidget(self.radio_achat)
        type_box.addWidget(self.radio_vente)
        type_box.addStretch()
        main_layout.addLayout(type_box)

        # 2bis. Taux de retenue attendu pour le fichier DS Comptable chargé.
        # CHANGEMENT (logique métier corrigée, 2e itération) : remplace les
        # deux boutons radio Marchandise/Loyer par une liste déroulante
        # couvrant toutes les catégories officielles de RS
        # (core/rules.py::CATALOGUE_TAUX_RS), avec possibilité de taper un
        # taux personnalisé si la catégorie n'y figure pas. DS Comptable
        # sépare ces écritures par compte : un fichier chargé est
        # entièrement d'un seul taux, choisi ici par l'utilisateur — il
        # n'est plus déduit automatiquement (voir core/matcher.py).
        taux_box = QHBoxLayout()
        taux_label = QLabel("Taux de retenue du fichier DS Comptable :")
        taux_label.setFont(QFont("Calibri", 10, QFont.Weight.Bold))

        self.combo_taux = QComboBox()
        self.combo_taux.setEditable(True)
        self.combo_taux.setMinimumWidth(400)
        for code, libelle, taux in CATALOGUE_TAUX_RS:
            self.combo_taux.addItem(f"{code} — {libelle} ({taux * 100:.2f}%)", taux)
        self.combo_taux.setToolTip(
            "Choisissez une catégorie dans la liste, ou tapez directement un "
            "taux (ex: 7.5 ou 7.5%) si votre catégorie n'y figure pas."
        )

        taux_box.addWidget(taux_label)
        taux_box.addWidget(self.combo_taux, stretch=1)
        main_layout.addLayout(taux_box)

        # 3. Zone de dépôt des fichiers
        files_layout = QHBoxLayout()
        self.drop_ds = FileDropZone("1. Fichier DS Comptable")
        self.drop_tej = FileDropZone("2. Fichier Plateforme TEJ")
        files_layout.addWidget(self.drop_ds)
        files_layout.addWidget(self.drop_tej)
        main_layout.addLayout(files_layout)

        # 4. Boutons d'action
        actions_layout = QHBoxLayout()
        self.btn_analyze = QPushButton("⚡ Lancer le Rapprochement")
        self.btn_analyze.setFont(QFont("Calibri", 11, QFont.Weight.Bold))
        self.btn_analyze.setStyleSheet("""
            QPushButton {
                background-color: #28A745;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 5px;
            }
            QPushButton:hover { background-color: #218838; }
        """)
        self.btn_analyze.clicked.connect(self.run_reconciliation)

        self.btn_export = QPushButton("📥 Exporter le Rapport Excel")
        self.btn_export.setEnabled(False)
        self.btn_export.setFont(QFont("Calibri", 11, QFont.Weight.Bold))
        self.btn_export.setStyleSheet("""
            QPushButton {
                background-color: #17A2B8;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 5px;
            }
            QPushButton:hover { background-color: #138496; }
            QPushButton:disabled { background-color: #6C757D; opacity: 0.5; }
        """)
        self.btn_export.clicked.connect(self.export_results)

        actions_layout.addWidget(self.btn_analyze)
        actions_layout.addWidget(self.btn_export)
        main_layout.addLayout(actions_layout)

        # 5. Tableau des résultats prévisualisés
        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(6)
        self.table_widget.setHorizontalHeaderLabels([
            "Statut", "Fournisseur DS", "Fournisseur TEJ", "Date Échéance", "RS (DT)", "Commentaire"
        ])
        self.table_widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        main_layout.addWidget(self.table_widget)

    def _lire_taux_cible(self) -> float:
        """
        Retourne le taux choisi (fraction décimale). Si l'utilisateur a
        sélectionné une entrée de la liste, on récupère la valeur exacte
        stockée avec elle (currentData) ; sinon on parse le texte tapé
        (ex: "7.5", "7,5", "7.5%").
        """
        data = self.combo_taux.currentData()
        if data is not None and self.combo_taux.currentText() == self.combo_taux.itemText(self.combo_taux.currentIndex()):
            return float(data)

        texte = self.combo_taux.currentText().strip().replace('%', '').replace(',', '.')
        try:
            valeur = float(texte)
        except ValueError:
            raise ValueError(f"Taux invalide : « {self.combo_taux.currentText()} ». Tapez un nombre, ex: 7.5 ou 7.5%.")
        return valeur / 100 if valeur > 1 else valeur

    def _load_ds(self, path: str):
        """
        Charge le fichier DS Comptable : seuls 3 rôles sont nécessaires
        (fournisseur, RS, échéance — voir core/parser.py::ROLES_DS), avec
        repli sur MappingDialog si la détection automatique échoue, car les
        intitulés de colonnes DS Comptable varient d'une société à l'autre.
        """
        df = load_excel(path)
        mapping = detect_columns(df, roles=ROLES_DS)
        manquants = [role for role, col in mapping.items() if col is None]

        if manquants:
            dialog = MappingDialog(list(df.columns), mapping, "DS Comptable", self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                raise ValueError("Mapping annulé par l'utilisateur pour le fichier DS Comptable.")
            mapping = dialog.get_validated_mapping()
            toujours_manquants = [role for role, col in mapping.items() if col is None]
            if toujours_manquants:
                raise ValueError(
                    f"Colonnes toujours non renseignées pour DS Comptable : {toujours_manquants}."
                )

        return df, mapping

    def _load_tej(self, path: str):
        """
        Charge le fichier TEJ : colonnes fixes (format standard identique
        pour toutes les sociétés, voir core/parser.py::COLONNES_TEJ_STANDARD),
        avec repli automatique par mots-clés si un intitulé ne correspond
        pas exactement, et MappingDialog en dernier recours si un rôle
        obligatoire reste introuvable (date_creation reste optionnelle).
        """
        df = load_excel(path)
        mapping = map_tej_columns(df)

        roles_obligatoires = ["fournisseur", "htva", "rs", "date_paiement", "statut"]
        manquants = [role for role in roles_obligatoires if mapping.get(role) is None]

        if manquants:
            dialog = MappingDialog(list(df.columns), mapping, "TEJ", self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                raise ValueError("Mapping annulé par l'utilisateur pour le fichier TEJ.")
            mapping = dialog.get_validated_mapping()
            toujours_manquants = [role for role in roles_obligatoires if mapping.get(role) is None]
            if toujours_manquants:
                raise ValueError(f"Colonnes toujours non renseignées pour TEJ : {toujours_manquants}.")

        return df, mapping

    def run_reconciliation(self):
        """Lance l'analyse automatique."""
        path_ds = self.drop_ds.file_path
        path_tej = self.drop_tej.file_path

        if not path_ds or not path_tej:
            QMessageBox.warning(self, "Avertissement", "Veuillez charger les DEUX fichiers Excel avant de lancer l'analyse.")
            return

        try:
            taux_cible = self._lire_taux_cible()

            # 1. Chargement, détection des colonnes et mapping manuel si besoin
            df_ds, map_ds = self._load_ds(path_ds)
            df_tej, map_tej = self._load_tej(path_tej)

            # 2. Exécution du rapprochement
            # CHANGEMENT (décision métier confirmée) : stats (lignes exclues
            # pour montant illisible / statut annulé) n'est plus affiché
            # dans la notification de fin d'analyse — ce sont des exclusions
            # attendues et normales (écriture au débit sans montant RS,
            # opération annulée), pas des anomalies à signaler à chaque
            # analyse. Les lignes restent bien exclues du rapport comme
            # avant, seul l'affichage du décompte dans la popup est retiré.
            self.results_cache, stats = reconcile_data(df_ds, map_ds, df_tej, map_tej, taux_cible)

            # 3. Affichage dans le tableau
            self.display_results(self.results_cache)
            self.btn_export.setEnabled(True)

            QMessageBox.information(
                self,
                "Analyse Terminée",
                f"Le rapprochement est terminé avec succès !\nTotal opérations dans le rapport : {len(self.results_cache)}"
            )

        except Exception as e:
            QMessageBox.critical(self, "Erreur lors de l'analyse", f"Une erreur s'est produite :\n{str(e)}")

    def display_results(self, results: list[dict]):
        """Rempli le tableau GUI avec des couleurs de statut."""
        self.table_widget.setRowCount(0)
        
        for row_idx, data in enumerate(results):
            self.table_widget.insertRow(row_idx)
            
            statut = data.get("Statut", "")
            
            # CHANGEMENT (logique métier corrigée, 4e itération) : AMBIGU
            # avait la même couleur rouge que les écarts d'absence, ce qui
            # les rendait indiscernables au premier coup d'œil. AMBIGU a
            # maintenant sa propre couleur (violet clair).
            if statut == "CONFORME":
                bg_color = QColor("#D4EDDA")  # Vert
            elif statut == "AVERTISSEMENT":
                bg_color = QColor("#FFF3CD")  # Orange
            elif statut == "AMBIGU":
                bg_color = QColor("#E2D9F3")  # Violet clair
            else:
                bg_color = QColor("#F8D7DA")  # Rouge (écarts d'absence)

            items = [
                QTableWidgetItem(STATUT_LABELS.get(statut, statut)),
                QTableWidgetItem(str(data.get("Fournisseur DS", "-"))),
                QTableWidgetItem(str(data.get("Fournisseur TEJ", "-"))),
                QTableWidgetItem(self._format_date(data.get('Date Échéance'))),
                QTableWidgetItem(self._format_montant(data.get('Montant RS'))),
                QTableWidgetItem(str(data.get("Commentaire", "")))
            ]

            for col_idx, item in enumerate(items):
                item.setBackground(bg_color)
                self.table_widget.setItem(row_idx, col_idx, item)

    @staticmethod
    def _format_date(valeur) -> str:
        if valeur is None:
            return "-"
        return valeur.strftime("%d/%m/%Y")

    @staticmethod
    def _format_montant(valeur) -> str:
        """CHANGEMENT : Montant RS peut être None pour une ligne d'erreur
        de montant — évite un TypeError sur le formatage numérique."""
        if valeur is None:
            return "-"
        return f"{valeur:,.3f}"

    def export_results(self):
        """Propose à l'utilisateur où sauvegarder le rapport Excel stylisé."""
        if not self.results_cache:
            return

        output_path, _ = QFileDialog.getSaveFileName(
            self, 
            "Enregistrer le rapport de contrôle", 
            "Rapport_Controle_RS.xlsx", 
            "Fichier Excel (*.xlsx)"
        )

        if output_path:
            try:
                export_reconciliation_report(self.results_cache, output_path)
                QMessageBox.information(self, "Succès", f"Rapport Excel généré avec succès :\n{output_path}")
            except Exception as e:
                QMessageBox.critical(self, "Erreur d'exportation", f"Impossible d'exporter le fichier :\n{str(e)}")
```

### `utils/__init__.py`

*Marqueur de package (vide).*

```python

```

### `utils/exporter.py`

*Génération du rapport Excel stylisé final.*

```python
import pandas as pd
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# CHANGEMENT (logique métier corrigée, 4e itération) : libellés lisibles,
# cohérents avec ui/main_window.py::STATUT_LABELS — le rapport exporté vers
# Excel doit rester lisible même sans l'interface.
STATUT_LABELS = {
    "CONFORME": "Conforme",
    "AVERTISSEMENT": "Avertissement",
    "AMBIGU": "Ambigu — à vérifier",
    "ECART_ABSENT_TEJ": "Absent côté TEJ",
    "ECART_ABSENT_DS_COMPTABLE": "Absent côté DS Comptable",
}

# Styles de couleurs (Palette Pastel Professionnelle)
FILL_CONFORME = PatternFill(start_color="D4EDDA", end_color="D4EDDA", fill_type="solid")     # Vert clair
FILL_AVERTISSEMENT = PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid") # Orange/Jaune clair
FILL_AMBIGU = PatternFill(start_color="E2D9F3", end_color="E2D9F3", fill_type="solid")        # Violet clair
FILL_ECART = PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")         # Rouge clair

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

def export_reconciliation_report(results: list[dict], output_file_path: str):
    """
    Exporte la liste des résultats de rapprochement vers un fichier Excel hautement stylisé.
    
    :param results: Liste de dictionnaires générée par matcher.reconcile_data()
    :param output_file_path: Chemin du fichier Excel de sortie (.xlsx)
    """
    if not results:
        raise ValueError("Aucun résultat à exporter.")

    # 1. Création du DataFrame (avec libellés de statut lisibles)
    df = pd.DataFrame(results)
    if "Statut" in df.columns:
        df["Statut"] = df["Statut"].map(lambda s: STATUT_LABELS.get(s, s))

    # Re-organisation/renommage des colonnes si besoin
    # CHANGEMENT (logique métier corrigée, 3e itération) : "Montant HTVA"
    # retiré (plus utile dans le rapport, demandé explicitement) ; "Date
    # Échéance" ajoutée pour que l'utilisateur puisse vérifier que le
    # filtrage par mois s'est fait correctement.
    columns_order = [
        "Statut",
        "Fournisseur DS",
        "Fournisseur TEJ",
        "Score nom (%)",
        "Date Échéance",
        "Montant RS",
        "Type retenue",
        "Commentaire"
    ]
    
    # S'assurer que toutes les colonnes existent
    df = df.reindex(columns=columns_order)

    # 2. Écriture avec Pandas et le moteur OpenPyXL
    with pd.ExcelWriter(output_file_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Rapport de Controle")
        
        # Récupération du classeur et de la feuille de calcul
        workbook = writer.book
        worksheet = writer.sheets["Rapport de Controle"]

        # 3. Style de l'En-tête (Ligne 1)
        for col_idx, col_name in enumerate(columns_order, start=1):
            cell = worksheet.cell(row=1, column=col_idx)
            cell.font = FONT_HEADER
            cell.fill = FILL_HEADER
            cell.alignment = ALIGN_CENTER
            cell.border = BORDER_THIN

        # 4. Stylisation des lignes de données (À partir de la ligne 2)
        for row_idx, row_data in enumerate(results, start=2):
            statut = row_data.get("Statut", "")

            # Sélection de la couleur de fond en fonction du statut
            if statut == "CONFORME":
                row_fill = FILL_CONFORME
            elif statut == "AVERTISSEMENT":
                row_fill = FILL_AVERTISSEMENT
            elif statut == "AMBIGU":
                row_fill = FILL_AMBIGU
            else:  # ECART_ABSENT_TEJ, ECART_ABSENT_DS_COMPTABLE
                row_fill = FILL_ECART

            # Application du style cellule par cellule dans la ligne
            for col_idx, col_name in enumerate(columns_order, start=1):
                cell = worksheet.cell(row=row_idx, column=col_idx)
                cell.font = FONT_BODY
                cell.fill = row_fill
                cell.border = BORDER_THIN

                # Alignement et formatage numérique spécifique
                if col_name == "Montant RS":
                    cell.number_format = '#,##0.000 "DT"'  # Format monétaire tunisien (3 décimales)
                    cell.alignment = ALIGN_RIGHT
                elif col_name == "Date Échéance":
                    cell.number_format = 'DD/MM/YYYY'
                    cell.alignment = ALIGN_CENTER
                elif col_name in ["Statut", "Type retenue", "Score nom (%)"]:
                    cell.alignment = ALIGN_CENTER
                else:
                    cell.alignment = ALIGN_LEFT

        # 5. Ajustement automatique de la largeur des colonnes
        for col in worksheet.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            
            for cell in col:
                # Calcul de la longueur du texte
                if cell.value is not None:
                    # Traitement des montants formotés pour ne pas couper
                    val_str = str(cell.value)
                    max_len = max(max_len, len(val_str))
            
            # Ajout d'une marge de sécurité
            worksheet.column_dimensions[col_letter].width = max(max_len + 5, 12)

        # Activer le quadrillage (Gridlines) explicitement
        worksheet.views.sheetView[0].showGridLines = True
```
