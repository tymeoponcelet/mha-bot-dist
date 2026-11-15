"""Profils de configuration : enregistrer / recharger un ensemble de reglages de farm ("Farm nuit", "Evenement"...).

    python profils.py                      # liste
    (l'assistant : bouton "Profils")

- Un profil = les reglages de FARM de config.json (mode, persos, niveaux, nombres de runs, recharge, garde-chien...).
- Jamais les reglages propres au PC (MACHINE) : chemin d'adb, appareil, paquet du jeu, reglages techniques.
- profils.json est une donnee perso du PC (comme config.json) : jamais publiee, jamais ecrasee par une mise a jour.
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
FILE = BASE / "profils.json"
MACHINE = {"adb", "device", "game_package", "tap_continue", "maintenance_max_h", "watchdog_stall",
           "watchdog_stall_battle", "refill_code", "refill_count", "resume_heure"}
NAME_MAX = 30


def clean_name(name):
    n = re.sub(r"\s+", " ", str(name)).strip()
    if not n:
        raise ValueError("Donne un nom au profil.")
    if len(n) > NAME_MAX:
        raise ValueError(f"Nom trop long ({NAME_MAX} caractères max).")
    return n


def load(path=None):
    try:
        data = json.loads(Path(path or FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data, path=None):
    p = Path(path or FILE)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def farm_part(cfg):
    """Les reglages de farm d'une config (sans les reglages du PC)."""
    return {k: v for k, v in cfg.items() if k not in MACHINE}


def save(name, cfg, path=None):
    """Enregistre (ou remplace) le profil `name` a partir de la config actuelle. -> nom propre."""
    name = clean_name(name)
    data = load(path)
    data[name] = farm_part(cfg)
    _save(data, path)
    return name


def apply(name, cfg, path=None):
    """-> nouvelle config = reglages du PC de `cfg` + reglages de farm du profil. KeyError si profil inconnu.
    Les reglages de farm absents du profil sont retires (le profil est l'etat complet du farm)."""
    prof = load(path)[name]
    new = {k: v for k, v in cfg.items() if k in MACHINE}
    new.update({k: v for k, v in prof.items() if k not in MACHINE})
    return new


def delete(name, path=None):
    data = load(path)
    if name not in data:
        return False
    del data[name]
    _save(data, path)
    return True


def summary(prof):
    """Resume lisible d'un profil."""
    mode = prof.get("mode", "?")
    names = {"story": "Histoire", "hero": "Hero Trial", "night": "Night Ops", "supply": "Supply", "whm": "Événement",
             "aa": "Agency", "chain": "Enchaîner"}
    bits = [names.get(mode, mode)]
    if mode == "supply":
        bits.append(f"{prof.get('supply_mode', '?')} niv. {prof.get('supply_level', '?')}")
    if mode == "whm":
        bits.append(f"{prof.get('whm_event', '?')} niv. {prof.get('whm_level', '?')}")
    if mode == "chain":
        bits.append(f"{len(prof.get('chain_steps', []))} étape(s)")
    if prof.get("pick_hero"):
        bits.append(f"perso {prof['pick_hero']}")
    bits.append(f"recharge {'oui' if prof.get('stamina_refill') else 'non'}")
    bits.append(f"garde-chien {'oui' if prof.get('watchdog') else 'non'}")
    return " · ".join(bits)


if __name__ == "__main__":
    data = load()
    for n, p in data.items():
        print(f"{n:<{NAME_MAX}}  {summary(p)}")
    if not data:
        print("Aucun profil (bouton « Profils » de l'assistant).")
