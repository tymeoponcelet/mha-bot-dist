"""Verification de l'installation (assistant, encadre "Verification") : lecture seule, rien n'est modifie.

    python verif.py          # meme chose dans la console

Chaque verification -> (cle, etat, texte, conseil) avec etat = "ok" | "warn" | "bad".
"""
import os  # noqa: E402
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # memoire : sinon numpy reserve ~370 Mo par processus (16/11/2026 : « Insufficient memory » chez un ami)
import json
import re
import subprocess
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WANT = (1600, 900)                         # resolution pour laquelle toutes les images ont ete faites
DEFAULT_PKG = "chapter2.tsh"               # paquet Android du jeu (config "game_package" sinon)
ALIVE = BASE / "logs" / "discord_bot.vivant"   # ecrit toutes les 15 s par discord_bot.py
ALIVE_MAX = 60                             # s : au-dela, le bot Discord est considere comme arrete


def _cfg():
    try:
        return json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _adb(adb, *args, timeout=15):
    try:
        r = subprocess.run([adb, *args], capture_output=True, text=True, encoding="utf-8", errors="ignore",
                           timeout=timeout, creationflags=NO_WINDOW)
        return r.returncode, r.stdout
    except Exception as e:                 # noqa: BLE001
        return -1, str(e)


def check_device(adb, device):
    if not adb:
        return "device", "bad", "ADB introuvable", "Étape 2 : clique sur « Télécharger »."
    code, out = _adb(adb, "devices")
    devs = [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]
    if not devs:
        return "device", "bad", "aucun émulateur ne répond", "Lance MuMu avec le jeu, puis « Détecter » (étape 4)."
    if device and device not in devs:
        return "device", "warn", f"« {device} » absent, mais {', '.join(devs)} répond", "Étape 4 : « Détecter » puis ✔."
    dev = device or devs[0]
    code, _ = _adb(adb, "-s", dev, "shell", "echo", "ok")
    if code != 0:
        return "device", "bad", f"{dev} ne répond pas", "Redémarre MuMu."
    return "device", "ok", f"émulateur {dev} : répond", ""


def parse_size(out):
    """Sortie de 'wm size' -> (largeur, hauteur) effective (Override prioritaire), paysage. None si illisible."""
    sizes = dict(re.findall(r"(Physical|Override) size:\s*(\d+x\d+)", out))
    s = sizes.get("Override") or sizes.get("Physical")
    if not s:
        return None
    w, h = (int(v) for v in s.split("x"))
    return (max(w, h), min(w, h))


def check_resolution(adb, device):
    code, out = _adb(adb, "-s", device, "shell", "wm", "size") if adb and device else (-1, "")
    size = parse_size(out) if code == 0 else None
    if not size:
        return "resolution", "warn", "résolution illisible", "Vérifie que l'émulateur est lancé."
    if size != WANT:
        return ("resolution", "bad", f"résolution {size[0]}x{size[1]} (il faut {WANT[0]}x{WANT[1]})",
                "MuMu : Paramètres > Affichage > 1600x900, puis redémarre MuMu.")
    return "resolution", "ok", f"résolution {size[0]}x{size[1]}", ""


def check_game(adb, device):
    pkg = str(_cfg().get("game_package") or DEFAULT_PKG)
    code, out = _adb(adb, "-s", device, "shell", "pm", "list", "packages", pkg) if adb and device else (-1, "")
    if code != 0:
        return "game", "warn", "impossible de vérifier le jeu", ""
    if f"package:{pkg}" not in out.split():
        return "game", "bad", f"jeu non installé ({pkg})", "Installe le jeu dans MuMu (ou vérifie game_package)."
    return "game", "ok", f"jeu installé ({pkg})", ""


def check_foreground(adb, device):
    pkg = str(_cfg().get("game_package") or DEFAULT_PKG)
    code, out = _adb(adb, "-s", device, "shell", "dumpsys", "window") if adb and device else (-1, "")
    focus = " ".join(l for l in out.splitlines() if "mCurrentFocus" in l or "mFocusedApp" in l)
    if code != 0 or not focus:
        return "foreground", "warn", "écran actuel inconnu", ""
    if pkg in focus:
        return "foreground", "ok", "jeu ouvert à l'écran", ""
    return "foreground", "warn", "le jeu n'est pas à l'écran", "Ouvre le jeu avant de lancer un farm."


def check_discord():
    if not (BASE / "discord_config.json").exists():
        return "discord", "warn", "bot Discord pas configuré (facultatif)", "Fenêtre MHA > « Démarrer le bot Discord »."
    try:
        age = time.time() - ALIVE.stat().st_mtime
    except OSError:
        age = None
    if age is not None and age < ALIVE_MAX:
        return "discord", "ok", "bot Discord en ligne", ""
    return "discord", "warn", "bot Discord configuré mais arrêté", "Lance lancer_discord.bat (ou la fenêtre MHA)."


def alt_device(adb, device):
    """Appareil qui repond quand celui enregistre est absent (sinon None) : propose dans l'assistant."""
    if not adb:
        return None
    code, out = _adb(adb, "devices")
    devs = [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]
    return devs[0] if devs and device not in devs else None


def run_all(adb, device):
    """Toutes les verifications, dans l'ordre d'affichage."""
    out = [check_device(adb, device)]
    dev_ok = out[0][1] != "bad"
    if dev_ok and (not device or out[0][1] == "warn"):      # appareil enregistre absent : on verifie celui qui repond
        code, txt = _adb(adb, "devices")
        devs = [l.split()[0] for l in txt.splitlines()[1:] if l.strip().endswith("device")]
        device = devs[0] if devs else None
    for f in (check_resolution, check_game, check_foreground):
        out.append(f(adb, device) if dev_ok else (f.__name__[6:], "warn", "émulateur indisponible", ""))
    out.append(check_discord())
    return out


if __name__ == "__main__":
    import auto_nightops as nt
    adb = nt._resolve_adb(_cfg().get("adb") or "adb")
    icon = {"ok": "OK  ", "warn": "(!) ", "bad": "NON "}
    for key, st, txt, tip in run_all(adb, _cfg().get("device")):
        print(f"{icon[st]} {txt}" + (f"  -> {tip}" if tip else ""))
