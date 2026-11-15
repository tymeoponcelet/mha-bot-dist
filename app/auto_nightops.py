"""
Automatisation du mode "Night Ops" (Multi-run) de MHA The Strongest Hero (emulateur + ADB).

Parcours :
  menu principal -> ua -> solo -> nightops -> challenge -> plus -> am -> multirun -> max -> start
  Pendant le combat : controlbattle (si la fenetre apparait) et auto (active le mode AUTO s'il est desactive).
  Entre chaque combat du multi-run : challagain ("Challenge Again (6s)", le compte a rebours est ignore).
  Sur la fenetre Multi-run, "max" est TOUJOURS tape avant "start" (les deux boutons sont visibles en meme temps).
  Apres le combat : continue ("Tap to continue"). Tant qu'aucun bouton n'est reconnu en combat / chargement,
  le bot tape en rafale sur le viseur (comme le mode Histoire), jamais sur le menu principal.

Utilisation :
    python auto_nightops.py              # lance le bot (nombre de lancements = config.json "nightops_runs", defaut 1)
    python auto_nightops.py --runs 3     # 3 lancements de "start" puis arret a la fin du dernier combat
    python auto_nightops.py --test       # UNE capture, affiche le score de chaque image (pour regler)

Dossier templates_nightops/ (captures a la MEME resolution que l'emulateur) :
    ua solo nightops challenge plus am multirun max start controlbattle continue challagain auto   (.png)
    auto.png = le bouton AUTO quand il est DESACTIVE.
  PERSO : plus d'am.png fixe. Le perso est choisi dans l'assistant (config.json "pick_hero") ou avec --hero <nom>
  (ex. --hero mirio) ; sa ligne est dans templates_chars/<nom>.png. Si la ligne n'est pas visible, le bot fait defiler
  la liste (bas puis haut) jusqu'a la trouver, puis tape SON bouton "Deploy" (voir heroes.py).
"""
import json
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import emulateur
import fastmatch
import heroes
import journal
import nav
import refill
import watchdog

BASE = Path(__file__).resolve().parent
TPL_DIR = BASE / "templates_nightops"

# ======================= REGLAGES =======================
ADB = "adb"
DEVICE = None
THRESHOLD = 0.85
AUTO_THRESHOLD = 0.60          # bouton AUTO (decor variable derriere)
LOOP_DELAY = 0.22              # pause entre deux captures (s)
SETTLE = 0.45                  # pause apres un tap (s)
CD_SCALE = 0.6                 # multiplie TOUS les delais mini entre deux taps du meme bouton (1.0 = ancienne vitesse, plus lent)
IDLE_LIMIT = 120               # s sans rien reconnaitre (hors combat) -> arret
BATTLE_END_DELAY = 20          # s minimum apres "start" avant de considerer le combat termine
MAX_RUNS = 1                   # nombre de lancements de "start"
AUTO_MAX_TAPS = 3              # securite : taps max sur AUTO par lancement
# Clic continu : entre le lancement du combat et son retour au menu, quand aucun bouton n'est reconnu,
# le script tape en rafale sur le viseur (position relative, ~ (450, 443) sur une capture 1085x610).
CLICK_ENABLED = True
CLICK_POS = (0.415, 0.726)
CLICK_BURST = 5                # nombre de taps par rafale
PORTS = ["127.0.0.1:16384", "127.0.0.1:7555", "127.0.0.1:5555", "127.0.0.1:62001"]

_CFG = BASE / "config.json"
if _CFG.exists():
    try:
        _c = json.loads(_CFG.read_text(encoding="utf-8"))
        ADB = _c.get("adb", ADB)
        DEVICE = _c.get("device", DEVICE)
        MAX_RUNS = int(_c.get("nightops_runs", MAX_RUNS) or MAX_RUNS)
    except Exception:
        pass
if "--runs" in sys.argv:
    try:
        MAX_RUNS = max(1, int(sys.argv[sys.argv.index("--runs") + 1]))
    except (IndexError, ValueError):
        pass

# Ordre = du plus avance au plus ancien : si plusieurs boutons sont visibles, le plus avance gagne.
# nom : (fichier, cooldown mini entre 2 taps, options)
SPEC = {
    # "Challenge Again (6s)" : on ne compare que le texte "Challenge Again" (le chiffre du compte a rebours change)
    "challagain":    dict(cd=2.0, crop=(0.05, 0.2, 0.62, 0.8), thr=0.80),
    "continue":      dict(cd=2.0, thr=0.80),          # "Tap to continue" apres le combat (fond variable -> seuil plus souple)
    "controlbattle": dict(cd=3.0),
    "auto":          dict(cd=8.0, crop=(0.10, 0.28, 0.88, 0.70), mask_white=True, thr=AUTO_THRESHOLD),
    "start":         dict(cd=10.0),
    # on ignore les bords de la fleche orange (fond variable) : on compare le coeur du bouton + le texte MAX
    "max":           dict(cd=2.5, crop=(0.2, 0.2, 0.85, 0.8), thr=0.80),
    "multirun":      dict(cd=2.5),
    # on reconnait la vignette du perso (28 % gauche de la ligne) et on tape "Deploy" (position relative a la ligne)
    "am":            dict(cd=2.5, crop=(0, 0, 0.28, 1), tap=(0.782, 0.581)),
    "plus":          dict(cd=2.5),
    "challenge":     dict(cd=2.5),
    "nightops":      dict(cd=2.5),
    "solo":          dict(cd=2.5),
    # le point rouge de notification disparait parfois : on ignore le haut de l'icone
    "ua":            dict(cd=2.5, crop=(0, 0.30, 1, 1)),
}
BATTLE_ONLY = {"challagain", "continue", "controlbattle", "auto"}   # tout le reste = ecrans de navigation


class Done(Exception):
    pass


# ======================= ADB =======================
def _resolve_adb(path):
    for cand in (path, shutil.which("adb"), BASE / "platform-tools" / "adb.exe", Path(r"C:\platform-tools\adb.exe")):
        if cand and (shutil.which(str(cand)) or Path(str(cand)).exists()):
            return str(cand)
    raise SystemExit("adb introuvable. Mets platform-tools/ a cote du script (ou renseigne config.json).")


def _devices():
    out = subprocess.run([ADB, "devices"], capture_output=True, text=True).stdout.splitlines()[1:]
    return [l.split()[0] for l in out if l.strip().endswith("device")]


def init_adb():
    global ADB, DEVICE
    ADB = _resolve_adb(ADB)
    devs = _devices()
    if not devs:
        for p in PORTS:
            try:
                subprocess.run([ADB, "connect", p], capture_output=True, timeout=6)
            except Exception:
                pass
        devs = _devices()
    if not devs:
        raise SystemExit("Aucun emulateur detecte. Lance-le (jeu ouvert) puis refais 'adb devices'.")
    if DEVICE not in devs:
        if DEVICE:
            print(f"(!) appareil '{DEVICE}' absent, on prend '{devs[0]}'")
        DEVICE = devs[0]
    print(f"ADB : {ADB} | appareil : {DEVICE}")
    emulateur.remember()               # instance MuMu a relancer si l'emulateur plante (voir emulateur.py)


def _find_device():
    """Pour la reprise (emulateur.py) : appareil visible par ADB (ports connus compris), ou None."""
    def devs():
        try:
            out = subprocess.run([ADB, "devices"], capture_output=True, text=True, timeout=15).stdout.splitlines()[1:]
        except Exception:
            return []
        return [l.split()[0] for l in out if l.strip().endswith("device")]
    d = devs()
    if not d:
        for p in PORTS:
            try:
                subprocess.run([ADB, "connect", p], capture_output=True, timeout=6)
            except Exception:
                pass
        d = devs()
    return DEVICE if DEVICE in d else (d[0] if d else None)


def adb(*args, binary=False, timeout=30):
    r = subprocess.run([ADB, "-s", DEVICE, *args], capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="ignore").strip() or "erreur adb")
    return r.stdout if binary else r.stdout.decode(errors="ignore")


FAST_SHOT = True               # capture brute (sans compression PNG cote emulateur) : ~2x plus rapide ; repli auto si ca ne marche pas


def _raw_shot():
    """Capture brute (RGBA). -> image BGR, ou None si le format n'est pas celui attendu."""
    raw = adb("exec-out", "screencap", binary=True)
    w, h, fmt = struct.unpack("<III", raw[:12])
    n = w * h * 4
    if fmt != 1 or not (0 < w <= 8192 and 0 < h <= 8192):
        return None
    for hdr in (12, 16):                       # l'en-tete fait 12 octets (Android <= 9) ou 16 (Android 10+)
        if len(raw) - hdr == n:
            img = np.frombuffer(raw, np.uint8, count=n, offset=hdr).reshape(h, w, 4)
            return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    return None


def screenshot():
    global FAST_SHOT, DEVICE
    err = None
    for i in range(5):
        if FAST_SHOT:
            try:
                img = _raw_shot()
                if img is not None:
                    journal.frame(img)             # boite noire (dernieres captures, en cas d'incident)
                    return img
                FAST_SHOT = False
                log("capture rapide non reconnue : retour a la capture PNG (normale)")
            except Exception as e:
                err = e                        # souci passager : on tente le PNG pour ce tour
        try:
            png = adb("exec-out", "screencap", "-p", binary=True)
            img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                journal.frame(img)
                return img
        except Exception as e:
            err = e
        time.sleep(2)
    dev = emulateur.recover(ADB, _find_device, log, why=str(err))     # ADB / MuMu relances, alerte Discord
    if dev:
        DEVICE = dev
        return screenshot()
    raise SystemExit(f"Capture impossible ({err}). L'emulateur est-il toujours connecte ?")


def tap(x, y):
    adb("shell", "input", "tap", str(int(x)), str(int(y)))


def tap_burst(x, y, n):
    """n taps au meme endroit en un seul appel ADB (beaucoup plus rapide que n appels)."""
    adb("shell", "; ".join([f"input tap {int(x)} {int(y)}"] * n))


# ======================= TEMPLATES =======================
class Tpl:
    def __init__(self, path, crop=(0, 0, 1, 1), mask_white=False, thr=THRESHOLD, cd=2.5, tap=(0.5, 0.5)):
        full = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if full is None:
            raise FileNotFoundError(path)
        H, W = full.shape[:2]
        x0, y0, x1, y1 = int(crop[0] * W), int(crop[1] * H), int(crop[2] * W), int(crop[3] * H)
        self.name = f"{Path(path).parent.name}/{Path(path).name}"     # pour la detection de derive (derive.py)
        self.size, self.off, self.thr, self.mask_white, self.cd, self.tap = (W, H), (x0, y0), thr, mask_white, cd * CD_SCALE, tap
        self.img = self.prep(full[y0:y1, x0:x1])
        fastmatch.prepare(self)                 # versions noir et blanc / demi-echelle (reconnaissance rapide)

    def prep(self, img):
        # AUTO : on compare uniquement les pixels blancs (le decor change d'un combat a l'autre)
        return cv2.inRange(img, (200, 200, 200), (255, 255, 255)) if self.mask_white else img

    _prep = prep                                # ancien nom, garde par compatibilite

    def best_exact(self, screen):
        """Comparaison couleur sur TOUTE la capture (ancienne methode : sert aux tests --test et de secours)."""
        return fastmatch.full(self, screen)

    def best(self, screen):
        """Reconnaissance rapide (voir fastmatch.py) : meme score qu'avant, ~13x plus vite."""
        return fastmatch.best(self, screen)

    def find(self, screen):
        """-> (x_tap, y_tap, score) ou None. Point de tap relatif a l'image COMPLETE (centre par defaut)."""
        score, loc = self.best(screen)
        if score < self.thr:
            return None
        W, H = self.size
        return loc[0] - self.off[0] + self.tap[0] * W, loc[1] - self.off[1] + self.tap[1] * H, score


T = {}
PICK = None                             # choix du perso (heroes.Picker), cree par load_templates()
GATE = None                             # barriere "bon perso place ?" avant Multi-run (heroes.SlotGate)


def load_templates(strict=True):
    global PICK, GATE
    missing = []
    for name, spec in SPEC.items():
        if name == "am":
            continue                    # le perso n'est plus une image fixe : voir heroes.py / templates_chars/
        p = TPL_DIR / f"{name}.png"
        if not p.exists():
            missing.append(f"templates_nightops/{name}.png")
            continue
        T[name] = Tpl(p, **spec)
    try:
        PICK = heroes.Picker(heroes.chosen_name(), adb, log, fail=Done)
        T["am"] = heroes.RowTpl(PICK, cd=SPEC["am"]["cd"] * CD_SCALE)
        VERIFY = "--no-verify" not in sys.argv and heroes.chosen_explicit()
        if not heroes.chosen_explicit():      # aucun perso choisi : on garde celui qui est deja place, on ne le remplace pas
            log("aucun perso choisi : je garde celui qui est deja place "
                "(choisis-en un dans l'assistant, sur Discord, ou avec --hero, pour que le bot le change)")
        GATE = heroes.SlotGate(heroes.SlotReader(), PICK.name, log, Done, enabled=VERIFY)
    except FileNotFoundError as e:
        missing.append(str(e))
    if missing and strict:
        raise SystemExit("Images manquantes : " + ", ".join(missing))
    return missing


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ======================= BOT =======================
def run_test():
    fastmatch.FAST = False                 # --test : scores exacts (comparaison couleur partout)
    load_templates(strict=False)
    screen = screenshot()
    print(f"Capture : {screen.shape[1]}x{screen.shape[0]}")
    print(f"{'image':<15}{'score':>7}  {'seuil':>6}  position")
    for name, t in T.items():
        score, loc = t.best(screen)
        print(f"{name:<15}{score:>7.2f}  {t.thr:>6.2f}  {'TROUVE ' + str(loc) if score >= t.thr else '-'}")
    for name in SPEC:
        if name not in T:
            print(f"{name:<15}  fichier manquant : {name}.png")


def make_refill():
    """Recharge de stamina (refill.py) si l'option est active (assistant / Discord / --refill) ; sinon None."""
    if not refill.enabled():
        return None
    rf = refill.Refill(screenshot, tap, adb, log, Done)
    miss = rf.missing()
    if miss:
        log("(!) Recharge de stamina desactivee, images manquantes : " + ", ".join(miss))
        return None
    log(f"Recharge de stamina : ACTIVEE (code {rf.code} x{rf.count})")
    return rf


def main():
    if "--test" in sys.argv:
        init_adb()
        return run_test()
    load_templates()
    init_adb()
    if "--no-home" not in sys.argv:
        nav.Home(screenshot, tap, log, back=lambda: adb("shell", "input", "keyevent", "4")).go()       # retour au menu principal, d'ou qu'on parte
    last_tap = {n: 0.0 for n in SPEC}
    runs, auto_taps, again_n = 0, 0, 0
    max_done = False                    # "max" deja tape pour le prochain "start" ?
    in_battle, t_start = False, 0.0
    awaiting_pick, pick_t = False, 0.0  # "plus" tape : la liste des persos va s'ouvrir
    last_action = time.time()
    RF = make_refill()                  # recharge de stamina par code (option) ; None si desactivee
    REPRISE = nav.Reprise(nav.Home(screenshot, tap, log, back=lambda: adb("shell", "input", "keyevent", "4")), log)   # ecran inconnu 60 s -> menu + reprise
    seen_t = time.time()                # derniere fois qu'une image a ete reconnue
    WD = watchdog.make(screenshot, tap, adb, log, Done)     # chien de garde (option) : maintenance / jeu bloque -> relance ; None si desactive
    print(f"Perso : {heroes.pretty(PICK.name)}")
    print(f"Demarrage : {MAX_RUNS} lancement(s). Ctrl+C pour arreter. (conseil : commence depuis le menu principal)")

    while True:
        screen = screenshot()
        now = time.time()
        case = RF.detect(screen) if RF else None
        if case:                        # plus de stamina (bouton jaune / message / fenetre Stamina Supply)
            if case != "insustam" and in_battle and now - t_start < 15:
                runs = max(0, runs - 1)             # le Start n'a rien lance (pas de stamina) : ce lancement sera refait
            RF.run(case, screen)
            if case == "insustam" and runs >= MAX_RUNS:     # dernier multi-run termine, Challenge Again n'etait pas a relancer
                raise Done(f"{runs}/{MAX_RUNS} lancement(s) termine(s). Stamina rechargee. Fini !")
            in_battle, awaiting_pick, max_done, auto_taps = False, False, False, 0
            PICK.reset()
            GATE.reset()
            last_tap = {n: 0.0 for n in SPEC}
            last_action = time.time()
            continue
        if WD and WD.poll(screen, in_battle):          # maintenance / accueil / jeu bloque ou ferme : le jeu a ete relance, on repart du menu
            if in_battle:
                runs = max(0, runs - 1)             # le multi-run en cours est perdu : il sera refait
            in_battle, awaiting_pick, max_done, auto_taps = False, False, False, 0
            PICK.reset()
            GATE.reset()
            last_tap = {n: 0.0 for n in SPEC}
            last_action = time.time()
            continue
        hits = {name: h for name, h in fastmatch.find_many(T.items(), screen).items() if h}   # en parallele, meme resultat

        # fin de combat : un ecran de navigation reapparait bien apres le dernier "start"
        if in_battle and now - t_start > BATTLE_END_DELAY and any(n not in BATTLE_ONLY for n in hits):
            in_battle = False
            log("combat termine")
            if runs >= MAX_RUNS:
                raise Done(f"{runs}/{MAX_RUNS} lancement(s) termine(s). Fini !")

        acted = False
        for name in SPEC:                       # du plus avance au plus ancien
            if name not in hits or now - last_tap[name] < T[name].cd:
                continue
            if name == "auto" and (not in_battle or auto_taps >= AUTO_MAX_TAPS):
                continue                        # on n'active AUTO qu'en combat, et pas en boucle
            if name == "start" and runs >= MAX_RUNS:
                continue
            if name == "start" and "max" in hits and not max_done:
                continue                        # fenetre Multi-run : Max d'abord, Start ensuite
            if name == "max" and max_done:
                continue                        # deja fait : on laisse passer Start
            if name == "multirun" and not in_battle:
                if "plus" in hits or (GATE.swap_t and awaiting_pick):
                    continue                    # emplacement vide, ou liste des persos en cours d'ouverture : on ne lance pas
                state, pt = GATE.check(screen, (hits["multirun"][0], hits["multirun"][1]))
                if state == "wait":
                    continue
                if state == "swap":
                    tap(pt[0], pt[1])
                    last_tap[name] = last_action = time.time()
                    awaiting_pick, pick_t = True, time.time()
                    PICK.reset()
                    log("emplacement du perso -> ouverture de la liste")
                    acted = True
                    time.sleep(SETTLE + 0.5)
                    break
            x, y, score = hits[name]
            tap(x, y)
            last_tap[name] = last_action = time.time()
            extra = ""
            if name == "max":
                max_done = True
            elif name == "start":
                runs += 1
                in_battle, t_start, auto_taps, again_n, max_done = True, time.time(), 0, 0, False
            elif name == "auto":
                auto_taps += 1
            elif name == "challagain":
                again_n += 1
                extra = f" #{again_n}"
            elif name == "plus":
                awaiting_pick, pick_t = True, time.time()
                PICK.reset()
                GATE.note_plus((x, y), (hits["multirun"][0], hits["multirun"][1]) if "multirun" in hits else None)
            elif name == "challenge":
                GATE.reset()
            if name == "am":
                GATE.done()
            if name in ("am", "start"):
                awaiting_pick = False
                PICK.reset()
            if name == "start":
                GATE.reset()
            if WD and name in ("challagain", "start"):
                WD.kick()                       # vrai progres (fin de run / lancement) : le chrono "bloque" repart a zero
            log(f"{name} ({score:.2f}{extra}) | run {runs}/{MAX_RUNS}")
            acted = True
            time.sleep(SETTLE)
            break

        if not acted:
            # liste des persos ouverte mais le perso voulu n'est pas visible : on fait defiler (bas puis haut)
            if GATE.swap_t and not in_battle and GATE.swap_stalled(PICK.on_list(screen) or "am" in hits):   # on_list = ~1 s : seulement quand on attend la liste
                raise Done("Le clic sur l'emplacement du perso place n'a PAS ouvert la liste des persos. Change-le a la main, "
                           "puis relance (ou lance avec --no-verify).")
            if awaiting_pick and not in_battle and "am" not in hits:
                if now - pick_t > 90:
                    awaiting_pick = False
                elif PICK.step(screen):
                    last_action = time.time()
                    continue
            if CLICK_ENABLED and in_battle and not any(n not in BATTLE_ONLY for n in hits):
                h, w = screen.shape[:2]
                tap_burst(CLICK_POS[0] * w, CLICK_POS[1] * h, CLICK_BURST)
                continue
            if hits:
                seen_t = now
            if not in_battle and REPRISE.due(now - max(last_action, seen_t)) and REPRISE.run():
                in_battle, awaiting_pick, max_done, auto_taps = False, False, False, 0     # comme apres le garde-chien
                PICK.reset()
                GATE.reset()
                last_tap = {n: 0.0 for n in SPEC}
                last_action = seen_t = time.time()
                continue
            if not in_battle and now - last_action > IDLE_LIMIT and not WD:     # avec le chien de garde, c'est lui qui relance le jeu
                raise Done(f"Plus rien reconnu depuis {IDLE_LIMIT}s : arret (ecran inattendu, plus de stamina ?).")
            time.sleep(LOOP_DELAY)


if __name__ == "__main__":
    import journal
    journal.start("nightops")                 # journal de session dans logs/ (garde les 10 derniers)
    journal.run(main, Done)               # meme affichage qu'avant + incident (capture, boite noire) si arret anormal
