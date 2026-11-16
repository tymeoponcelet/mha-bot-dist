"""Sauvegardes automatiques des reglages perso : logs/sauvegardes/<date>_<raison>/ (10 gardees).

    python sauvegarde.py              # liste
    python sauvegarde.py --now        # sauvegarde tout de suite

Fichiers : config.json, planning.json, profils.json, sequence.json. JAMAIS discord_config.json (il contient le token :
pas de copie ; configurer_discord.bat le recree).
Quand : une fois par jour (assistant / bot Discord), avant une mise a jour, avant une restauration.
"""
import os  # noqa: E402
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # memoire : sinon numpy reserve ~370 Mo par processus (16/11/2026 : « Insufficient memory » chez un ami)
import json
import shutil
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
FILES = ("config.json", "planning.json", "profils.json", "sequence.json")
KEEP = 10
REASONS = {"quotidienne": "quotidienne", "avant_maj": "avant mise à jour", "avant_restauration": "avant restauration",
           "manuelle": "manuelle"}


def folder(base=None):
    return Path(base or BASE) / "logs" / "sauvegardes"


def backup(reason="manuelle", base=None):
    """Copie les fichiers de reglages presents. -> dossier cree, ou None s'il n'y a rien a sauvegarder."""
    base = Path(base or BASE)
    present = [f for f in FILES if (base / f).exists()]
    if not present:
        return None
    now = time.time()                                  # millisecondes : ordre chronologique meme dans la meme seconde
    d = folder(base) / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}{int(now % 1 * 1000):03d}_{reason}"
    n = 2
    while d.exists():
        d = d.with_name(f"{d.name.split('~')[0]}~{n}")
        n += 1
    d.mkdir(parents=True)
    for f in present:
        shutil.copy2(base / f, d / f)
    import journal
    journal.rotate(folder(base), "2*", KEEP)
    return d


def listing(base=None):
    """-> [(dossier, date lisible, raison, fichiers)] du plus recent au plus ancien."""
    out = []
    for d in sorted(folder(base).glob("2*"), reverse=True) if folder(base).exists() else []:
        if not d.is_dir():
            continue
        stamp, _, reason = d.name.split("~")[0].partition("_")
        try:
            when = time.strftime("%d/%m %H:%M", time.strptime(stamp[:15], "%Y%m%d-%H%M%S"))
        except ValueError:
            when = stamp
        out.append((d, when, REASONS.get(reason, reason), sorted(p.name for p in d.iterdir() if p.name in FILES)))
    return out


def daily(base=None):
    """Sauvegarde quotidienne si la derniere a plus de 24 h. -> dossier cree ou None."""
    last = listing(base)
    if last and time.time() - last[0][0].stat().st_mtime < 86400:
        return None
    return backup("quotidienne", base)


def restore(d, base=None):
    """Remet les fichiers de la sauvegarde d (apres avoir sauvegarde l'etat actuel). -> fichiers remis."""
    base = Path(base or BASE)
    d = Path(d)
    if not str(d.resolve()).startswith(str(folder(base).resolve())):
        raise ValueError("sauvegarde inconnue")
    files = [p for p in d.iterdir() if p.name in FILES]
    if not files:
        raise ValueError("sauvegarde vide")
    data = {}
    for p in files:                                    # lu et verifie AVANT tout : la sauvegarde de securite ci-dessous
        raw = p.read_bytes()                           # peut faire tourner la rotation et effacer ce dossier
        try:
            json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ValueError(f"sauvegarde abîmée ({p.name}) : rien n'a été changé") from None
        data[p.name] = raw
    backup("avant_restauration", base)
    for name, raw in data.items():
        (base / name).write_bytes(raw)
    return sorted(data)


if __name__ == "__main__":
    import sys
    if "--now" in sys.argv:
        print("Sauvegarde :", backup("manuelle"))
    for d, when, why, files in listing():
        print(f"{when}  {why:<20} {', '.join(files)}   ({d.name})")
