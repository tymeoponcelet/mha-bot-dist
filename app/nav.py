"""
Retour au MENU PRINCIPAL depuis n'importe quel ecran du jeu (avant de lancer un mode).

  boucle : capture -> menu principal visible ? fini
                      popup connu (close / continue) ? on le ferme
                      "Abandonning Stage" (abandonno.png) / "Exit Stage" (exitstage.png) / HOME visibles ? on les tape
                      rien de reconnu (combat, ecran inconnu) ? touche RETOUR de l'emulateur : le menu du combat s'ouvre
                      (Exit Stage ; en Night Ops il faut un 2e Retour pour avoir "Abandonning Stage")
  - La touche Retour n'est JAMAIS envoyee sur le menu principal (ca ouvrirait "quitter le jeu").
  - Menu principal = l'icone "ua" visible ET le bouton home absent (templates_nightops/ua.png).
  - Bouton home    = templates/home.png (capture du bouton, meme resolution que l'emulateur).
  - Si le bouton home est aussi present sur le menu principal : apres un tap qui ne change plus l'ecran, on considere
    qu'on y est deja.
  - Ecran de resultats (Challenge Again) : le bouton "Exit Stage" de cet ecran (templates_hero/exit.png) est tape directement.
  - Jamais bloquant : si on n'y arrive pas (combat en cours, popup inconnu...), go() renvoie False et le bot continue
    comme avant. Desactiver : --no-home.

Utilisation seule :
    python nav.py            # retourne au menu principal
    python nav.py --test     # UNE capture : score de ua / home / close / continue
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import fastmatch

BASE = Path(__file__).resolve().parent
HOME_THR = 0.74            # 0.80 ratait de peu (0.78) le bouton home de l'ecran de l'evenement
UA_THR = 0.85
POPUP_THR = 0.85
TAP_CD = 1.2            # s mini entre deux taps du meme bouton
SETTLE = 1.0            # pause apres un tap (l'ecran change)
SAME_DIFF = 2.0         # ecran "pas change" apres un tap si difference moyenne < a ca
BACK_WAIT = 3.0         # s sans rien reconnaitre ni taper avant d'envoyer (ou de renvoyer) Retour : laisse le menu s'ouvrir
BACK_MAX = 6            # touches Retour max par retour au menu
TIMEOUT = 60            # s max pour tout le retour au menu (Retour -> Exit Stage -> Retour -> Abandon -> home)


class _T:
    def __init__(self, path, crop=(0, 0, 1, 1), thr=0.85, mask_bg=False):
        full = cv2.imread(str(path), cv2.IMREAD_COLOR) if path and Path(path).exists() else None
        self.ok = full is not None
        self.thr = thr
        self.name = f"{Path(path).parent.name}/{Path(path).name}" if path else None    # derive.py
        self.img = None
        if self.ok:
            H, W = full.shape[:2]
            self.size, self.off = (W, H), (int(crop[0] * W), int(crop[1] * H))
            self.img = full[int(crop[1] * H):int(crop[3] * H), int(crop[0] * W):int(crop[2] * W)]
        # mask_bg : bouton pose sur la ville (le decor change) -> on ne compare que l'icone (fastmatch.background_mask)
        self.mask = fastmatch.background_mask(self.img) if (self.ok and mask_bg) else None
        fastmatch.prepare(self)                 # reconnaissance rapide (voir fastmatch.py)

    def prep(self, img):
        return img

    def best(self, screen):
        """-> (score, centre de l'image complete) ; reconnaissance rapide, meme score qu'avant."""
        if not self.ok:
            return 0.0, None
        sc, loc = fastmatch.best(self, screen)
        W, H = self.size
        return sc, (loc[0] - self.off[0] + W / 2, loc[1] - self.off[1] + H / 2)

    def find(self, screen):
        sc, pt = self.best(screen)
        return (pt[0], pt[1], sc) if sc >= self.thr else None


def _sig(screen):
    return cv2.resize(cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY), (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)


class Home:
    def __init__(self, shot, tap, log, back=None, base=BASE):
        """back : fonction sans argument qui envoie la touche Retour de l'emulateur (adb shell input keyevent 4)."""
        self.shot, self.tap, self.log, self.back = shot, tap, log, back
        self.t = {
            "ua": _T(Path(base) / "templates_nightops" / "ua.png", crop=(0, 0.30, 1, 1), thr=UA_THR),
            "home": _T(Path(base) / "templates" / "home.png", crop=(0.1, 0.1, 0.9, 0.9), thr=HOME_THR),
            "abandon": _T(Path(base) / "templates" / "abandonno.png", crop=(0.1, 0.2, 0.9, 0.8), thr=POPUP_THR),
            "exit": _T(Path(base) / "templates" / "exitstage.png", crop=(0.1, 0.2, 0.9, 0.8), thr=0.78),
            # bouton PAUSE du combat (rond blanc, en haut a droite) : il ouvre le menu ou se trouve "Exit Stage"
            "pause": _T(Path(base) / "templates" / "pause.png", thr=0.85),
            # bouton "Leave" d'un dialogue avec un personnage de la ville (si un clic a lance une discussion)
            "leave": _T(Path(base) / "templates" / "leave.png", thr=0.85),
            # "Exit Stage" de l'ecran de resultats (a cote de Challenge Again) : bouton plus petit que celui du menu Retour
            "exit2": _T(Path(base) / "templates_hero" / "exit.png", crop=(0.1, 0.2, 0.9, 0.8), thr=0.80),
            "close": _T(Path(base) / "templates" / "close.png", thr=POPUP_THR),
            # fenetre "Daily Sign-in" (1er lancement du jour) : bouton Sign In, puis grande croix rouge en haut a droite
            "signin": _T(Path(base) / "templates" / "signin.png", thr=0.85),
            "xpopup": _T(Path(base) / "templates" / "xpopup.png", thr=0.75),
            "continue": _T(Path(base) / "templates" / "continue.png", thr=POPUP_THR),
        }

    def find_pause(self, screen):
        """Bouton pause du combat, cherche UNIQUEMENT dans le coin haut-droit de l'ecran. -> (x, y, score) ou None."""
        t = self.t["pause"]
        if not t.ok:
            return None
        H, W = screen.shape[:2]
        x0 = int(0.90 * W)
        t.last = None
        h = t.find(screen[0:int(0.12 * H), x0:])
        return (h[0] + x0, h[1], h[2]) if h else None

    def find_exit_relaxed(self, screen):
        """Menu pause ouvert : cherche "Exit Stage" de facon tolerante (niveaux de gris, plusieurs tailles, seuil plus bas).
        Images essayees : templates/exitpause.png (facultative, prioritaire), exitstage.png, templates_hero/exit.png."""
        if not hasattr(self, "_exit_imgs"):
            self._exit_imgs = []
            for f in (Path(BASE) / "templates" / "exitpause.png", Path(BASE) / "templates" / "exitstage.png",
                      Path(BASE) / "templates_hero" / "exit.png"):
                im = cv2.imread(str(f)) if f.exists() else None
                if im is not None:
                    h, w = im.shape[:2]
                    crop = im[int(0.2 * h):int(0.8 * h), int(0.1 * w):int(0.9 * w)]          # le texte du bouton
                    self._exit_imgs.append((f.name, cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)))
        g = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        best = (0.0, None, "")
        for name, im in self._exit_imgs:
            for k in (1.0, 0.9, 1.1, 0.8, 1.25):
                t = im if k == 1.0 else cv2.resize(im, None, fx=k, fy=k, interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
                if t.shape[0] >= g.shape[0] or t.shape[1] >= g.shape[1]:
                    continue
                r = cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)
                _, sc, _, loc = cv2.minMaxLoc(r)
                if sc > best[0]:
                    best = (float(sc), (loc[0] + t.shape[1] / 2, loc[1] + t.shape[0] / 2), name)
        return (best[1][0], best[1][1], best[0], best[2]) if best[0] >= 0.70 else (None, None, best[0], best[2])

    def _shot_debug(self, name, screen):
        try:
            d = Path(BASE) / "debug"
            d.mkdir(exist_ok=True)
            f = d / f"{name}_{time.strftime('%H%M%S')}.png"
            cv2.imwrite(str(f), screen)
            self.log(f"(debug) capture : debug/{f.name}")
        except Exception:
            pass

    def on_main(self, screen):
        if self.t["signin"].find(screen) or self.t["xpopup"].find(screen):
            return False                                       # une fenetre (Daily Sign-in) recouvre le menu
        return bool(self.t["ua"].find(screen)) and not self.t["home"].find(screen)

    def go(self, timeout=TIMEOUT):
        """-> True si on est sur le menu principal, False sinon (jamais d'exception : le bot continue comme avant)."""
        if not self.t["home"].ok or not self.t["ua"].ok:
            self.log("(!) retour au menu : templates/home.png ou templates_nightops/ua.png manquant, on continue sans.")
            return False
        t0, last, taps, backs = time.time(), {}, 0, 0
        t_act = time.time()                                    # derniere action (tap ou Retour)
        while time.time() - t0 < timeout:
            screen = self.shot()
            if self.on_main(screen):
                if taps or backs:
                    self.log("menu principal atteint")
                return True
            acted = False
            for key in ("signin", "close", "continue", "xpopup", "leave", "abandon", "exit", "exit2", "home"):   # popups, sortie, home
                h = self.t[key].find(screen)
                if h and time.time() - last.get(key, 0) > TAP_CD:
                    before = _sig(screen)
                    self.tap(h[0], h[1])
                    last[key] = t_act = time.time()
                    self.log(f"retour au menu : {key} ({h[2]:.2f})")
                    acted = True
                    time.sleep(SETTLE)
                    if key == "home":
                        taps += 1
                        after = self.shot()
                        # home present partout, ecran inchange apres le tap et "ua" visible : on est deja sur le menu
                        if self.t["ua"].find(after) and float(np.abs(_sig(after) - before).mean()) < SAME_DIFF:
                            self.log("menu principal atteint")
                            return True
                    else:
                        taps += 1
                    break
            if acted:
                continue
            # en plein combat : le bouton PAUSE (coin haut-droit) ouvre le menu qui contient "Exit Stage"
            p = self.find_pause(screen)
            if p and time.time() - last.get("pause", 0) > 4.0:
                self.tap(p[0], p[1])
                last["pause"] = t_act = time.time()
                taps += 1
                self.log(f"retour au menu : combat en cours -> bouton pause ({p[2]:.2f})")
                time.sleep(SETTLE + 0.5)
                # menu pause : on y cherche "Exit Stage" pendant 6 s, sans Retour ni 2e appui sur pause (ca refermerait le menu)
                t_p, found = time.time(), False
                while time.time() - t_p < 6 and not found:
                    sp = self.shot()
                    if any(self.t[k].find(sp) for k in ("abandon", "exit", "exit2")):
                        found = True                           # reconnu normalement : la boucle principale le tape
                        break
                    x, y, sc, name = self.find_exit_relaxed(sp)
                    if x is not None:
                        self.tap(x, y)
                        last["exit"] = t_act = time.time()
                        self.log(f"retour au menu : Exit Stage du menu pause ({sc:.2f}, {name})")
                        time.sleep(SETTLE)
                        found = True
                        break
                    time.sleep(0.5)
                if not found:
                    self.log(f"(!) menu pause : Exit Stage non reconnu (meilleur score {sc:.2f})")
                    if not getattr(self, "_pause_shot", False):
                        self._pause_shot = True
                        self._shot_debug("nav_pause", sp)
                    last["pause"] = time.time() + 8            # on laisse le menu ouvert avant de retenter
                continue
            # rien de reconnu : combat en cours / ecran inconnu -> touche Retour (menu du combat), avec un delai pour le laisser s'ouvrir
            if self.back and time.time() - t_act > BACK_WAIT and backs < BACK_MAX:
                self.back()
                backs += 1
                t_act = time.time()
                self.log(f"retour au menu : touche Retour ({backs}/{BACK_MAX})")
                time.sleep(SETTLE)
                continue
            time.sleep(0.5)
        self.log("(!) menu principal non atteint (popup inconnu ?) : on continue quand meme.")
        try:                                                   # capture de l'ecran bloque, pour savoir quel bouton manque
            d = Path(BASE) / "debug"
            d.mkdir(exist_ok=True)
            f = d / f"nav_{time.strftime('%H%M%S')}.png"
            cv2.imwrite(str(f), self.shot())
            self.log(f"(debug) capture de l'ecran bloque : debug/{f.name}")
        except Exception:
            pass
        return False


class Reprise:
    """Ecran inconnu : apres IDLE s sans action ET sans aucune image reconnue (hors combat) ->
    capture (incident, envoye sur Discord) + retour au menu principal ; le mode repart de zero, comme apres le garde-chien.
    Au plus MAX reprises par WINDOW s : au-dela, run() renvoie False et l'arret d'origine (IDLE_LIMIT) reprend la main."""
    IDLE = 60
    MAX = 3
    WINDOW = 1800

    def __init__(self, home, log):
        self.home, self.log, self.times = home, log, []

    def due(self, idle_s):
        return idle_s > self.IDLE

    def run(self, why="aucune image reconnue"):
        """-> True si une reprise a ete faite (le mode doit remettre son etat a zero et continuer)."""
        now = time.time()
        self.times = [t for t in self.times if now - t < self.WINDOW]
        if len(self.times) >= self.MAX:
            return False
        self.times.append(now)
        n = len(self.times)
        try:
            import journal
            journal.incident(f"Écran inconnu depuis {self.IDLE}s ({why}) : retour au menu principal et reprise "
                             f"({n}/{self.MAX} en {self.WINDOW // 60} min)", live=True, kind="reprise")
        except Exception:
            pass
        self.log(f"=== ECRAN INCONNU depuis {self.IDLE}s : retour au menu principal, puis reprise ({n}/{self.MAX}) ===")
        ok = self.home.go()
        if not ok:
            self.log("(!) menu principal non atteint : le mode reessaie quand meme")
        return True


def run_test(shot):
    fastmatch.FAST = False                 # --test : scores exacts (comparaison couleur partout)
    h = Home(shot, lambda x, y: None, print)
    screen = shot()
    print(f"Capture : {screen.shape[1]}x{screen.shape[0]}")
    for k, t in h.t.items():
        sc, pt = t.best(screen)
        print(f"{k:<10}{sc:>6.2f}  seuil {t.thr:.2f}  {'TROUVE ' + str(tuple(int(v) for v in pt)) if t.ok and sc >= t.thr else '-' if t.ok else 'fichier manquant'}")
    print("-> menu principal" if h.on_main(screen) else "-> pas sur le menu principal")


if __name__ == "__main__":
    import auto_nightops as nt
    nt.init_adb()
    if "--test" in sys.argv:
        run_test(nt.screenshot)
    else:
        print("OK, menu principal." if Home(nt.screenshot, nt.tap, nt.log, back=lambda: nt.adb("shell", "input", "keyevent", "4")).go()
              else "Menu principal non atteint.")
