"""Boutons PRESQUE reconnus pendant les farms (captures de logs/presque/, ecrites par derive.near).

    python presque.py          # liste par image : combien de fois, meilleur score, derniere capture

Une image souvent "presque reconnue" est a refaire (assistant : outil « Refaire une image ») ou a passer en
fond masque (mask_bg) si elle est posee sur la ville.
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
DIR = BASE / "logs" / "presque"


def entries():
    try:
        lines = (DIR / "index.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for l in lines:
        try:
            e = json.loads(l)
        except ValueError:
            continue
        if (DIR / e.get("fichier", "")).exists():
            out.append(e)
    return out


def summary():
    """-> [(image, nombre, meilleur score, seuil, derniere capture)] du plus frequent au moins frequent."""
    by = {}
    for e in entries():
        n, best, thr, last = by.get(e["image"], (0, 0.0, e["seuil"], None))
        by[e["image"]] = (n + 1, max(best, e["score"]), e["seuil"], e["fichier"])
    return sorted(((k, *v) for k, v in by.items()), key=lambda x: (-x[1], -x[2]))


if __name__ == "__main__":
    rows = summary()
    if not rows:
        print("Aucun bouton presque reconnu pour l'instant (logs/presque/ se remplit pendant les farms).")
    for img, n, best, thr, last in rows:
        print(f"{img:<42} {n:>3} fois  meilleur {best:.2f} / seuil {thr:.2f}   derniere : logs/presque/{last}")
