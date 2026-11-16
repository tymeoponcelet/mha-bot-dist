"""
Choix du PERSONNAGE (liste qui s'ouvre apres le "+" dans Night Ops, Supply et Hero Trial).

  - Le perso voulu = une image de sa LIGNE de la liste (portrait + BP + Deploy) dans templates_chars/<nom>.png
  - Perso choisi : option --hero <nom>, sinon config.json ("pick_hero", ecrit par l'assistant), sinon "allmight"
  - Si sa ligne n'est pas visible, le bot FAIT DEFILER la liste (d'abord vers le bas, puis vers le haut) jusqu'a la trouver,
    puis tape sur SON bouton "Deploy". Jamais de touche Retour.
  - Pour AJOUTER un perso : depose sa ligne recadree (meme resolution que l'emulateur) dans templates_chars/ -> il apparait
    automatiquement dans l'assistant.

Ce module ne depend d'aucun bot : chaque bot lui passe sa fonction adb et sa fonction de log.
"""
import os  # noqa: E402
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # memoire : sinon numpy reserve ~370 Mo par processus (16/11/2026 : « Insufficient memory » chez un ami)
import json
import sys
import time
from pathlib import Path

try:                                    # l'assistant (sans opencv) peut quand meme importer la liste des persos
    import cv2
    import numpy as np
    import fastmatch                    # reconnaissance rapide (meme score, ~10x plus vite)
except ImportError:                     # pragma: no cover
    cv2 = np = fastmatch = None

BASE = Path(__file__).resolve().parent
CHAR_DIR = BASE / "templates_chars"
DEFAULT = "allmight"
ORDER = ["allmight", "ende", "deku", "denki", "stain", "tsuyu", "ochaco", "iida", "nejire", "mirio"]
NAMES = {"allmight": "All Might", "ende": "Endeavor", "deku": "Deku", "denki": "Denki", "stain": "Stain",
         "tsuyu": "Tsuyu", "ochaco": "Ochaco", "iida": "Iida", "nejire": "Nejire", "mirio": "Mirio"}

# ======================= REGLAGES =======================
CROP = (0.045, 0.10, 0.26, 0.90)        # zone comparee dans la ligne : le portrait (sans halo, sans BP / niveau qui changent)
THR = 0.80                              # seuil de reconnaissance d'une ligne
BTN_THR = 0.80                          # seuil du bouton "Deploy" generique (sert a savoir qu'on est sur la liste)
SWIPE_DURATION = 700                    # ms (lent = pas d'effet d'inertie)
SWIPE_FROM, SWIPE_TO = 0.72, 0.34       # hauteurs (0-1) du glissement : le doigt monte de FROM a TO -> la liste descend
SWIPE_MAX = 10                          # glissements max dans un sens avant de repartir dans l'autre
SWIPE_END_DIFF = 2.0                    # difference d'image < a ca apres un glissement = bout de la liste
SWIPE_STILL = 2                         # glissements consecutifs sans mouvement avant de conclure "bout de la liste"
MAX_ENDS = 4                            # bouts atteints (2 = un aller-retour complet) avant d'abandonner
SETTLE = 1.0                            # pause apres un glissement (s)


def pretty(name):
    return NAMES.get(name, name.capitalize())


def available(char_dir=CHAR_DIR):
    """Persos disponibles = PNG de templates_chars/ (ordre : ORDER, puis alphabetique)."""
    names = [p.stem for p in Path(char_dir).glob("*.png")]
    return sorted(names, key=lambda n: (ORDER.index(n) if n in ORDER else len(ORDER), n))


def chosen_name():
    """--hero <nom> > config.json 'pick_hero' > DEFAULT."""
    if "--hero" in sys.argv:
        try:
            return sys.argv[sys.argv.index("--hero") + 1].strip().lower()
        except IndexError:
            pass
    try:
        c = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
        if isinstance(c.get("pick_hero"), str) and c["pick_hero"]:
            return c["pick_hero"].strip().lower()
    except Exception:
        pass
    return DEFAULT


def chosen_explicit():
    """True si un perso a ete choisi EXPRES (--hero, ou "pick_hero" dans config.json). False = on garde celui deja place."""
    if "--hero" in sys.argv:
        try:
            return bool(sys.argv[sys.argv.index("--hero") + 1].strip())
        except IndexError:
            return False
    try:
        c = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
        return isinstance(c.get("pick_hero"), str) and bool(c["pick_hero"].strip())
    except Exception:
        return False


class _Row:
    """Une ligne de la liste : reconnaissance du portrait + position du bouton Deploy (detectee dans l'image)."""

    def __init__(self, path):
        full = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if full is None:
            raise FileNotFoundError(f"{Path(path).parent.name}/{Path(path).name}")
        H, W = full.shape[:2]
        self.size = (W, H)
        self.name = f"{Path(path).parent.name}/{Path(path).name}"     # pour la detection de derive (derive.py)
        self.x0, self.y0 = int(CROP[0] * W), int(CROP[1] * H)
        self.img = full[self.y0:int(CROP[3] * H), self.x0:int(CROP[2] * W)]
        self.btn_box = self._deploy_box(full)            # (x, y, w, h) du bouton Deploy dans l'image de la ligne
        bx, by, bw, bh = self.btn_box
        self.deploy = (bx + bw / 2, by + bh / 2)         # point de tap, en pixels de l'image de la ligne
        m = 4                                            # marge : on ne garde que le coeur du bouton
        self.btn_img = full[by + m:by + bh - m, bx + m:bx + bw - m]
        self.thr = THR                                   # pour fastmatch : verification en couleur exacte autour du seuil
        fastmatch.prepare(self)

    @staticmethod
    def _deploy_box(full):
        """Le bouton Deploy = la plus grande tache jaune vif dans la partie droite de la ligne."""
        H, W = full.shape[:2]
        mask = cv2.inRange(cv2.cvtColor(full, cv2.COLOR_BGR2HSV), (15, 120, 170), (35, 255, 255))
        mask[:, :int(0.45 * W)] = 0
        n, _lab, st, _c = cv2.connectedComponentsWithStats(mask)
        if n > 1:
            i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
            x, y, w, h, area = (int(v) for v in st[i])
            if area > 0.02 * W * H:
                return x, y, w, h
        return int(0.69 * W), int(0.36 * H), int(0.28 * W), int(0.33 * H)    # repli : position habituelle

    def prep(self, img):                                 # nom attendu par fastmatch (comparaison couleur directe)
        return img

    def best(self, screen):
        """Meme score que la comparaison couleur sur toute la capture (fastmatch ; --slow-match pour l'ancienne)."""
        return fastmatch.best(self, screen)

    def top_left(self, loc):
        """Coin haut-gauche de la ligne complete a l'ecran, d'apres la position trouvee du portrait."""
        return loc[0] - self.x0, loc[1] - self.y0


class Picker:
    """Trouve la ligne du perso voulu, fait defiler la liste si besoin, donne le point du bouton Deploy."""

    def __init__(self, name, adb, log, fail=RuntimeError, char_dir=CHAR_DIR):
        if cv2 is None:
            raise SystemExit("opencv-python manquant (pip install opencv-python numpy).")
        self.name, self.adb, self.log, self.fail = name, adb, log, fail
        path = Path(char_dir) / f"{name}.png"
        if not path.exists():
            raise FileNotFoundError(f"templates_chars/{name}.png (perso '{name}' inconnu ; disponibles : "
                                    f"{', '.join(available(char_dir)) or 'aucun'})")
        self.target = _Row(path)
        self.others = [_Row(p) for p in sorted(Path(char_dir).glob("*.png")) if p.stem != name]
        self.row_w = self.target.size[0]
        self.reset()

    # ---------- etat du defilement ----------
    def reset(self):
        self.dir, self.n, self.ends, self.still, self.miss, self.sig = 1, 0, 0, 0, 0, None
        self.geo = None                     # (x gauche, x droit) de la liste a l'ecran, appris quand une ligne est visible

    # ---------- reconnaissance ----------
    def find(self, screen):
        """-> (x_tap, y_tap, score, (x_haut_gauche, y_haut_gauche)) de la ligne du perso voulu, ou None."""
        score, loc = self.target.best(screen)
        if score < THR:
            return None
        tlx, tly = self.target.top_left(loc)
        self.geo = (tlx, tlx + self.row_w)
        return tlx + self.target.deploy[0], tly + self.target.deploy[1], score, (tlx, tly)

    def on_list(self, screen):
        """True si on est sur la liste de persos (une autre ligne connue OU un bouton Deploy de ligne est visible)."""
        for row in self.others:
            score, loc = row.best(screen)
            if score >= THR:
                tlx = row.top_left(loc)[0]
                self.geo = (tlx, tlx + row.size[0])
                return True
        t = self.target
        if t.btn_img.size and screen.shape[0] >= t.btn_img.shape[0] and screen.shape[1] >= t.btn_img.shape[1]:
            res = cv2.matchTemplate(screen, t.btn_img, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            if not np.isnan(score) and score >= BTN_THR:
                left = loc[0] - t.btn_box[0] - 4                 # coin gauche de la ligne deduit de la position du bouton
                self.geo = (left, left + self.row_w)
                return True
        return False

    # ---------- defilement ----------
    def _sig(self, screen):
        h, w = screen.shape[:2]
        x0, x1 = (0, w) if not self.geo else (max(0, int(self.geo[0])), min(w, int(self.geo[1])))
        if x1 - x0 < 40:
            x0, x1 = 0, w
        band = cv2.cvtColor(screen[int(0.12 * h):int(0.95 * h), x0:x1], cv2.COLOR_BGR2GRAY)
        return cv2.resize(band, (64, 96), interpolation=cv2.INTER_AREA).astype(np.float32)

    def step(self, screen):
        """A appeler quand la ligne voulue n'est PAS visible. Si on est bien sur la liste, fait defiler (apres 2 constats
        consecutifs, pour laisser l'ecran finir de s'afficher). -> True si un glissement a ete fait."""
        if not self.on_list(screen):
            self.miss = 0
            return False
        self.miss += 1
        if self.miss < 2:
            return False
        if self.sig is not None:                         # on vient de glisser : la liste a-t-elle bouge ?
            diff = float(np.abs(self._sig(screen) - self.sig).mean())
            self.sig = None
            self.still = self.still + 1 if diff < SWIPE_END_DIFF else 0
            if self.still >= SWIPE_STILL or self.n >= SWIPE_MAX:
                self.ends += 1
                self.dir, self.n, self.still = -self.dir, 0, 0
                self.log("bout de la liste atteint, on repart dans l'autre sens")
        if self.ends >= MAX_ENDS:
            raise self.fail(f"Perso '{pretty(self.name)}' introuvable : la liste a ete parcourue dans les deux sens. "
                            f"Verifie qu'il est bien dans ta liste et que templates_chars/{self.name}.png vient de la meme "
                            f"resolution que l'emulateur (le bot ne touche jamais a Retour).")
        h, w = screen.shape[:2]
        cx = (self.geo[0] + self.geo[1]) / 2 if self.geo else 0.5 * w
        cx = min(max(cx, 0.05 * w), 0.95 * w)
        y1, y2 = (SWIPE_FROM, SWIPE_TO) if self.dir > 0 else (SWIPE_TO, SWIPE_FROM)
        self.log(f"perso '{pretty(self.name)}' introuvable : defilement de la liste vers le {'bas' if self.dir > 0 else 'haut'}")
        self.adb("shell", "input", "swipe", str(int(cx)), str(int(y1 * h)), str(int(cx)), str(int(y2 * h)), str(SWIPE_DURATION))
        self.sig = self._sig(screen)
        self.n += 1
        time.sleep(SETTLE)
        return True


class RowTpl:
    """Adaptateur : se comporte comme l'image 'am' des bots (find / best / cd / thr) mais cherche le perso choisi."""

    def __init__(self, picker, cd=2.5, with_pos=False):
        self.picker, self.cd, self.with_pos, self.thr = picker, cd, with_pos, THR

    def best(self, screen):
        score, loc = self.picker.target.best(screen)
        return score, loc

    def find(self, screen, thr=None):
        hit = self.picker.find(screen)
        if hit and thr and hit[2] < thr:
            return None
        if hit and not self.with_pos:
            return hit[0], hit[1], hit[2]
        return hit


# ======================= PERSO DEJA PLACE (emplacement du panneau lateral) =======================
SLOT_DIR = BASE / "templates_slot"      # une image par perso = son portrait DANS l'emplacement du panneau (<nom>.png)
SLOT_CORE = (0.12, 0.05, 0.88, 0.60)    # partie comparee : l'illustration seule (sans bords, sans BP ni badge de rang en bas)
SLOT_ID_THR = 0.80                      # seuil : "c'est bien ce perso-la qui est place"
SLOT_SCALE = 0.5                        # la reconnaissance se fait a cette echelle (vitesse)


class SlotReader:
    """Detecte QUEL perso est deja place dans l'emplacement du panneau lateral (la ou s'affichait le "+").

    templates_slot/<perso>.png = capture de l'emplacement avec CE perso place (meme resolution que l'emulateur, noms comme
    templates_chars/ : allmight, ende, deku, denki, stain, tsuyu, ochaco, iida, nejire, mirio...). Seule l'illustration est
    comparee, donc le BP qui change n'a aucun effet. On cherche l'illustration sur tout l'ecran : ca donne aussi la position.
    """

    def __init__(self, slot_dir=SLOT_DIR):
        if cv2 is None:
            raise SystemExit("opencv-python manquant (pip install opencv-python numpy).")
        self.dir = Path(slot_dir)
        self.cores, self.small = {}, {}      # images pleine taille et reduites (la recherche se fait en reduit : ~4x plus rapide)
        for p in sorted(self.dir.glob("*.png")):
            img = cv2.imread(str(p), cv2.IMREAD_COLOR)
            if img is None:
                continue
            h, w = img.shape[:2]
            core = img[int(SLOT_CORE[1] * h):int(SLOT_CORE[3] * h), int(SLOT_CORE[0] * w):int(SLOT_CORE[2] * w)]
            self.cores[p.stem] = core
            self.small[p.stem] = cv2.resize(core, None, fx=SLOT_SCALE, fy=SLOT_SCALE, interpolation=cv2.INTER_AREA)

    def has(self, name):
        return name in self.cores

    def match(self, screen, name, small=None):
        """-> (score, (x, y)) : meilleure ressemblance de ce perso a l'ecran, et le centre de son illustration."""
        c = self.small.get(name)
        if c is None:
            return 0.0, None
        if small is None:
            small = cv2.resize(screen, None, fx=SLOT_SCALE, fy=SLOT_SCALE, interpolation=cv2.INTER_AREA)
        if small.shape[0] < c.shape[0] or small.shape[1] < c.shape[1]:
            return 0.0, None
        res = np.nan_to_num(cv2.matchTemplate(small, c, cv2.TM_CCOEFF_NORMED), nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, score, _, loc = cv2.minMaxLoc(res)
        return float(score), ((loc[0] + c.shape[1] / 2) / SLOT_SCALE, (loc[1] + c.shape[0] / 2) / SLOT_SCALE)

    def identify(self, screen, want):
        """-> (nom ou None, score, (x, y) ou None, {nom: score}). On regarde d'abord le perso voulu, puis les autres."""
        small = cv2.resize(screen, None, fx=SLOT_SCALE, fy=SLOT_SCALE, interpolation=cv2.INTER_AREA)
        score, pt = self.match(screen, want, small)
        scores = {want: score} if want in self.cores else {}
        if score >= SLOT_ID_THR:
            return want, score, pt, scores
        best = (None, 0.0, None)
        for name in self.cores:
            if name == want:
                continue
            sc, p = self.match(screen, name, small)
            scores[name] = sc
            if sc >= SLOT_ID_THR and sc > best[1]:
                best = (name, sc, p)
        return best[0], best[1], best[2], scores


class SlotGate:
    """Barriere "bon perso place ?" a franchir AVANT de lancer un combat quand le "+" n'est pas visible.

    check(screen, ref_xy) -> ("ok", None) | ("wait", None) | ("swap", (x, y))
      ok   : le perso place est celui choisi (ou verification impossible/desactivee) -> on peut continuer
      wait : pas encore sur de la lecture (on confirme sur 2 captures)
      swap : mauvais perso -> taper en (x, y) (l'emplacement) pour ouvrir la liste
    S'il ne peut pas verifier ET ne sait pas ou taper, il leve `fail` plutot que de lancer avec un mauvais perso
    (option --no-verify pour desactiver).
    """

    def __init__(self, reader, want, log, fail=RuntimeError, enabled=True):
        self.reader, self.want, self.log, self.fail, self.enabled = reader, want, log, fail, enabled
        self.rel = None                      # position de l'emplacement par rapport a un bouton de reference (apprise via le "+")
        self.warned = False
        self.reset()

    def reset(self):
        self.ok, self.miss, self.tries, self.swap_t = False, 0, 0, 0.0

    def done(self):
        self.ok, self.miss, self.swap_t = True, 0, 0.0

    def note_plus(self, plus_xy, ref_xy):
        if ref_xy:
            self.rel = (plus_xy[0] - ref_xy[0], plus_xy[1] - ref_xy[1])

    def swap_stalled(self, list_open, timeout=10):
        """True si on a tape sur l'emplacement il y a plus de `timeout` s et que la liste ne s'est pas ouverte."""
        return bool(self.swap_t and time.time() - self.swap_t > timeout and not list_open)

    def check(self, screen, ref_xy=None):
        if self.ok or not self.enabled:
            return "ok", None
        want = self.want
        if not self.reader.has(want):
            if not self.warned:
                self.warned = True
                self.log(f"(!) pas d'image templates_slot/{want}.png : impossible de savoir quel perso est place, on continue.")
            self.done()
            return "ok", None
        name, score, pt, scores = self.reader.identify(screen, want)
        if name == want:
            self.log(f"perso deja place : {pretty(name)} ({score:.2f}) = celui choisi, on garde")
            self.done()
            return "ok", None
        self.miss += 1
        if not name and self.miss < 2:       # rien de reconnu : on confirme sur une 2e capture (l'ecran finit peut-etre de s'afficher)
            return "wait", None              # (si un autre perso connu est reconnu a >= 0.80, c'est sur : on change tout de suite)
        self.miss = 0
        if name:
            self.log(f"{pretty(name)} est place ({score:.2f}), on veut {pretty(want)} -> on change de perso")
        else:
            best = max(scores, key=scores.get) if scores else None
            seen = f"meilleure ressemblance : {pretty(best)} {scores[best]:.2f}" if best else "aucune reference"
            if self.rel and ref_xy:
                pt = (ref_xy[0] + self.rel[0], ref_xy[1] + self.rel[1])
                self.log(f"perso place non reconnu ({seen}) -> on le remplace par {pretty(want)}")
            else:
                raise self.fail(f"Un perso est place mais je ne le reconnais pas ({seen}) et je ne sais pas ou est l'emplacement : "
                                f"arret pour ne pas lancer avec un mauvais perso. Fais `--test` pour voir ce que le bot lit "
                                f"(ou relance avec --no-verify pour ne plus verifier).")
        self.tries += 1
        if self.tries > 3:
            raise self.fail(f"Impossible de placer '{pretty(want)}' apres 3 tentatives : place-le a la main puis relance.")
        self.swap_t = time.time()
        return "swap", pt
