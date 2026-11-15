"""
AGENCY ASSESSMENT (AA) - farm automatique.

Chemin (pilote par ce qui est reconnu a l'ecran) :
    menu principal -> Co-op (coop) -> Agency Assessment (aa) -> Join Team (jointeam) -> Create Team (createteam)
    LOBBY :
      1. "Insert Permit" (insertpermit) : le permit apparait (permit) avec son FLOOR -> le bot lit le floor
      2. si le floor a atteint celui que tu as fixe : RESET du permit
            Recruit (recruit) -> My Permit (mypermit) -> Reset to Floor 1 (resetpermit) -> Confirm (confirmreset)
      3. Bots (bots) : il ajoute les 2 bots choisis avec "Add" (add), puis ferme avec la croix (closebots)
      4. (facultatif) changement de perso selon la stamina : voir "PERSOS" plus bas
      5. Deploy (deploy)
    COMBAT : il active AUTO et attend "Card Flip" (cardflip) -> il retourne les cartes -> Return (return) -> retour au lobby.

Utilisation :
    python auto_aa.py                         # reglages de config.json
    python auto_aa.py --runs 6                # 6 combats puis arret
    python auto_aa.py --bots deku,bakugo      # les 2 bots (deku bakugo shoto momo ochaco eijiro fumikage denki)
    python auto_aa.py --reset-floor 8         # reset du permit des que le floor atteint 8 (0 = jamais)
    python auto_aa.py --floor 4               # floor actuel, si le bot n'arrive pas a le lire
    python auto_aa.py --test                  # UNE capture : score de chaque image + floor lu
    python auto_aa.py --capture               # enregistre une capture plein ecran dans debug/ (pour regler / montrer un ecran)
    Options communes : --no-home, --watchdog / --no-watchdog

LE FLOOR : le bot le lit sur le permit (chiffre jaune de l'hexagone). Il ne connait au depart que le chiffre de ta capture
(4). Pour les autres, il APPREND tout seul : apres un reset il sait qu'il est au floor 1, puis +1 a chaque combat gagne, et il
enregistre le dessin de chaque chiffre dans templates_aa/floors/<n>.png. Ensuite il les lit directement.
S'il ne sait pas ou il en est (1er lancement sur un floor inconnu), donne-le avec --floor (ou dans l'assistant).

PERSOS (stamina 2/2 -> 1/2 -> 0/2) : le menu des persos s'ouvre en cliquant sur TON perso dans le lobby. Le bot ne peut pas
deviner ou il est : regle "aa_char_pos": [x, y] dans config.json (fraction de l'ecran, ex. [0.50, 0.55]). Il ouvre alors le
menu avant chaque combat, prend le 1er perso qui a encore de la stamina (2/2 puis 1/2) en balayant la liste horizontale, et
s'arrete quand plus aucun n'en a. Sans ce reglage, il ne touche pas au perso et s'arrete quand Deploy ne lance plus rien.

Config.json : "aa_runs", "aa_bots", "aa_reset_floor", "aa_floor", "aa_cards" (cartes a retourner, defaut 3),
              "aa_char_pos", "aa_icon_dy" (distance en px entre la barre de stamina et l'icone du perso, defaut 95).
Images : dossier templates_aa/.
"""
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import aa_digits
import auto_nightops as nt
import fastmatch
import nav
import watchdog
from auto_nightops import Done, Tpl, log

BASE = Path(__file__).resolve().parent
TDIR = BASE / "templates_aa"
ALL_BOTS = ("deku", "bakugo", "shoto", "momo", "ochaco", "eijiro", "fumikage", "denki")
BOT_NAMES = {"deku": "Izuku Midoriya", "bakugo": "Katsuki Bakugo", "shoto": "Shoto Todoroki", "momo": "Momo Yaoyorozu",
             "ochaco": "Ochaco Uraraka", "eijiro": "Eijiro Kirishima", "fumikage": "Fumikage Tokoyami", "denki": "Denki Kaminari"}

MAX_RUNS = 10
BOTS = ["deku", "bakugo"]
RESET_FLOOR = 0                # 0 = jamais de reset
FLOOR = None                   # floor actuel s'il est connu
CARDS = 3
CHAR_POS = None
DEFAULT_CHAR_POS = (0.205, 0.42)   # ton perso (modele 3D) dans le lobby, en fractions de l'ecran ; "aa_char_pos" le remplace
RESERVED = []                  # persos gardes pour les hauts floors (images dans templates_aa/chars/<nom>.png)
RESERVE_FLOOR = 10             # a partir de ce floor on joue les persos reserves ; en dessous on n'y touche pas
ICON_DY = 75                   # distance entre la barre de stamina et le centre de l'icone du perso (menu Battle Settings)
try:
    _c = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    MAX_RUNS = int(_c.get("aa_runs", MAX_RUNS) or MAX_RUNS)
    BOTS = [b for b in (_c.get("aa_bots") or BOTS) if b in ALL_BOTS][:2]
    RESET_FLOOR = int(_c.get("aa_reset_floor", RESET_FLOOR) or 0)
    FLOOR = int(_c["aa_floor"]) if _c.get("aa_floor") else None
    CARDS = max(1, min(5, int(_c.get("aa_cards", CARDS) or CARDS)))
    CHAR_POS = tuple(float(v) for v in _c["aa_char_pos"]) if _c.get("aa_char_pos") else None
    ICON_DY = int(_c.get("aa_icon_dy", ICON_DY) or ICON_DY)
    RESERVED = [str(n).lower() for n in (_c.get("aa_reserved") or [])]
    RESERVE_FLOOR = max(1, int(_c.get("aa_reserve_floor", RESERVE_FLOOR) or RESERVE_FLOOR))
except Exception:
    pass


from commun import arg as _arg                   # valeur d'une option de la ligne de commande (commun.py)


MAX_RUNS = max(1, _arg("--runs") or MAX_RUNS)
if _arg("--bots", str) is not None:
    BOTS = [b.strip().lower() for b in _arg("--bots", str).split(",") if b.strip()]
    bad = [b for b in BOTS if b not in ALL_BOTS]
    if bad or len(BOTS) > 2 or len(set(BOTS)) != len(BOTS):
        raise SystemExit(f"Bots : 2 maximum, differents, parmi {', '.join(ALL_BOTS)} (recu : {', '.join(BOTS) or 'rien'}).")
if _arg("--reset-floor") is not None:
    RESET_FLOOR = max(0, _arg("--reset-floor"))
if _arg("--floor") is not None:
    FLOOR = max(1, _arg("--floor"))

if _arg("--reserve", str) is not None:
    RESERVED = [n.strip().lower() for n in _arg("--reserve", str).split(",") if n.strip() and n.strip().lower() != "aucun"]
if _arg("--reserve-floor") is not None:
    RESERVE_FLOOR = max(1, _arg("--reserve-floor"))

BTN = (0.15, 0.15, 0.85, 0.85)                 # boutons jaunes : on compare le TEXTE, pas le fond (tous identiques)
SPEC = {                                       # du plus avance au plus ancien
    "revive":       dict(cd=2.5, crop=(0.10, 0.0, 0.62, 0.85), thr=0.80),      # "Free this time" + "Revive Now" (sans le compteur 5/5)
    "return":       dict(cd=3.0, crop=BTN, thr=0.80),
    "cards":        dict(cd=2.0, crop=(0.445, 0.263, 0.61, 0.33), thr=0.75),   # cardflip2.png : "Each player can flip"
    "cardflip":     dict(cd=3.0, crop=BTN, thr=0.80),
    "auto":         dict(cd=8.0, crop=(0.10, 0.28, 0.88, 0.70), mask_white=True, thr=nt.AUTO_THRESHOLD),
    "confirmreset": dict(cd=3.0, crop=BTN, thr=0.80),
    "resetpermit":  dict(cd=3.0, crop=BTN, thr=0.80),
    "mypermit":     dict(cd=3.0, crop=(0.2, 0.2, 0.9, 0.8), thr=0.80),
    "closebots":    dict(cd=2.0, crop=(0.18, 0.12, 0.78, 0.88), thr=0.72),
    "closereset":   dict(cd=2.0, crop=(0.05, 0.10, 0.72, 0.95), thr=0.75),     # croix de la page My Permit, apres le reset
    "add":          dict(cd=1.5, crop=BTN, thr=0.80),
    "deploy":       dict(cd=6.0, crop=(0.25, 0.25, 0.75, 0.75), thr=0.72),     # sans les etoiles animees
    "permit":       dict(cd=3.0, crop=(0.36, 0.04, 1.0, 0.30), thr=0.78),      # titre "Agency Assessment" + roue dentee
    "insertpermit": dict(cd=3.0, crop=(0.05, 0.15, 0.85, 0.85), thr=0.72),
    "bots":         dict(cd=3.0, crop=BTN, thr=0.80),
    "botsgris":     dict(cd=3.0, crop=BTN, thr=0.85),                          # Bots grise = bots deja selectionnes
    "recruit":      dict(cd=3.0, crop=(0.05, 0.05, 0.95, 0.95), thr=0.78),
    "createteam":   dict(cd=3.0, crop=BTN, thr=0.80),
    "myteam":       dict(cd=3.0, crop=BTN, thr=0.80),                          # remplace Create Team quand l'equipe existe deja
    "jointeam":     dict(cd=3.0, crop=BTN, thr=0.80),
    "aa":           dict(cd=3.0, crop=(0.0, 0.0, 1.0, 0.60), thr=0.78),        # sans la ligne "Floor 4 Lv. 70"
    "coop":         dict(cd=3.0, crop=(0.1, 0.1, 0.9, 0.75), thr=0.78),
    "ua":           dict(cd=3.0, crop=(0, 0.30, 1, 1)),                        # menu principal : icone U.A. (Solo / Co-op)
}
OPTIONAL = {"botsgris", "myteam", "revive", "closereset"}                         # images facultatives
FILES = {"revive": "revivefree.png", "cards": "cardflip2.png", "auto": BASE / "templates_nightops" / "auto.png",
         "ua": BASE / "templates_nightops" / "ua.png"}
# cardflip2.png (1574x532) : centre de l'ancre "Each player can flip" et centres des 5 cartes
CARD_ANCHOR = (830.5, 157.5)
CARD_XY = [(245, 350), (508, 350), (770, 350), (1033, 350), (1296, 350)]
# zone du chiffre du floor dans permit.png (fractions de l'image complete)
FLOOR_BOX = (0.137, 0.275, 0.31, 0.51)

T = {}
BOT_T = {}
ST = {}                                         # images des barres de stamina
CH = {}                                         # visages des persos reserves (templates_aa/chars/<nom>.png)
ICONS = {}                                      # visages de tous les persos connus (sert a trouver ton perso dans le lobby)
FACE = (0.14, 0.12, 0.86, 0.60)                 # partie comparee d'une icone : le visage (sans cadre, sans "Lv. 90")
ICON_W = 135                                    # largeur normale d'une icone a l'ecran ; les images plus petites sont agrandies
NOSTAM = []                                     # image "Insufficient stamina"


def load_templates(strict=True):
    missing = []
    for name, spec in SPEC.items():
        p = FILES.get(name, f"{name}.png")
        p = p if isinstance(p, Path) else TDIR / p
        if not p.exists():
            if name not in OPTIONAL:
                missing.append(str(p.relative_to(BASE)))
            continue
        T[name] = Tpl(p, **spec)
    for b in ALL_BOTS:
        p = TDIR / f"{b}.png"
        if p.exists():
            BOT_T[b] = Tpl(p, crop=(0.12, 0.06, 0.88, 0.72), thr=0.80)      # le visage, sans le nom
        elif b in BOTS:
            missing.append(f"templates_aa/{b}.png")
    for k in ("charstamina2", "charstamina1", "charstamina0"):
        p = TDIR / f"{k}.png"
        if p.exists():
            ST[k] = cv2.imread(str(p))
    for p in sorted((TDIR / "chars").glob("*.png")) if (TDIR / "chars").is_dir() else []:
        img = cv2.imread(str(p))
        if img is None:
            continue
        if img.shape[1] < 0.8 * ICON_W:                 # icone decoupee en petit : on la remet a la taille de l'ecran
            k = ICON_W / img.shape[1]
            img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
        h, w = img.shape[:2]
        ICONS[p.stem.lower()] = img[int(FACE[1] * h):int(FACE[3] * h), int(FACE[0] * w):int(FACE[2] * w)]
    for n in RESERVED:
        if n in ICONS:
            CH[n] = ICONS[n]
        else:
            missing.append(f"templates_aa/chars/{n}.png (perso reserve)")
    p = TDIR / "nostamina.png"
    if p.exists() and not NOSTAM:
        im = cv2.imread(str(p))
        h, w = im.shape[:2]
        NOSTAM.append(im[int(0.2 * h):int(0.8 * h), int(0.08 * w):int(0.92 * w)])
    if missing and strict:
        raise SystemExit("Images manquantes : " + ", ".join(missing))
    return missing


# ---------------------------------------------------------------- outils
def find_all(screen, tpl, thr, max_n=12):
    """Toutes les occurrences d'une image (centres), de la meilleure a la moins bonne."""
    if tpl is None or screen.shape[0] < tpl.shape[0] or screen.shape[1] < tpl.shape[1]:
        return []
    res = cv2.matchTemplate(screen, tpl, cv2.TM_CCOEFF_NORMED)
    h, w = tpl.shape[:2]
    out = []
    while len(out) < max_n:
        _, sc, _, loc = cv2.minMaxLoc(res)
        if np.isnan(sc) or sc < thr:
            break
        out.append((loc[0] + w / 2, loc[1] + h / 2, float(sc)))
        cv2.rectangle(res, (max(0, loc[0] - w // 2), max(0, loc[1] - h // 2)), (loc[0] + w // 2, loc[1] + h // 2), -1.0, -1)
    return out


def swipe(x0, y0, x1, y1, ms=450):
    nt.adb("shell", "input", "swipe", str(int(x0)), str(int(y0)), str(int(x1)), str(int(y1)), str(ms))
    time.sleep(0.9)


def debug_shot(name, screen=None):
    try:
        d = BASE / "debug"
        d.mkdir(exist_ok=True)
        f = d / f"aa_{name}_{time.strftime('%H%M%S')}.png"
        cv2.imwrite(str(f), screen if screen is not None else nt.screenshot())
        log(f"(debug) capture : debug/{f.name}")
    except Exception:
        pass


# ---------------------------------------------------------------- lecture du floor
def _yellow(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (18, 110, 140), (36, 255, 255))


def floor_mask(screen):
    """Dessin (masque jaune) du chiffre du floor sur le permit affiche. -> image binaire, ou None si le permit n'est pas la."""
    t = T.get("permit")
    if t is None:
        return None
    score, loc = t.best(screen)
    if score < t.thr:
        return None
    W, H = t.size
    x0, y0 = loc[0] - t.off[0], loc[1] - t.off[1]                    # coin haut-gauche du permit a l'ecran
    xa, ya, xb, yb = (int(x0 + FLOOR_BOX[0] * W), int(y0 + FLOOR_BOX[1] * H), int(x0 + FLOOR_BOX[2] * W), int(y0 + FLOOR_BOX[3] * H))
    if xa < 0 or ya < 0 or yb > screen.shape[0] or xb > screen.shape[1]:
        return None
    m = _yellow(screen[ya:yb, xa:xb])
    return m if int(m.sum()) // 255 >= 25 else None


def permit_inserted(screen, hit):
    """Le titre "Agency Assessment" est affiche avec ET sans permit : on regarde si l'hexagone est turquoise (permit pose)."""
    th, tw = 189, 438                                   # taille de permit.png (image complete)
    x0, y0 = hit[0] - tw / 2, hit[1] - th / 2
    H, W = screen.shape[:2]
    xa, xb = int(max(0, x0 + 0.08 * tw)), int(min(W, x0 + 0.36 * tw))
    ya, yb = int(max(0, y0 + 0.20 * th)), int(min(H, y0 + 0.80 * th))
    if xb - xa < 10 or yb - ya < 10:
        return False
    hsv = cv2.cvtColor(screen[ya:yb, xa:xb], cv2.COLOR_BGR2HSV)
    teal = ((hsv[..., 0] > 75) & (hsv[..., 0] < 100) & (hsv[..., 1] > 80) & (hsv[..., 2] > 60)).mean()
    return teal > 0.30


def hexagon_xy(hit):
    """Centre de l'hexagone (la ou on pose le permit), a partir de la position du titre."""
    return hit[0] - 219 + 0.22 * 438, hit[1]


BOT_WAIT = 3.5                                   # secondes d'attente apres chaque ajout de bot (message a l'ecran)
BOTS_POS = [None]                                # dernier endroit ou le bouton Bots (jaune) a ete vu


def bots_grey(screen):
    """Bouton Bots GRISE (= les bots sont deja dans l'equipe) ? Meme texte que bots.png mais sans le jaune.
    Si tu ajoutes templates_aa/botsgris.png, elle est utilisee en priorite."""
    g = T.get("botsgris")
    if g is not None:
        return bool(g.find(screen))
    t = T.get("bots")
    if t is None:
        return False
    full = cv2.imread(str(TDIR / "bots.png"))
    th, tw = full.shape[:2]
    H, W = screen.shape[:2]
    cx, cy = BOTS_POS[0] or (0.6625 * W, 0.942 * H)
    xa, ya = int(max(0, cx - tw)), int(max(0, cy - th))
    zone = screen[ya:int(min(H, cy + th)), xa:int(min(W, cx + tw))]
    if zone.shape[0] < th or zone.shape[1] < tw:
        return False
    res = cv2.matchTemplate(cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY), cv2.cvtColor(full, cv2.COLOR_BGR2GRAY), cv2.TM_CCOEFF_NORMED)
    _, sc, _, loc = cv2.minMaxLoc(res)
    if sc < 0.55:
        return False                                   # pas de bouton "Bots" a cet endroit
    box = cv2.cvtColor(zone[loc[1]:loc[1] + th, loc[0]:loc[0] + tw], cv2.COLOR_BGR2HSV)
    return float(box[..., 1].mean()) < 60              # jaune = tres sature ; gris = presque pas


SEEN_DIGITS = set()


def read_floor(screen):
    """-> (floor lu ou None, confiance, masque). Le numero du floor est le nombre jaune au-dessus de "Lv." sur le permit
    (en haut a droite du lobby). Lecture chiffre par chiffre : voir aa_digits.py."""
    m = floor_mask(screen)
    if m is None:
        return None, 0.0, None
    n, det = aa_digits.read_number(m)
    for d, how, conf, g in det:                            # chiffre lu "a la forme" (jamais vu) : on garde son dessin pour verifier
        if how == "forme" and d not in SEEN_DIGITS:
            SEEN_DIGITS.add(d)
            try:
                (BASE / "debug").mkdir(exist_ok=True)
                cv2.imwrite(str(BASE / "debug" / f"aa_chiffre_lu_{d}_{time.strftime('%H%M%S')}.png"), g)
            except Exception:
                pass
    return n, (min((c for _d, _h, c, _g in det), default=0.0)), m


# ---------------------------------------------------------------- bots
def pick_bots():
    """Interface Bots ouverte : ajoute les bots voulus (Add le plus proche du portrait), puis ferme avec la croix."""
    added = []
    for b in BOTS:
        t = BOT_T.get(b)
        if t is None:
            continue
        hit = None
        for attempt in range(7):                               # on cherche le portrait ; sinon on fait defiler la liste
            s = nt.screenshot()
            t.last = None
            hit = t.find(s)
            if hit:
                break
            H, W = s.shape[:2]
            if attempt < 3:
                swipe(0.5 * W, 0.70 * H, 0.5 * W, 0.35 * H)     # liste verticale ?
            else:
                swipe(0.70 * W, 0.5 * H, 0.35 * W, 0.5 * H)     # liste horizontale ?
        if not hit:
            log(f"(!) bot {BOT_NAMES[b]} introuvable dans la liste : on continue sans lui")
            continue
        px, py = hit[0], hit[1]
        if added:
            time.sleep(BOT_WAIT)                               # le message "bot ajoute" recouvre les Add de la 1re rangee :
            s = nt.screenshot()                                # on attend qu'il disparaisse avant l'ajout suivant
            t.last = None
            hit = t.find(s) or hit
            px, py = hit[0], hit[1]
        adds = find_all(s, T["add"].img, T["add"].thr) if "add" in T else []
        if not adds:
            log(f"bot {BOT_NAMES[b]} : pas de bouton Add (deja dans l'equipe ?)")
            continue
        if "botsui" not in SEEN_DIGITS:                         # une capture de l'interface par lancement, pour controle
            SEEN_DIGITS.add("botsui")
            debug_shot("bots_ui", s)
        # le bouton Add d'un bot est ALIGNE avec son portrait : juste en dessous (meme colonne) ou sur la meme ligne.
        # Un Add qui n'est pas aligne appartient a un AUTRE bot (cas ou celui-ci est deja dans l'equipe) : on n'y touche pas.
        mine = [a_ for a_ in adds if (abs(a_[0] - px) <= 95 and 0 < a_[1] - py <= 280) or (abs(a_[1] - py) <= 60 and a_[0] > px)]
        if not mine:
            log(f"bot {BOT_NAMES[b]} : pas de bouton Add a lui (deja dans l'equipe) : je ne touche a rien")
            added.append(b)
            continue
        ax, ay, sc = min(mine, key=lambda a_: (a_[0] - px) ** 2 + (a_[1] - py) ** 2)
        nt.tap(ax, ay)
        added.append(b)
        log(f"bot ajoute : {BOT_NAMES[b]} (Add {sc:.2f})")
        time.sleep(1.0)
    if added:
        time.sleep(BOT_WAIT)                                   # idem avant de fermer la fenetre
    for _ in range(6):                                         # fermeture de l'interface
        s = nt.screenshot()
        h = T["closebots"].find(s) if "closebots" in T else None
        if not h:
            break
        nt.tap(h[0], h[1])
        time.sleep(1.0)
    return added


# ---------------------------------------------------------------- persos (stamina)
def stamina_bars(screen):
    """Barres de stamina visibles -> [(x, y, stamina 2 / 1 / 0)], de gauche a droite."""
    out = []
    for k, val in (("charstamina2", 2), ("charstamina1", 1), ("charstamina0", 0)):
        img = ST.get(k)
        if img is None:
            continue
        for x, y, _sc in find_all(screen, img, 0.82):
            if all(abs(x - o[0]) > img.shape[1] * 0.6 or abs(y - o[1]) > img.shape[0] for o in out):
                out.append((x, y, val))
    return sorted(out)


def char_under(screen, x, y, pool=None):
    """Nom du perso (parmi pool, par defaut les reserves) dont l'icone est sous la barre de stamina (x, y), sinon None."""
    H, W = screen.shape[:2]
    xa, xb = int(max(0, x - ICON_W)), int(min(W, x + ICON_W))
    ya, yb = int(max(0, y - 10)), int(min(H, y + ICON_DY + ICON_W))
    zone = screen[ya:yb, xa:xb]
    best = (None, 0.0)
    for n, img in (CH if pool is None else pool).items():
        if zone.shape[0] < img.shape[0] or zone.shape[1] < img.shape[1]:
            continue
        sc = float(cv2.matchTemplate(zone, img, cv2.TM_CCOEFF_NORMED).max())
        if sc > best[1]:
            best = (n, sc)
    return best[0] if best[1] >= 0.80 else None


def half(img):
    return cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)


def no_stamina(screen):
    """Message "Insufficient stamina" a l'ecran ? (recherche en demi-taille : 4x plus rapide)"""
    if not NOSTAM:
        return False
    if len(NOSTAM) == 1:
        NOSTAM.append(half(NOSTAM[0]))
    for x, y, _sc in find_all(half(screen), NOSTAM[1], 0.60, 2):       # passe rapide, puis verification exacte en taille reelle
        h, w = NOSTAM[0].shape[:2]
        xa, ya = int(max(0, 2 * x - w / 2 - 8)), int(max(0, 2 * y - h / 2 - 8))
        zone = screen[ya:ya + h + 16, xa:xa + w + 16]
        if zone.shape[0] >= h and zone.shape[1] >= w and float(cv2.matchTemplate(zone, NOSTAM[0], cv2.TM_CCOEFF_NORMED).max()) >= 0.85:
            return True
    return False


def open_char_menu():
    """Ouvre le menu des persos : aa_char_pos s'il est regle, sinon on cherche l'icone de ton perso dans le lobby.
    -> True si les barres de stamina sont visibles."""
    s = nt.screenshot()
    if stamina_bars(s):
        return True
    H, W = s.shape[:2]
    # ton perso = le modele 3D a gauche du lobby : un clic dessus ouvre le menu des persos
    pos = CHAR_POS or DEFAULT_CHAR_POS
    spots = [(pos[0] * W, pos[1] * H, "ton perso"), (pos[0] * W, (pos[1] + 0.12) * H, "ton perso, plus bas")]
    for x, y, n in spots[:4]:
        nt.tap(x, y)
        time.sleep(1.2)
        if stamina_bars(nt.screenshot()):
            log(f"menu des persos ouvert (clic sur {n})")
            return True
    log("(!) le clic sur ton perso n'ouvre pas le menu des persos (aucune barre de stamina visible)")
    if "persos" not in SEEN_DIGITS:                         # une seule capture par lancement
        SEEN_DIGITS.add("persos")
        debug_shot("persos", nt.screenshot())
    return False


def close_settings():
    """Ferme la fenetre "Battle Settings" (croix rouge) si elle est encore ouverte."""
    for _ in range(3):
        s = nt.screenshot()
        if not stamina_bars(s):
            return
        h = next((T[k].find(s) for k in ("closereset", "closebots") if k in T and T[k].find(s)), None)
        if not h:
            return
        nt.tap(h[0], h[1])
        time.sleep(0.9)


def pick_char(floor=None):
    """Ouvre le menu des persos (aa_char_pos) et en prend un qui a de la stamina.
    Persos reserves : jamais joues sous RESERVE_FLOOR, joues en priorite a partir de RESERVE_FLOOR.
    -> True / False (plus aucun perso jouable) / None (pas regle)."""
    high = bool(CH) and floor is not None and floor >= RESERVE_FLOOR
    if CH:
        log(f"choix du perso : floor {floor if floor is not None else 'INCONNU'} -> "
            + (f"reserves en priorite ({', '.join(CH)})" if high else f"reserves gardes pour les floors {RESERVE_FLOOR}+"))
    if not open_char_menu():
        log("(!) menu des persos non ouvert : je garde le perso actuel")
        return None
    s0 = nt.screenshot()
    H, W = s0.shape[:2]
    if "menu" not in SEEN_DIGITS:                           # une capture du menu par lancement, pour verifier la reconnaissance
        SEEN_DIGITS.add("menu")
        debug_shot("menu_persos", s0)
    seen = False
    # Le menu se parcourt en balayant de gauche a droite : on le parcourt EN ENTIER, dans les deux sens, avant de conclure.
    # Haut floor : d'abord les reserves (tout le menu), ensuite seulement n'importe qui. Bas floor : jamais les reserves.
    modes = ("reserve", "tous") if high else ("libre",)
    direction = 1                                           # 1 = vers la suite du menu (doigt de droite a gauche), -1 = retour
    for mode in modes:
        ends = 0                                            # bouts du menu atteints pendant ce passage
        for i in range(40):
            s = nt.screenshot()
            bars = stamina_bars(s)
            seen = seen or bool(bars)
            cands = []
            for x, y, v in bars:
                if v <= 0:
                    continue
                who = char_under(s, x, y) if CH else None
                if (mode == "reserve" and not who) or (mode == "libre" and who):
                    continue
                cands.append((-v, x, y, who))
            if cands:
                v, x, y, who = min(cands)
                nt.tap(x, y + ICON_DY)
                name = who or char_under(s, x, y, ICONS) or "perso inconnu"
                log(f"perso choisi : {name}{' (reserve)' if who else ''} {-v}/2"
                    + (f" | floor {floor}" if floor else ""))
                time.sleep(1.0)
                close_settings()
                return True
            if not seen and i >= 2:
                log("(!) menu des persos non reconnu apres le clic sur ton perso (aucune barre de stamina)")
                debug_shot("persos", s)
                return None
            yy = (bars[0][1] + ICON_DY * 0.5) if bars else 0.6 * H
            if direction > 0:
                swipe(0.70 * W, yy, 0.32 * W, yy, 600)
            else:
                swipe(0.32 * W, yy, 0.70 * W, yy, 600)
            s2 = nt.screenshot()
            band = slice(int(max(0, yy - 150)), int(min(H, yy + 200)))
            moved = float(cv2.absdiff(s[band], s2[band]).mean()) > 2.0
            if not moved:                                   # le menu ne bouge plus : on est au bout -> on repart dans l'autre sens
                ends += 1
                direction = -direction
                if ends >= 2:
                    break                                   # les deux bouts ont ete vus : tout le menu a ete regarde
    close_settings()
    if CH and not high:
        log(f"(!) il ne reste que les persos reserves ({', '.join(CH)}) : je les garde pour les floors {RESERVE_FLOOR}+")
    return False


# ---------------------------------------------------------------- test
def run_test():
    fastmatch.FAST = False
    load_templates(strict=False)
    screen = nt.screenshot()
    print(f"Capture : {screen.shape[1]}x{screen.shape[0]} | bots : {', '.join(BOTS)} | reset au floor : {RESET_FLOOR or 'jamais'}")
    print(f"{'image':<14}{'score':>7}{'seuil':>7}  resultat")
    for name, t in list(T.items()) + [(f"bot:{b}", t) for b, t in BOT_T.items()]:
        score, _ = t.best(screen)
        hit = t.find(screen)
        print(f"{name:<14}{score:>7.2f}{t.thr:>7.2f}  {'TROUVE -> ' + str((int(hit[0]), int(hit[1]))) if hit else '-'}")
    fl, sc, m = read_floor(screen)
    print(f"floor lu : {fl if fl else ('chiffre inconnu' if m is not None else 'permit non visible')} (score {sc:.2f}) | "
          f"chiffres connus : {sorted({k[0] for k in aa_digits.known()})} (les autres sont lus a la forme)")
    print("barres de stamina :", [(int(x), int(y), f"{v}/2") for x, y, v in stamina_bars(screen)] or "aucune")


# ---------------------------------------------------------------- boucle principale
def main():
    global FLOOR
    if "--capture" in sys.argv:
        nt.init_adb()
        debug_shot("capture")
        return
    if "--test" in sys.argv:
        nt.init_adb()
        return run_test()
    load_templates()
    nt.init_adb()
    if "--no-home" not in sys.argv:
        nav.make(nt.screenshot, nt.tap, nt.adb, log).go()
    REPRISE = nav.Reprise(nav.make(nt.screenshot, nt.tap, nt.adb, log), log)   # ecran inconnu 60 s -> menu + reprise
    seen_t = time.time()                # derniere fois qu'une image a ete reconnue
    WD = watchdog.make(nt.screenshot, nt.tap, nt.adb, log, Done)
    last = {n: 0.0 for n in SPEC}
    runs, auto_taps = 0, 0
    in_battle, t_start = False, 0.0
    bots_done, char_done, flipped = False, False, False
    floor, floor_known = FLOOR, FLOOR is not None       # floor courant ; connu = lu sur le permit, donne, ou apres un reset
    floor_read = False                                  # lu sur le permit depuis le dernier combat
    resetting, deploy_tries, nav_taps = False, 0, 0
    after_reset = 0.0
    need_char = False                                   # "Insufficient stamina" vu : il faut absolument changer de perso
    last_action = time.time()
    print(f"Agency Assessment : {MAX_RUNS} combat(s) | bots : {', '.join(BOT_NAMES[b] for b in BOTS) or 'aucun'} | "
          f"reset du permit : {'a partir du floor ' + str(RESET_FLOOR) if RESET_FLOOR else 'jamais'} | "
          f"floor de depart : {floor if floor_known else 'a lire'}"
          + (f" | reserves pour les floors {RESERVE_FLOOR}+ : {', '.join(CH)}" if CH else "") + ". Ctrl+C pour arreter.")


    def tap_key(key, h, extra=""):
        nonlocal last_action, nav_taps
        nt.tap(h[0], h[1])
        last[key] = last_action = time.time()
        nav_taps += 1
        log(f"{key} ({h[2]:.2f}{extra}) | run {runs}/{MAX_RUNS}")
        time.sleep(nt.SETTLE)

    while True:
        screen = nt.screenshot()
        now = time.time()
        if WD and WD.poll(screen, in_battle, frozen_only=True):   # pas de Challenge Again en AA : relance si l'ecran est fige 5 min
            in_battle, bots_done, char_done, flipped, resetting, deploy_tries = False, False, False, False, False, 0
            last_action = time.time()
            continue
        hits = {key: h for key, h in fastmatch.find_many(T.items(), screen).items() if h}     # en parallele, meme resultat
        if "bots" in hits:
            BOTS_POS[0] = hits["bots"][:2]
            if bots_grey(screen):
                del hits["bots"]                           # meme texte mais grise : ce n'est pas un bouton a taper
        title = hits.get("permit")                         # titre du lobby (present meme sans permit)
        if title and not permit_inserted(screen, title):
            del hits["permit"]
        ready = {k: h for k, h in hits.items() if now - last[k] >= T[k].cd}

        if (("deploy" in hits or (in_battle and now - t_start < 30)) and now - last.get("_nostam", 0) > 6
                and no_stamina(screen)):
            last["_nostam"] = last_action = now            # "Insufficient stamina" : ce perso est vide -> on en change
            log("stamina insuffisante sur ce perso : changement de perso (apres lecture du floor)")
            in_battle, char_done, need_char = False, False, True    # le choix se fait dans le lobby, une fois le floor lu
            time.sleep(1.5)
            continue

        if "revive" in ready:                              # perso mort au combat : resurrection gratuite
            tap_key("revive", hits["revive"], " : resurrection gratuite")
            continue

        # --- 1. fin de combat : cartes puis Return ---
        if "cards" in hits and not flipped:
            t = T["cards"]
            _sc, loc = t.best(screen)
            ax, ay = loc[0] + t.img.shape[1] / 2, loc[1] + t.img.shape[0] / 2     # centre de l'ancre a l'ecran
            for i, (cx, cy) in enumerate(CARD_XY[:CARDS]):
                nt.tap(ax + cx - CARD_ANCHOR[0], ay + cy - CARD_ANCHOR[1])
                time.sleep(0.9)
            flipped, last_action, in_battle = True, time.time(), False
            log(f"cartes retournees ({CARDS}) | run {runs}/{MAX_RUNS}")
            if WD:
                WD.kick()
            time.sleep(1.0)
            continue
        if "return" in ready:
            tap_key("return", hits["return"])
            if in_battle or flipped:                       # un combat vient de se terminer
                runs += 1
                floor_read = floor_known = False           # apres un combat on ne suppose RIEN : le floor est relu sur le permit
                floor = None
                last.pop("_read", None)
                last.pop("_fl0", None)
                log(f"combat termine ({runs}/{MAX_RUNS})")
            in_battle, flipped, bots_done, char_done, deploy_tries, nav_taps = False, False, False, False, 0, 0
            if WD:
                WD.kick()
            if runs >= MAX_RUNS:
                raise Done(f"{runs}/{MAX_RUNS} combat(s) termine(s). Fini !")
            continue
        if "cardflip" in ready:
            in_battle = False
            tap_key("cardflip", hits["cardflip"])
            continue

        # --- 2. combat ---
        if in_battle:
            if ("auto" in ready and auto_taps < nt.AUTO_MAX_TAPS and "deploy" not in hits and not title
                    and hits["auto"][1] < 0.30 * screen.shape[0]):     # le bouton AUTO est en haut de l'ecran de combat
                auto_taps += 1
                tap_key("auto", hits["auto"])
                continue
            if "deploy" not in hits and "permit" not in hits:
                if now - t_start > 15:
                    deploy_tries = 0                       # le combat a bien demarre
                time.sleep(nt.LOOP_DELAY)
                continue
            if now - t_start < 25:                         # Deploy vient d'etre tape : on laisse le temps au chargement
                time.sleep(nt.LOOP_DELAY)
                continue
            in_battle = False                              # toujours dans le lobby : Deploy n'a rien lance
            if deploy_tries >= 3:
                debug_shot("deploy", screen)
                raise Done("Deploy ne lance plus le combat (3 essais) : plus de stamina sur ce perso ? "
                           "Regle \"aa_char_pos\" pour que le bot change de perso tout seul (voir le haut de auto_aa.py).")

        # --- 3. reset du permit (Recruit -> My Permit -> Reset to Floor 1 -> Confirm) ---
        if resetting:
            for key in ("confirmreset", "resetpermit", "mypermit", "recruit"):
                if key in ready:
                    tap_key(key, hits[key], " (reset du permit)")
                    if key == "confirmreset":
                        resetting, floor, floor_known, bots_done, char_done = False, 1, True, False, False
                        floor_read = False
                        last.pop("_fl0", None)
                        after_reset = time.time()          # il reste a fermer la page avec la croix (closereset)
                        log("permit remis au floor 1")
                    break
            else:
                if now - last_action > 12 and "deploy" not in hits and ("closereset" in ready or "closebots" in ready):
                    k = "closereset" if "closereset" in ready else "closebots"      # reset bloque : on ferme la page et on reessaie
                    tap_key(k, hits[k])
                elif now - last_action > 40:
                    debug_shot("reset", screen)
                    raise Done("Reset du permit : un bouton n'est pas reconnu (Recruit / My Permit / Reset / Confirm). Voir debug/.")
                else:
                    time.sleep(nt.LOOP_DELAY)
            continue

        # --- 3 bis. apres le reset : fermer la page du permit avec la croix ---
        if after_reset:
            lobby = "deploy" in hits or "insertpermit" in hits
            key = next((k for k in ("closereset", "closebots") if k in ready), None)
            if key and not lobby:
                tap_key(key, hits[key], " (page du permit fermee)")
                continue
            if lobby or now - after_reset > 20:
                after_reset = 0.0
            else:
                time.sleep(nt.LOOP_DELAY)
                continue

        # --- 4. interface Bots ouverte ---
        if "add" in hits and "deploy" not in hits and not bots_done:
            pick_bots()
            bots_done, last_action = True, time.time()
            continue
        if "closebots" in ready and "deploy" not in hits and "add" not in hits and (bots_done or "mypermit" in hits):
            tap_key("closebots", hits["closebots"])        # fenetre restee ouverte (bots deja faits / apres un reset)
            continue

        # --- 5. lobby ---
        if "deploy" in hits or "insertpermit" in hits or "permit" in hits or title:
            if "permit" not in hits and ("insertpermit" in hits or title):
                if "insertpermit" in ready:
                    tap_key("insertpermit", hits["insertpermit"])
                elif "insertpermit" not in hits and now - last.get("_hex", 0) > 3:
                    last["_hex"] = last_action = now       # "Insert Permit" non reconnu (texte anime) : on tape l'hexagone
                    hx, hy = hexagon_xy(title)
                    nt.tap(hx, hy)
                    log(f"insertpermit (hexagone vide) | run {runs}/{MAX_RUNS}")
                    time.sleep(nt.SETTLE)
                else:
                    time.sleep(nt.LOOP_DELAY)
                continue                                   # jamais de Bots / Deploy tant que le permit n'est pas pose
            if "permit" in hits:
                fl, sc, mask = read_floor(screen)
                if fl != last.get("_read"):
                    last["_read_t"] = now                  # la lecture vient de changer : on attend qu'elle se stabilise
                if fl is not None and fl == last.get("_read") and now - last.get("_read_t", now) >= 1.5:   # stable 1,5 s = fiable
                    if not floor_known or fl != floor or not floor_read:
                        log(f"floor lu sur le permit : {fl}")
                        if len([k for k in SEEN_DIGITS if str(k).startswith("lu")]) < 10:     # captures de controle (10 max)
                            SEEN_DIGITS.add(f"lu{now}")
                            debug_shot(f"floor_lu_{fl}", screen)
                    floor, floor_known, floor_read = fl, True, True
                last["_read"] = fl
                if not floor_read:
                    if fl is None and mask is not None and now - last.get("_fl", 0) > 60:
                        last["_fl"] = now
                        log("(!) numero du floor pas encore lisible sur le permit : j'attends")
                    if now - last.setdefault("_fl0", now) < 30:
                        time.sleep(nt.LOOP_DELAY)          # le floor d'abord : rien d'autre (reset, bots, perso) avant de l'avoir lu
                        continue
                    if now - last.get("_flcap", 0) > 120:  # toujours illisible apres 30 s : capture + on continue sans le connaitre
                        last["_flcap"] = now
                        log("(!) floor ILLISIBLE apres 30 s : pas de reset, et les persos reserves ne seront pas utilises ce combat")
                        debug_shot("floor_illisible", screen)
                if RESET_FLOOR and floor_known and floor >= RESET_FLOOR:
                    log(f"floor {floor} : reset du permit (regle a partir du floor {RESET_FLOOR})")
                    resetting, last_action = True, time.time()
                    continue
            if "bots" not in hits and BOTS and not bots_done and bots_grey(screen):
                bots_done = True
                log("bouton Bots grise : les bots sont deja dans l'equipe")
            if "bots" in ready and BOTS and not bots_done:
                tap_key("bots", hits["bots"])
                time.sleep(0.8)
                continue
            if not char_done and (bots_done or not BOTS):
                r = pick_char(floor if floor_known else None)
                char_done, last_action = True, time.time()
                if need_char and not r:
                    raise Done(f"Plus de stamina sur ce perso et aucun autre perso jouable. {runs}/{MAX_RUNS} combat(s) faits. Fini !")
                need_char = False
                if r is False:
                    raise Done(f"Plus aucun perso avec de la stamina. {runs}/{MAX_RUNS} combat(s) faits. Fini !")
                if r:
                    continue
            if "deploy" in ready and ("permit" in hits or "insertpermit" not in hits) and (bots_done or not BOTS):
                deploy_tries += 1
                tap_key("deploy", hits["deploy"], f" (essai {deploy_tries})" if deploy_tries > 1 else "")
                in_battle, t_start, auto_taps, flipped = True, time.time(), 0, False
                continue
            time.sleep(nt.LOOP_DELAY)
            continue

        # --- 6. navigation jusqu'au lobby ---
        for key in ("myteam", "createteam", "jointeam", "aa", "coop", "ua"):
            if key in ready:
                tap_key(key, hits[key])
                break
        else:
            if nav_taps > 60:
                debug_shot("nav", screen)
                raise Done("Securite : 60 taps sans arriver a lancer un combat. Voir debug/.")
            if now - last_action > 20 and now - last.get("_dbg", 0) > 120:
                last["_dbg"] = now                         # rien reconnu : on garde une capture + les scores pour comprendre
                best = sorted(((round(float(t.best_exact(screen)[0]), 2), k) for k, t in T.items()), reverse=True)[:4]
                log("(!) aucun bouton reconnu sur cet ecran. Meilleurs scores : " + ", ".join(f"{k} {v}" for v, k in best))
                debug_shot("rien", screen)
            if hits:
                seen_t = now
            if not in_battle and REPRISE.due(now - max(last_action, seen_t)) and REPRISE.run():
                in_battle, bots_done, char_done, flipped, resetting, deploy_tries = False, False, False, False, False, 0
                last_action = seen_t = time.time()                  # comme apres le garde-chien
                continue
            if now - last_action > nt.IDLE_LIMIT and not WD:
                debug_shot("bloque", screen)
                raise Done(f"Plus rien reconnu depuis {nt.IDLE_LIMIT}s : arret. Regarde debug/ et lance 'python auto_aa.py --test' "
                           f"sur cet ecran.")
            time.sleep(nt.LOOP_DELAY)


if __name__ == "__main__":
    import journal
    journal.start("aa")                 # journal de session dans logs/ (garde les 10 derniers)
    journal.run(main, Done)               # meme affichage qu'avant + incident (capture, boite noire) si arret anormal
