"""Version installee (lanceur MHA) : affichee par l'assistant, /panneau, et annoncee apres une mise a jour.

Chez un ami installe par le lanceur : ../installed.json (ecrit par updater.py) -> numero, message, historique.
Chez le createur (pas de lanceur) : « version de developpement ».
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent


def info(base=None):
    """-> {'version': str ou None, 'message': str, 'historique': [...], 'dev': bool}."""
    base = Path(base or BASE)
    try:
        d = json.loads((base.parent / "installed.json").read_text(encoding="utf-8"))
        return {"version": d.get("version"), "message": d.get("message", ""), "historique": d.get("historique", []),
                "dev": False}
    except (OSError, ValueError):
        return {"version": None, "message": "", "historique": [], "dev": True}


def label(base=None):
    i = info(base)
    return "version de développement" if i["dev"] else f"version {i['version']}"


def news(base=None, n=3):
    """Les n dernieres nouveautes (version + message), de la plus recente a la plus ancienne."""
    h = [x for x in info(base)["historique"] if x.get("message")]
    return list(reversed(h))[:n]
