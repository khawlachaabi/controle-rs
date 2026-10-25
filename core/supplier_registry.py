"""
Référentiel fournisseurs (Étape 4) : associe un nom canonique à ses variantes
(alias) connues, pour une résolution fiable des noms au-delà du seul fuzzy
matching (utile notamment pour des variantes non phonétiquement proches, ex.
transliterations arabes → latin — voir Documentation, "noms arabes").

Persisté en JSON local (pas de base de données lourde nécessaire pour ce
volume de données). Enrichi au fil des validations humaines (voir
ui/review_dialog.py) : quand l'utilisateur confirme que deux noms désignent
le même fournisseur, l'alias est ajouté ici et reconnu automatiquement lors
des rapprochements suivants.
"""

import json
import os
import shutil
import sys

from core.normalizer import clean_text

_PROJECT_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "supplier_registry.json"
)


def _chemin_registre_par_defaut() -> str:
    """
    En développement : supplier_registry.json à la racine du projet.

    En exécutable PyInstaller (sys.frozen) : le dossier du code est un
    répertoire temporaire (_MEIPASS) recréé puis supprimé à chaque lancement
    — y écrire ferait perdre tous les alias validés à la fermeture. Le
    référentiel est donc stocké dans %APPDATA%\\ControleRS (accessible en
    écriture même si l'exe est installé dans Program Files), initialisé au
    premier lancement avec le référentiel livré dans l'exe.
    """
    if not getattr(sys, "frozen", False):
        return _PROJECT_REGISTRY_PATH
    from utils.journal import dossier_donnees
    chemin = os.path.join(dossier_donnees(), "supplier_registry.json")
    initial = os.path.join(getattr(sys, "_MEIPASS", ""), "supplier_registry.json")
    if not os.path.exists(chemin) and os.path.exists(initial):
        shutil.copyfile(initial, chemin)
    return chemin


DEFAULT_REGISTRY_PATH = _chemin_registre_par_defaut()


class SupplierRegistry:
    def __init__(self, path: str = DEFAULT_REGISTRY_PATH):
        self.path = path
        self._alias_to_canonical: dict[str, str] = {}  # alias normalisé -> nom canonique
        self._canonical_aliases: dict[str, set] = {}  # nom canonique -> alias bruts connus
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for canonical, aliases in data.items():
            self._canonical_aliases[canonical] = set(aliases)
            for alias in aliases:
                self._alias_to_canonical[clean_text(alias)] = canonical
            self._alias_to_canonical[clean_text(canonical)] = canonical

    def save(self):
        data = {canonical: sorted(aliases) for canonical, aliases in self._canonical_aliases.items()}
        # Écriture atomique : un plantage en cours d'écriture ne doit jamais
        # laisser un JSON tronqué (illisible au prochain lancement).
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def resolve(self, raw_name: str) -> str | None:
        """Retourne le nom canonique si ce nom (nettoyé) est un alias validé connu, sinon None."""
        if not raw_name:
            return None
        return self._alias_to_canonical.get(clean_text(raw_name))

    def add_alias(self, canonical: str, alias: str, persist: bool = True):
        """
        Enregistre un alias validé pour un fournisseur canonique — appelé lors
        d'une validation humaine (ex: confirmation dans ui/review_dialog.py).
        """
        self._canonical_aliases.setdefault(canonical, set()).add(canonical)
        self._canonical_aliases[canonical].add(alias)
        self._alias_to_canonical[clean_text(alias)] = canonical
        self._alias_to_canonical[clean_text(canonical)] = canonical
        if persist:
            self.save()

    def canonical_or_self(self, raw_name: str) -> str:
        return self.resolve(raw_name) or raw_name

    def list_all(self) -> list[dict]:
        """
        Liste chaque variante validée, une ligne par (canonical, alias) —
        pour un affichage type tableau (voir décision métier confirmée,
        "validation fournisseur persistante et propagation automatique",
        §7 : consultation/contrôle humain du référentiel). Exclut la ligne
        où l'alias EST le nom canonique lui-même (bookkeeping interne, voir
        add_alias — pas une variante réellement validée par un humain).
        Triée par canonical puis alias pour un affichage stable.
        """
        lignes = []
        for canonical, aliases in self._canonical_aliases.items():
            for alias in aliases:
                if clean_text(alias) == clean_text(canonical):
                    continue
                lignes.append({"canonical": canonical, "alias": alias, "source": "Validation humaine"})
        lignes.sort(key=lambda l: (l["canonical"], l["alias"]))
        return lignes

    def remove_alias(self, canonical: str, alias: str, persist: bool = True) -> bool:
        """
        Retire UNE variante validée d'un fournisseur canonique ("Annuler la
        validation", voir §7) — ne supprime JAMAIS le fournisseur canonique
        lui-même ni ses autres alias : seule la relation (canonical, alias)
        précise est retirée. Refuse de retirer le nom canonique de son
        propre groupe (alias == canonical, bookkeeping interne). Retourne
        True si une suppression a effectivement eu lieu, False sinon
        (relation inconnue ou tentative de retirer le canonical lui-même).
        """
        if canonical not in self._canonical_aliases:
            return False
        if clean_text(alias) == clean_text(canonical):
            return False
        aliases = self._canonical_aliases[canonical]
        forme_exacte = next((a for a in aliases if clean_text(a) == clean_text(alias)), None)
        if forme_exacte is None:
            return False
        aliases.discard(forme_exacte)
        if self._alias_to_canonical.get(clean_text(forme_exacte)) == canonical:
            del self._alias_to_canonical[clean_text(forme_exacte)]
        if persist:
            self.save()
        return True
