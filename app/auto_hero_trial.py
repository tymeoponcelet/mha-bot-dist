"""
Automatisation du mode "Hero Trial" de MHA The Strongest Hero (émulateur + ADB).

Boucle :
  menu principal -> UA -> Solo -> Hero Trial -> carte (ochaco / iida / tsuyu)
  -> (plus -> choix du perso (am) la 1re fois seulement) -> verification des essais (0/2 ?) -> Challenge
  -> combat (AUTO active) -> Challenge Again tant qu'il reste des essais
  -> "Insufficient chances" -> Exit Stage -> perso suivant ... jusqu'a ce que tous soient faits.

Utilisation :
    python auto_hero_trial.py          # lance le bot
    python auto_hero_trial.py --test   # prend UNE capture et affiche le score de chaque image (pour regler)
    python auto_hero_trial.py --swipe left   # un seul glissement (left ou right) pour tester la zone
    python auto_hero_trial.py --chars deku,denki   # choisir les persos a farmer (sinon : config.json, cle "hero_chars")
    python auto_hero_trial.py --list         # liste les persos disponibles (PNG de templates_hero/)

Pour AJOUTER un perso : depose sa carte (ex. todoroki.png, meme resolution) dans templates_hero/ -> il apparait
automatiquement dans l'assistant (setup_assistant.py) et dans --list.

Dossier templates_hero/ (captures a la MEME resolution que l'emulateur) :
    ua.png solo.png herotrial.png          navigation
    ochaco.png iida.png tsuyu.png          cartes des persos a farmer
    plus.png am.png                        choix du perso (am = ligne du perso utilise)
    attempt.png                            bandeau "Attempts (0/2)"  (= plus d'essai)
    challenge.png                          bouton Challenge
    auto.png                               bouton AUTO quand il est DESACTIVE (le bot clique dessus)
    again.png insufficient.png exit.png    fin de combat
  Optionnels : auto_on.png (AUTO active), continue.png, close.png
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

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
TPL_DIR = BASE / "templates_hero"
PICK = None          # choix du perso qui combat (heroes.Picker) : cree par load_templates(), ligne dans templates_chars/
SLOT = None          # lecture du perso deja place dans l'emplacement du panneau (heroes.SlotReader) : templates_slot/
GATE = None          # barriere "bon perso place ?" avant Challenge (heroes.SlotGate)

# ======================= REGLAGES =======================
# Images qui ne sont PAS des cartes de persos (tout autre PNG de templates_hero/ = un perso farmable)
NAV_FILES = {"ua", "solo", "herotrial", "plus", "am", "attempt", "challenge", "auto", "auto_on",
             "again", "insufficient", "exit", "continue", "close"}
CHAR_ORDER = ["ochaco", "iida", "tsuyu", "deku", "denki", "eijiro", "mineta"]


def available_chars():
    names = [p.stem for p in TPL_DIR.glob("*.png") if p.stem not in NAV_FILES]
    return sorted(names, key=lambda n: (CHAR_ORDER.index(n) if n in CHAR_ORDER else len(CHAR_ORDER), n))


CHARS = ["ochaco", "iida", "tsuyu", "deku", "denki", "eijiro", "mineta"]   # defaut (ecrase par config.json / --chars)
ADB = "adb"
DEVICE = None                         # None = auto-detection
THRESHOLD = 0.85                      # seuil de reconnaissance standard
CARD_THRESHOLD = 0.80                 # cartes des persos (l'illustration peut varier un peu)
AUTO_THRESHOLD = 0.60                 # bouton AUTO (fond de decor variable)
ATTEMPT_ZERO = 0.80                   # le 1er chiffre du compteur doit ressembler a ce point au "0" de attempt.png
ATTEMPT_MARGIN = 0.10                 # ... et au moins autant (+marge) que le "2" de droite (cas 2/2)
SETTLE = 0.7                          # pause apres chaque tap (s)
LOOP_DELAY = 0.3                      # pause entre deux captures (s)
STAGE_TIMEOUT = 30                    # s sans progres dans une etape -> on se re-synchronise
IDLE_LIMIT = 300                      # s sans AUCUNE action -> arret
MAX_BATTLES = 14                      # securite : recalcule plus bas = 2 essais x nombre de persos
AUTO_POS = None                       # inutile normalement : le bot clique sur auto.png
SWIPE_Y = 0.50                        # hauteur (0-1) de la ligne de cartes, la ou on fait glisser le doigt
SWIPE_X_MIN = 0.06                    # zone horizontale (0-1) ou on glisse : bord gauche de la liste de cartes
SWIPE_X_MAX = 0.94                    # ... bord droit. Si un panneau lateral est visible (plus/Attempts/Challenge), la zone s'arrete avant lui
SWIPE_DURATION = 700                  # ms (lent = pas d'effet d'inertie)
SWIPE_MAX = 8                         # glissements max dans un sens avant de repartir dans l'autre
SWIPE_END_DIFF = 2.0                  # difference d'image < a ca apres un glissement = fin de la liste
SWIPE_STILL = 2                       # glissements consecutifs sans mouvement avant de conclure "bout de la liste"
MAX_ENDS = 4                          # bouts atteints (2 = un aller-retour complet) avant d'abandonner. JAMAIS de touche Retour.
PORTS = ["127.0.0.1:16384", "127.0.0.1:7555", "127.0.0.1:5555", "127.0.0.1:62001"]

# Config partagee avec l'assistant du zip mha-bot (config.json a cote du script)
_CFG = BASE / "config.json"
if _CFG.exists():
    try:
        _c = json.loads(_CFG.read_text(encoding="utf-8"))
        ADB = _c.get("adb", ADB)
        DEVICE = _c.get("device", DEVICE)
        if _c.get("hero_chars"):
            CHARS = [c for c in _c["hero_chars"] if isinstance(c, str)]
    except Exception:
        pass

if "--chars" in sys.argv:                 # python auto_hero_trial.py --chars deku,denki
    try:
        CHARS = [c.strip().lower() for c in sys.argv[sys.argv.index("--chars") + 1].split(",") if c.strip()]
    except IndexError:
        pass
CHARS = list(dict.fromkeys(CHARS))        # sans doublons, ordre conserve
MAX_BATTLES = 2 * max(1, len(CHARS))      # securite : 2 essais par perso

# nom : (fichier, zone utile (x0,y0,x1,y1) en proportion, point de tap (x,y) en proportion de l'image complete, options)
SPEC = {
    # le point rouge de notification (en haut) disparait parfois : on ignore le haut de l'icone
    "ua":           dict(file="ua.png", crop=(0, 0.30, 1, 1)),
    "solo":         dict(file="solo.png"),
    "herotrial":    dict(file="herotrial.png"),
    "plus":         dict(file="plus.png"),
    # le BP / niveau changent : on ne reconnait que le portrait, puis on tape sur le bouton Deploy
    "am":           dict(file="am.png", crop=(0, 0, 0.28, 1), tap=(0.782, 0.581)),
    # on repere le mot "Attempts", le compteur "(0/2)" est compare a part
    "attempt":      dict(file="attempt.png", crop=(0, 0, 0.70, 1)),
    "challenge":    dict(file="challenge.png"),
    "auto":         dict(file="auto.png", crop=(0.10, 0.28, 0.88, 0.70), mask_white=True, thr=AUTO_THRESHOLD),
    "auto_on":      dict(file="auto_on.png", crop=(0.10, 0.28, 0.88, 0.70), mask_white=True, thr=AUTO_THRESHOLD),
    "again":        dict(file="again.png"),
    "insufficient": dict(file="insufficient.png"),
    "exit":         dict(file="exit.png"),
    "continue":     dict(file="continue.png"),
    "close":        dict(file="close.png"),
}
for _c_name in CHARS:   # le bas de la carte contient "(2/2)" qui change : on l'ignore
    SPEC[_c_name] = dict(file=f"{_c_name}.png", crop=(0, 0, 1, 0.84), thr=CARD_THRESHOLD)
OPTIONAL = {"auto_on", "continue", "close"}
# chiffres du compteur "(0/2)" dans attempt.png (pixels) : 1er chiffre (0, 1 ou 2) et chiffre de droite (toujours 2)
DIGIT1 = (377, 10, 395, 38)
DIGIT2 = (399, 10, 417, 38)


class Done(Exception):
    pass


# ======================= ADB =======================
def _resolve_adb(path):
    for cand in (path, shutil.which("adb"), BASE / "platform-tools" / "adb.exe",
                 Path(r"C:\platform-tools\adb.exe")):
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
    if not devs:                      # l'emulateur n'est pas connecte : on tente les ports connus
        for p in PORTS:
            try:
                subprocess.run([ADB, "connect", p], capture_output=True, timeout=6)
            except Exception:
                pass
        devs = _devices()
    if not devs:
        raise SystemExit("Aucun emulateur detecte. Lance-le (jeu ouvert) puis refais 'adb devices'.")
    if DEVICE not in devs:            # config.json perimee ou vide
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


def screenshot():
    global DEVICE
    err = None
    for _ in range(5):
        try:
            png = adb("exec-out", "screencap", "-p", binary=True)
            img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                journal.frame(img)             # boite noire (dernieres captures, en cas d'incident)
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


# ======================= TEMPLATES =======================
class Tpl:
    def __init__(self, path, crop=(0, 0, 1, 1), tap=(0.5, 0.5), mask_white=False, thr=THRESHOLD):
        full = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if full is None:
            raise FileNotFoundError(path)
        H, W = full.shape[:2]
        x0, y0, x1, y1 = int(crop[0] * W), int(crop[1] * H), int(crop[2] * W), int(crop[3] * H)
        self.full, self.size, self.off = full, (W, H), (x0, y0)
        self.name = f"{Path(path).parent.name}/{Path(path).name}"     # pour la detection de derive (derive.py)
        self.tap, self.thr, self.mask_white = tap, thr, mask_white
        self.img = self._prep(full[y0:y1, x0:x1])
        fastmatch.prepare(self)                 # reconnaissance rapide (voir fastmatch.py), comme les autres modes

    def _prep(self, img):
        # AUTO : on compare uniquement les pixels blancs (le decor derriere change d'un combat a l'autre)
        return cv2.inRange(img, (200, 200, 200), (255, 255, 255)) if self.mask_white else img

    prep = _prep                                # nom attendu par fastmatch

    def best(self, screen, thr=None):
        """Meme score que la comparaison couleur sur toute la capture, ~10x plus vite (fastmatch).
        thr : seuil plus bas que celui de l'image (find(screen, 0.80)) -> fastmatch verifie a partir de ce seuil-la."""
        base = self.thr
        if thr is not None and thr < base:
            self.thr = thr
        try:
            return fastmatch.best(self, screen)
        finally:
            self.thr = base

    def find(self, screen, thr=None):
        """-> (x_tap, y_tap, score, (x_haut_gauche, y_haut_gauche)) ou None"""
        score, loc = self.best(screen, thr)
        if score < (thr or self.thr):
            return None
        W, H = self.size
        tlx, tly = loc[0] - self.off[0], loc[1] - self.off[1]
        return tlx + self.tap[0] * W, tly + self.tap[1] * H, score, (tlx, tly)


T = {}


def load_templates(strict=True):
    global PICK, SLOT, GATE
    missing = []
    try:        # le perso qui combat n'est plus une image fixe (am.png) : voir heroes.py / templates_chars/
        PICK = heroes.Picker(heroes.chosen_name(), adb, log, fail=Done)
        T["am"] = heroes.RowTpl(PICK, with_pos=True)
    except FileNotFoundError as e:
        missing.append(str(e))
    for name, spec in SPEC.items():
        if name == "am":
            continue
        p = TPL_DIR / spec["file"]
        if not p.exists():
            if name not in OPTIONAL:
                missing.append(spec["file"])
            continue
        kw = {k: v for k, v in spec.items() if k != "file"}
        T[name] = Tpl(p, **kw)
    SLOT = heroes.SlotReader()
    if PICK:
        VERIFY = "--no-verify" not in sys.argv and heroes.chosen_explicit()
        if not heroes.chosen_explicit():      # aucun perso choisi : on garde celui qui est deja place, on ne le remplace pas
            log("aucun perso choisi : je garde celui qui est deja place "
                "(choisis-en un dans l'assistant, sur Discord, ou avec --hero, pour que le bot le change)")
        GATE = heroes.SlotGate(SLOT, PICK.name, log, Done, enabled=VERIFY)
    if missing and strict:
        raise SystemExit("Images manquantes dans templates_hero/ : " + ", ".join(missing))
    return missing


def _digit_mask(img, box, thr=150):
    x0, y0, x1, y1 = box
    return cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY) > thr


def _best_iou(p, q, tol=2):
    best = 0.0
    for dy in range(-tol, tol + 1):
        for dx in range(-tol, tol + 1):
            s = np.roll(np.roll(q, dy, 0), dx, 1)
            best = max(best, float((p & s).sum()) / max(1.0, float((p | s).sum())))
    return best


def attempts_info(screen):
    """-> (etat, sim_0, sim_2) ; etat : True = compteur '(0/2)' (plus d'essai), False = il reste des essais
    ((1/2) ou (2/2)), None = bandeau 'Attempts' illisible.
    On compare le 1er chiffre au '0' de attempt.png ET au '2' de droite : un '(2/2)' ressemble a '(0/2)'
    pour une comparaison globale, mais son 1er chiffre est identique au 2e."""
    t = T["attempt"]
    hit = t.find(screen, 0.80)
    if not hit:
        return None, 0.0, 0.0
    tlx, tly = hit[3]
    H, W = screen.shape[:2]

    def shift(b):
        return (tlx + b[0], tly + b[1], tlx + b[2], tly + b[3])

    b1, b2 = shift(DIGIT1), shift(DIGIT2)
    if min(b1[0], b1[1], b2[0], b2[1]) < 0 or max(b1[2], b2[2]) > W or max(b1[3], b2[3]) > H:
        return None, 0.0, 0.0
    d1, d2 = _digit_mask(screen, b1), _digit_mask(screen, b2)
    if d1.sum() < 15:
        return None, 0.0, 0.0
    ref0 = _digit_mask(t.full, DIGIT1)
    i0 = _best_iou(d1, ref0)
    i2 = _best_iou(d1, d2) if d2.sum() >= 15 else 0.0
    return (i0 >= ATTEMPT_ZERO and i0 > i2 + ATTEMPT_MARGIN), i0, i2


def attempts_state(screen):
    state, i0, i2 = attempts_info(screen)
    if state is not None:
        print(f"    compteur d'essais : ressemble a '0' {i0:.2f} / a '2' {i2:.2f} -> "
              f"{'0/2 (plus d essai)' if state else 'il reste des essais'}")
    return state


# ======================= BOT =======================
S = SimpleNamespace()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def set_stage(name):
    S.stage, S.t, S.progress = name, time.time(), time.time()
    if name == "wait_card":
        reset_scan()
    if name == "wait_pick" and PICK:
        PICK.reset()
    log(f"-> etape : {name}")


def do_tap(hit, pause=SETTLE):
    tap(hit[0], hit[1])
    S.progress = time.time()
    time.sleep(pause)


def detect_stage(screen):
    """Devine l'etape a partir de l'ecran (du plus specifique au plus general)."""
    if T["again"].find(screen) or T["insufficient"].find(screen):
        return "battle"
    if T["attempt"].find(screen, 0.80) or T["challenge"].find(screen):
        return "wait_check"
    if T["am"].find(screen) or PICK.on_list(screen):
        return "wait_pick"
    if T["plus"].find(screen):
        return "wait_plus"
    if any(T[c].find(screen) for c in CHARS):
        return "wait_card"
    if "herotrial" in T and T["herotrial"].find(screen):
        return "wait_hero"
    if T["solo"].find(screen):
        return "wait_solo"
    if T["ua"].find(screen):
        return "wait_ua"
    return None


def h_ua(screen):
    hit = T["ua"].find(screen)
    if hit:
        do_tap(hit)
        set_stage("wait_solo")
        return True


def h_solo(screen):
    hit = T["solo"].find(screen)
    if hit:
        do_tap(hit)
        set_stage("wait_hero")
        return True


def h_hero(screen):
    hit = T["herotrial"].find(screen)
    if hit:
        do_tap(hit)
        set_stage("wait_card")
        return True


def card_sig(screen):
    """Signature de la zone des cartes (pour savoir si la liste a bouge apres un glissement)."""
    h = screen.shape[0]
    band = cv2.cvtColor(screen[int(0.15 * h):int(0.85 * h)], cv2.COLOR_BGR2GRAY)
    return cv2.resize(band, (96, 48), interpolation=cv2.INTER_AREA).astype(np.float32)


def reset_scan():
    S.card_miss, S.dir, S.swipes, S.ends, S.sig, S.still = 0, -1, 0, 0, None, 0   # dir -1 = on glisse vers la gauche


def swipe_zone(screen):
    """Zone horizontale ou glisser (x_min, x_max en pixels) : on evite le panneau lateral s'il est affiche."""
    h, w = screen.shape[:2]
    xmin, xmax = SWIPE_X_MIN * w, SWIPE_X_MAX * w
    lefts = []
    for n in ("plus", "attempt", "challenge", "am"):
        hit = T[n].find(screen, 0.80) if n in T else None
        if hit:
            lefts.append(hit[3][0])
    if lefts:
        xmax = min(xmax, min(lefts) - 0.02 * w)
    return xmin, max(xmax, xmin + 0.2 * w)


def do_swipe(screen, direction):
    """direction -1 : le doigt va vers la gauche (la liste avance vers la droite) ; +1 : l'inverse."""
    h, w = screen.shape[:2]
    xmin, xmax = swipe_zone(screen)
    card_w = T[CHARS[0]].size[0]
    dist = min(2.5 * card_w, 0.85 * (xmax - xmin))
    cx = (xmin + xmax) / 2
    x1, x2 = cx - direction * dist / 2, cx + direction * dist / 2
    y = SWIPE_Y * h
    log(f"glissement {int(x1)} -> {int(x2)} (y={int(y)}, zone {int(xmin)}-{int(xmax)} sur {w})")
    adb("shell", "input", "swipe", str(int(x1)), str(int(y)), str(int(x2)), str(int(y)), str(SWIPE_DURATION))


def swipe_list(screen):
    do_swipe(screen, S.dir)
    S.sig = card_sig(screen)
    S.swipes += 1
    S.progress = time.time()
    time.sleep(SETTLE + 0.5)


def h_card(screen):
    pending = [c for c in CHARS if c not in S.done]
    if not pending:
        raise Done("Tous les persos sont termines (" + ", ".join(CHARS) + "). Fini !")
    for c in pending:
        hit = T[c].find(screen)
        if hit:
            reset_scan()
            S.cur = c
            GATE.reset()
            log(f"perso : {c} ({hit[2]:.2f})")
            do_tap(hit)
            set_stage("wait_plus")
            return True
    # aucune carte visible : on fait defiler la liste (gauche <-> droite) jusqu'a la trouver
    if S.sig is not None:                          # on vient de glisser : est-ce que la liste a bouge ?
        diff = float(np.abs(card_sig(screen) - S.sig).mean())
        S.sig = None
        S.still = S.still + 1 if diff < SWIPE_END_DIFF else 0
        if S.still >= SWIPE_STILL or S.swipes >= SWIPE_MAX:
            S.ends += 1
            S.dir, S.swipes, S.still = -S.dir, 0, 0
            log("bout de la liste atteint, on repart dans l'autre sens")
    if S.ends >= MAX_ENDS:                         # la liste a ete parcourue dans les deux sens sans trouver la carte
        raise Done("Cartes introuvables (reste : " + ", ".join(pending) + "). Le bot ne touche pas a Retour : "
                   "verifie les images des cartes (templates_hero/), ou regle SWIPE_Y si les glissements ne bougent pas la liste.")
    S.card_miss += 1
    if S.card_miss < 2:                            # 1re fois : on laisse l'ecran finir de s'afficher
        return False
    log(f"carte introuvable : on fait defiler la liste ({'vers la gauche' if S.dir < 0 else 'vers la droite'})")
    swipe_list(screen)
    S.card_miss = 1                                # prochain echec -> on glisse tout de suite
    return True


def panel_visible(screen):
    return bool(T["attempt"].find(screen, 0.80) or T["challenge"].find(screen))


def gate_step(screen):
    """Verifie que le perso place est celui choisi. -> True si on peut continuer ; sinon False (attente, ou on a tape
    sur l'emplacement pour changer de perso : l'etape passe alors a wait_pick)."""
    ch = T["challenge"].find(screen, 0.80)
    state, pt = GATE.check(screen, (ch[0], ch[1]) if ch else None)
    if state == "ok":
        return True
    if state == "swap":
        do_tap(pt, pause=SETTLE + 0.5)
        set_stage("wait_pick")
    return False


def h_plus(screen):
    hit = T["plus"].find(screen)
    if hit:
        log("emplacement vide -> plus")
        ch = T["challenge"].find(screen, 0.80)
        GATE.note_plus((hit[0], hit[1]), (ch[0], ch[1]) if ch else None)
        do_tap(hit)
        set_stage("wait_pick")
        return True
    if not panel_visible(screen):
        return False
    # pas de "+" : un perso est DEJA place. Est-ce celui qu'on veut (choisi dans l'assistant) ?
    if gate_step(screen):
        set_stage("wait_check")
        return True
    return S.stage == "wait_pick"


def h_pick(screen):
    hit = T["am"].find(screen)
    if hit:
        log(f"choix du perso '{heroes.pretty(PICK.name)}' ({hit[2]:.2f}) -> Deploy")
        PICK.reset()
        GATE.done()
        do_tap(hit)
        set_stage("wait_check")
        return True
    if PICK.step(screen):          # liste ouverte mais perso absent de l'ecran : defilement (bas puis haut)
        S.progress = time.time()
        return True
    # on a tape sur le perso deja place pour le changer, mais la liste ne s'ouvre pas
    if GATE.swap_t and GATE.swap_stalled(PICK.on_list(screen), 8) and panel_visible(screen):
        raise Done("Le clic sur l'emplacement du perso place n'a PAS ouvert la liste des persos. Change-le a la main "
                   "(enleve ou remplace le perso), puis relance. Dis-moi comment on change de perso dans le jeu pour que je l'ajoute.")


def start_battle():
    S.battles += 1
    if S.battles > MAX_BATTLES:
        raise Done(f"Securite : {MAX_BATTLES} combats lances, arret.")
    S.no_att, S.b_start, S.auto_ok, S.auto_taps = False, time.time(), False, 0
    S.auto_miss, S.auto_warned, S.auto_tap_t = 0, False, 0
    log(f"combat {S.battles}/{MAX_BATTLES} ({S.cur})")


def h_check(screen):
    if not GATE.ok and not T["plus"].find(screen):        # arrive ici sans passer par h_plus (re-synchronisation...)
        if not panel_visible(screen):
            return False
        if not gate_step(screen):
            return S.stage == "wait_pick"
    state = attempts_state(screen)
    if state is True:
        S.done.add(S.cur)
        log(f"{S.cur} : 0/2 -> plus d'essai, on passe au suivant")
        set_stage("wait_card")
        return False
    hit = T["challenge"].find(screen)
    if hit and (state is False or (state is None and time.time() - S.t > 6)):
        if state is None:
            log("(!) compteur d'essais non verifie, Challenge quand meme")
        log(f"{S.cur} : essais disponibles -> Challenge")
        do_tap(hit)
        start_battle()
        set_stage("battle")
        return True


def h_battle(screen):
    now = time.time()
    if not S.no_att and T["insufficient"].find(screen):
        S.no_att = True
        log("Insufficient chances : plus d'essai")
    if S.no_att:
        hit = T["exit"].find(screen)
        if hit:
            log(f"{S.cur} termine -> Exit Stage")
            S.done.add(S.cur)
            S.no_att = False
            do_tap(hit, pause=2.5)
            set_stage("wait_card")
            return True
        return False
    hit = T["again"].find(screen)
    if hit:
        log("Challenge Again")
        do_tap(hit, pause=4.0)
        start_battle()
        return True
    for opt in ("continue", "close"):
        if opt in T and T[opt].find(screen):
            do_tap(T[opt].find(screen))
            return True
    # AUTO : auto.png = bouton AUTO DESACTIVE -> on clique dessus une seule fois par combat
    if now - S.b_start > 4 and not S.auto_ok:
        if "auto_on" in T and T["auto_on"].find(screen):
            S.auto_ok = True
            log("AUTO deja actif")
            return False
        off = T["auto"].find(screen)
        if off:
            if S.auto_taps < 1:
                log(f"AUTO desactive -> on l'active ({off[2]:.2f})")
                do_tap(off)
                S.auto_taps += 1
                S.auto_tap_t = time.time()
                return True
            if now - S.auto_tap_t > 4 and not S.auto_warned:
                S.auto_warned = True
                log("(!) auto.png est toujours visible apres le clic : si AUTO est bien actif, ajoute auto_on.png")
        else:
            S.auto_miss += 1
            if S.auto_taps or S.auto_miss >= 3:
                S.auto_ok = True
                log("AUTO actif" if S.auto_taps else "AUTO deja actif (bouton OFF non detecte)")
    return False


HANDLERS = {"wait_ua": h_ua, "wait_solo": h_solo, "wait_hero": h_hero, "wait_card": h_card,
            "wait_plus": h_plus, "wait_pick": h_pick, "wait_check": h_check, "battle": h_battle}


def run_test():
    fastmatch.FAST = False                 # --test : scores exacts (comparaison couleur partout)
    load_templates(strict=False)
    screen = screenshot()
    print(f"Capture : {screen.shape[1]}x{screen.shape[0]}")
    print(f"{'image':<14}{'score':>7}  {'seuil':>6}  position")
    for name, t in T.items():
        score, loc = t.best(screen)
        print(f"{name:<14}{score:>7.2f}  {t.thr:>6.2f}  {'TROUVE ' + str(loc) if score >= t.thr else '-'}")
    for name, spec in SPEC.items():
        if name not in T and name not in OPTIONAL:
            print(f"{name:<14}  fichier manquant : {spec['file']}")
    if "attempt" in T:
        st, i0, i2 = attempts_info(screen)
        txt = {True: "0/2 (plus d'essai)", False: "il reste des essais (1/2 ou 2/2)", None: "bandeau non visible"}[st]
        print(f"etat des essais : {txt}  [ressemble a '0' {i0:.2f} | a '2' {i2:.2f}]")
    if SLOT and "plus" in T and PICK:
        hit = T["plus"].find(screen)
        if hit:
            print(f"emplacement du perso : VIDE (+) en {tuple(int(v) for v in hit[3])}")
        else:
            name, sc, pt, scores = SLOT.identify(screen, PICK.name)
            det = ", ".join(f"{k} {v:.2f}" for k, v in sorted(scores.items(), key=lambda kv: -kv[1])[:4]) or "aucune reference"
            where = f" en ({int(pt[0])}, {int(pt[1])})" if pt else ""
            print(f"emplacement du perso : {('PLACE = ' + name + where) if name else 'aucun perso reconnu'}  [{det}]  (voulu : {PICK.name})")
    print("etape deduite   :", detect_stage(screen) if len(T) == len([n for n in SPEC if n not in OPTIONAL]) else "(images manquantes)")


def run_swipe_test():
    """python auto_hero_trial.py --swipe left|right : fait UN glissement pour verifier le reglage."""
    load_templates(strict=False)
    direction = -1 if "left" in sys.argv else 1
    screen = screenshot()
    do_swipe(screen, direction)
    time.sleep(SETTLE)
    diff = float(np.abs(card_sig(screenshot()) - card_sig(screen)).mean())
    print(f"difference avant/apres : {diff:.2f} (seuil de fin de liste : {SWIPE_END_DIFF}) -> "
          f"{'la liste a bouge' if diff >= SWIPE_END_DIFF else 'la liste N a PAS bouge'}")


def main():
    if "--list" in sys.argv:
        print("Persos disponibles :", ", ".join(available_chars()))
        print("Persos selectionnes :", ", ".join(CHARS) or "(aucun)")
        return
    if not CHARS:
        raise SystemExit("Aucun perso selectionne (config.json 'hero_chars' ou --chars a,b).")
    unknown = [c for c in CHARS if not (TPL_DIR / f"{c}.png").exists()]
    if unknown:
        raise SystemExit("Carte(s) introuvable(s) dans templates_hero/ : " + ", ".join(f"{c}.png" for c in unknown))
    print("Persos a farmer :", ", ".join(CHARS))
    print("Perso qui combat :", heroes.pretty(heroes.chosen_name()), "(--hero <nom> ou assistant ; liste :",
          ", ".join(heroes.available()) + ")")
    init_adb()
    if "--test" in sys.argv:
        return run_test()
    if "--swipe" in sys.argv:
        return run_swipe_test()
    load_templates()
    if "--no-home" not in sys.argv:
        nav.Home(screenshot, tap, log, back=lambda: adb("shell", "input", "keyevent", "4")).go()          # retour au menu principal, d'ou qu'on parte
    RF = None
    if refill.enabled():                  # recharge de stamina par code (option) : voir refill.py
        RF = refill.Refill(screenshot, tap, adb, log, Done)
        miss = RF.missing()
        if miss:
            log("(!) Recharge de stamina desactivee, images manquantes : " + ", ".join(miss))
            RF = None
        else:
            log(f"Recharge de stamina : ACTIVEE (code {RF.code} x{RF.count})")
    REPRISE = nav.Reprise(nav.Home(screenshot, tap, log, back=lambda: adb("shell", "input", "keyevent", "4")), log)   # ecran inconnu 60 s -> menu + reprise
    WD = watchdog.make(screenshot, tap, adb, log, Done)      # chien de garde (option) : maintenance / jeu bloque -> relance
    S.done, S.cur, S.no_att, S.battles = set(), None, False, 0
    reset_scan()
    S.b_start, S.auto_ok, S.auto_taps, S.auto_miss, S.auto_warned, S.auto_tap_t = 0, False, 0, 0, False, 0
    S.stage, S.t, S.progress = None, time.time(), time.time()
    print("Demarrage. Ctrl+C pour arreter. (conseil : commence depuis le menu principal)")

    while True:
        screen = screenshot()
        now = time.time()
        case = RF.detect(screen) if RF else None
        if case:                          # plus de stamina : on recharge puis on se re-synchronise
            if case != "insustam" and S.stage == "battle" and now - S.b_start < 15:
                S.battles = max(0, S.battles - 1)
            RF.run(case, screen)
            S.no_att, S.stage, S.progress = False, None, time.time()
            GATE.reset()
            continue
        if WD:
            WD.last_progress = max(WD.last_progress, S.progress)      # toute action du bot = du progres
            if WD.poll(screen, S.stage == "battle"):
                S.no_att, S.stage, S.progress = False, None, time.time()
                GATE.reset()
                continue
        if S.stage is None:
            guess = detect_stage(screen)
            set_stage(guess or "wait_ua")
        if (S.stage != "battle" and REPRISE.due(now - S.progress)
                and not any(h for h in fastmatch.find_many(T.items(), screen).values())   # aucune image reconnue
                and REPRISE.run()):
            S.no_att, S.stage, S.progress = False, None, time.time()     # comme apres le garde-chien
            GATE.reset()
            continue
        if now - S.progress > IDLE_LIMIT and not WD:
            raise Done(f"Plus aucune action depuis {IDLE_LIMIT}s : arret (ecran inattendu ?).")
        acted = HANDLERS[S.stage](screen)
        if not acted:
            if S.stage not in ("battle", "wait_card") and time.time() - S.t > STAGE_TIMEOUT:
                guess = detect_stage(screen)
                if guess and guess != S.stage:
                    log(f"re-synchronisation : {S.stage} -> {guess}")
                    set_stage(guess)
                else:
                    log(f"toujours en attente ({S.stage})...")
                    S.t = time.time()
            time.sleep(LOOP_DELAY)


if __name__ == "__main__":
    import journal
    journal.start("herotrial")                 # journal de session dans logs/ (garde les 10 derniers)
    journal.run(main, Done)               # meme affichage qu'avant + incident (capture, boite noire) si arret anormal
