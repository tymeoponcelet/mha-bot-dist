"""
Automatisation de la boucle "Peacekeeping" de MHA The Strongest Hero (émulateur + ADB).

Boucle :
  quête sous la minimap -> Deploy -> (chargement) -> Pathfinding
  -> 4 combats, "Tap to continue" après chacun -> croix rouge pour fermer la page -> recommence

Prérequis :
    pip install opencv-python numpy
    ADB (LDPlayer / BlueStacks / platform-tools), débogage ADB activé.

Dossier templates/ (captures recadrées, même résolution que l'émulateur) :
    main.png         -> un élément fixe de l'écran principal (ex: l'icône "UA" en bas à droite)
                        Sert à vérifier qu'on est en ville avant de cliquer sur la quête.
    deploy.png       -> le bouton jaune "Deploy"
    pathfinding.png  -> le bouton "Pathfinding"
    continue.png     -> le texte "Tap to continue"
    close.png        -> la croix rouge en haut à droite de la page de récompenses
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

ADB = "adb"
DEVICE = None  # nom affiché par "adb devices" (None si un seul appareil)
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
THRESHOLD = 0.85
LOOP_DELAY = 0.6
IDLE_LIMIT = 120         # secondes sans rien détecter -> arrêt (stamina vide, bug, etc.)
MAX_RUNS = 1             # nombre de runs COMPLETS (4 combats + fermeture). Passe à 20 après le test.

# Réglages personnels écrits par l'assistant (config.json) : prioritaires sur les valeurs ci-dessus
_CFG = Path(__file__).resolve().parent / "config.json"
if _CFG.exists():
    _c = json.loads(_CFG.read_text(encoding="utf-8"))
    ADB = _c.get("adb", ADB)
    DEVICE = _c.get("device", DEVICE)
    MAX_RUNS = _c.get("max_runs", MAX_RUNS)


def _resolve_adb(path):
    """Cherche adb.exe : chemin configuré, PATH, dossier platform-tools à côté du script, C:\\platform-tools."""
    here = Path(__file__).resolve().parent
    for cand in (path, shutil.which("adb"), here / "platform-tools" / "adb.exe",
                 Path(r"C:\platform-tools\adb.exe")):
        if cand and (shutil.which(str(cand)) or Path(str(cand)).exists()):
            return str(cand)
    raise SystemExit("adb.exe introuvable. Mets-le dans un dossier 'platform-tools' à côté du script, "
                     "ou lance lancer_assistant.bat (étape 2 : Télécharger ADB).")


ADB = _resolve_adb(ADB)

if DEVICE is None:
    # Aucun appareil configuré : on prend le premier appareil détecté par adb
    _out = subprocess.run([ADB, "devices"], capture_output=True, text=True).stdout.splitlines()[1:]
    _devs = [l.split()[0] for l in _out if l.strip().endswith("device")]
    if not _devs:
        raise SystemExit("Aucun émulateur détecté. Lance-le (jeu ouvert) puis refais 'adb devices'.")
    DEVICE = _devs[0]
print(f"ADB : {ADB} | appareil : {DEVICE}")

# Ordre = priorité. (nom, fichier, délai mini entre deux taps en secondes)
STEPS = [
    ("continue",    "continue.png",    2.0),
    ("close",       "close.png",       3.0),
    ("pathfinding", "pathfinding.png", 5.0),
    ("deploy",      "deploy.png",      8.0),
    ("quest",       "main.png",        5.0),   # détecte l'écran principal, puis tap fixe
]

# Position du bloc de quête (sous la minimap), en proportion de l'écran (x, y).
# Estimée sur ta capture 1080x606 : centre du bloc ~ (975, 192). Ajuste si besoin.
QUEST_POS = (0.90, 0.32)

# Clic continu : quand aucun bouton n'est détecté (combats, chargements...), le script tape
# en rafale sur ce point, SAUF sur l'écran principal (pour ne pas annuler le déplacement auto).
# Position du viseur (cible) estimée sur ta capture 1085x610 : ~ (450, 443).
CLICK_ENABLED = True
CLICK_POS = (0.415, 0.726)
CLICK_BURST = 5          # nombre de taps par rafale


def adb(*args, binary=False):
    cmd = [ADB] + (["-s", DEVICE] if DEVICE else []) + list(args)
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    return out if binary else out.decode(errors="ignore")


def screenshot():
    png = adb("exec-out", "screencap", "-p", binary=True)
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)


def tap(x, y):
    adb("shell", "input", "tap", str(int(x)), str(int(y)))


def tap_burst(x, y, n):
    """n taps au même endroit en un seul appel ADB (beaucoup plus rapide que n appels)."""
    adb("shell", "; ".join([f"input tap {int(x)} {int(y)}"] * n))


def find(screen, tpl):
    res = cv2.matchTemplate(screen, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score >= THRESHOLD:
        h, w = tpl.shape[:2]
        return loc[0] + w // 2, loc[1] + h // 2, score
    return None


def main():
    templates = {}
    for name, fname, cd in STEPS:
        img = cv2.imread(str(TEMPLATES_DIR / fname), cv2.IMREAD_COLOR)
        if img is None:
            print(f"(!) templates/{fname} introuvable, étape '{name}' ignorée")
            continue
        templates[name] = (img, cd)
    if not templates:
        raise SystemExit("Aucun template chargé.")

    last_tap = {name: 0.0 for name in templates}
    last_action = time.time()
    runs = 0
    print("Démarrage. Ctrl+C pour arrêter.")

    while runs < MAX_RUNS:
        screen = screenshot()
        now = time.time()
        for name, _, _ in STEPS:
            if name not in templates:
                continue
            tpl, cooldown = templates[name]
            if now - last_tap[name] < cooldown:
                continue
            hit = find(screen, tpl)
            if hit:
                x, y, score = hit
                if name == "quest":
                    # On est sur l'écran principal : on tape sur le bloc de quête (position fixe)
                    h, w = screen.shape[:2]
                    x, y = QUEST_POS[0] * w, QUEST_POS[1] * h
                tap(x, y)
                last_tap[name] = last_action = time.time()
                if name == "close":
                    runs += 1  # un run est terminé quand la page de récompenses est fermée
                print(f"[{time.strftime('%H:%M:%S')}] {name} ({score:.2f}) | run {runs}/{MAX_RUNS}")
                break
        else:
            if now - last_action > IDLE_LIMIT:
                print("Plus rien détecté : arrêt (stamina vide ou écran inattendu).")
                return
            # Rien à cliquer : tap en continu sur le viseur, sauf sur l'écran principal
            if CLICK_ENABLED:
                on_main = "quest" in templates and find(screen, templates["quest"][0]) is not None
                if not on_main:
                    h, w = screen.shape[:2]
                    tap_burst(CLICK_POS[0] * w, CLICK_POS[1] * h, CLICK_BURST)
                    continue
        time.sleep(LOOP_DELAY)

    print(f"{MAX_RUNS} runs terminés.")


if __name__ == "__main__":
    import journal
    journal.start("histoire")                 # journal de session dans logs/ (garde les 10 derniers)
    journal.run(main, stop_msg="Arrêté.")
