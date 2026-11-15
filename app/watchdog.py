"""
Chien de garde : repere une maintenance du serveur ou un jeu bloque, et relance le jeu tout seul.

  1. MAINTENANCE / RETOUR A L'ECRAN D'ACCUEIL (le serveur a coupe, le jeu est revenu au titre)
       La barre du serveur ("Server 1 OPEN BETA", iconverte.png) porte une PASTILLE a gauche :
         pastille ORANGE = serveur en maintenance -> le bot attend (capture toutes les 20 s, jusqu'a 8 h)
         pastille VERTE  = serveur rouvert        -> le bot tape AU MILIEU DE L'ECRAN, ferme les annonces,
                                                     revient au menu principal, et le script reprend.
       La couleur est lue directement sur la pastille : une seule image (iconverte.png) suffit pour les deux etats.
  2. BLOQUE : aucun Challenge Again / Continue / Start depuis 5 min (reglage "watchdog_stall", en secondes)
       -> le bot FERME le jeu, puis refait toute la sequence de lancement :
          pub de l'emulateur -> croixpub.png  |  icone du jeu -> iconeacc.png  |  bouton understood.png
          |  attente de la pastille VERTE  |  "Tap to continue"  |  annonces  |  menu principal.
  3. Le jeu n'est plus au premier plan (plantage, bureau de l'emulateur) -> meme sequence.
  Garde-fou : 3 recuperations en moins de 30 min sans aucun progres entre deux -> le bot s'arrete au lieu de boucler.

Option (desactivee par defaut) : case dans l'assistant | /watchdog sur Discord | --watchdog / --no-watchdog
  config.json : "watchdog" (true/false), "watchdog_stall" (300 s), "maintenance_max_h" (8 h d'attente maxi),
                "game_package" (detecte tout seul au 1er lancement si le jeu est a l'ecran),
                "tap_continue" ([0.5, 0.5] : ou taper quand la pastille est verte, en fraction de l'ecran)

Images (dossier templates_watchdog/, a la meme resolution que l'emulateur) :
  iconverte.png   la barre du serveur avec la pastille VERTE (sert aussi a reconnaitre la pastille orange)
  croixpub.png    la croix qui ferme la pub de l'emulateur
  iconeacc.png    l'icone du jeu sur le bureau de l'emulateur
  understood.png  le bouton "Understood!" au lancement
  maintenance.png le bouton "Back to login" qui s'affiche quand le serveur passe en maintenance (le bot appuie dessus)
  restart.png     le bouton "Restart" qui peut s'afficher ensuite sur l'ecran d'accueil (le bot appuie dessus)
  tapcontinue.png (facultatif) le texte "Tap to continue" ; sans lui, le bot tape a "tap_continue"

Tests :  python watchdog.py --test   scores des images sur l'ecran actuel, couleur de la pastille, paquet du jeu
         python watchdog.py --now    ferme et relance le jeu tout de suite, puis revient au menu principal
"""
import json
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import nav

BASE = Path(__file__).resolve().parent

# La barre du serveur est reconnue SANS sa pastille (crop) : la meme image marche donc en vert comme en orange.
BAR_CROP = (0.11, 0.0, 0.88, 1.0)      # texte "Server 1 ..." seul (ni la pastille a gauche, ni les fleches a droite)
HEX_C = (0.0672, 0.4714)               # centre de la pastille, en fraction de la barre entiere
HEX_R = (0.030, 0.26)                  # demi-largeur / demi-hauteur de la zone lue sur la pastille

SPEC = {
    "iconverte":   dict(thr=0.75, crop=BAR_CROP),
    # boutons jaunes : on compare le TEXTE seul (le fond est le meme que "Confirm", "Return", "Add"... qui marquaient
    # jusqu'a 0,95 en comparant le bouton entier)
    "maintenance": dict(thr=0.85, crop=(0.15, 0.15, 0.85, 0.85)),      # "Back to login" (debut de maintenance)
    "restart":     dict(thr=0.88, crop=(0.15, 0.15, 0.85, 0.85)),      # "Restart" (redemarrer le jeu)
    "croixpub":    dict(thr=0.80),
    "iconeacc":    dict(thr=0.80),
    "understood":  dict(thr=0.80),
    "tapcontinue": dict(thr=0.75),
}
OPTIONAL = ("tapcontinue", "maintenance", "restart")
STALL_S = 300                  # s sans progres (hors combat) -> fermeture / relance du jeu
BATTLE_FACTOR = 4              # EN COMBAT, on attend BEAUCOUP plus longtemps : un combat d'evenement peut durer >5 min,
                               # et relancer le jeu a ce moment-la ressemble a un crash. Les autres controles (maintenance,
                               # ecran d'accueil, jeu ferme) restent immediats.
MAX_RESTARTS = 3               # recuperations max sans progres entre deux, dans WINDOW
WINDOW = 1800                  # s
MOVE_DIFF = 2.0                # difference moyenne entre deux captures au-dela de laquelle "l'image bouge"
FG_EVERY = 30                  # s entre deux verifications "le jeu est-il au premier plan ?"
MAINT_POLL = 20                # s entre deux captures pendant la maintenance
LOGIN_TIMEOUT = 420            # s max pour revenir au menu principal (l'attente de la pastille verte ne compte pas)
TAP_CONTINUE = (0.5, 0.5)      # pastille verte -> on tape AU MILIEU DE L'ECRAN (jamais sur la barre du serveur : la
                               # toucher ouvre le choix du serveur). Reglable : "tap_continue" dans config.json
GENERIC_TAPS = ((0.5, 0.80), (0.5, 0.55))      # sans aucune image : tap au centre-bas puis au centre, en alternance
NOT_GAME = re.compile(r"launcher|home|systemui|inputmethod|settings|nemu|mumu|android$", re.I)


def _cfg():
    try:
        return json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cfg(**kv):
    c = _cfg()
    c.update(kv)
    (BASE / "config.json").write_text(json.dumps(c, indent=2), encoding="utf-8")


def enabled():
    """--watchdog / --no-watchdog (prioritaire), sinon config.json "watchdog", sinon desactive."""
    if "--no-watchdog" in sys.argv:
        return False
    if "--watchdog" in sys.argv:
        return True
    return bool(_cfg().get("watchdog", False))


def foreground_package(adb):
    """Paquet de l'appli au premier plan de l'emulateur ('' si on n'arrive pas a le savoir)."""
    for cmd in ("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'",
                "dumpsys activity activities | grep -E 'ResumedActivity|topResumedActivity'"):
        try:
            out = adb("shell", cmd)
        except Exception:
            continue
        for m in re.finditer(r"([A-Za-z][\w]*(?:\.[\w]+)+)/", out):
            return m.group(1)
    return ""


class Watchdog:
    def __init__(self, shot, tap, adb, log, Done, base=BASE, home=None):
        self.shot, self.tap, self.adb, self.log, self.Done = shot, tap, adb, log, Done
        self.base = Path(base)
        c = _cfg()
        self.stall = max(60, int(c.get("watchdog_stall", STALL_S) or STALL_S))
        self.battle_stall = max(self.stall, int(c.get("watchdog_stall_battle", self.stall * BATTLE_FACTOR) or self.stall * BATTLE_FACTOR))
        self.maint_max = float(c.get("maintenance_max_h", 8) or 8) * 3600
        self.pkg = str(c.get("game_package") or "")
        tc = c.get("tap_continue") or TAP_CONTINUE
        self.tap_continue = (float(tc[0]), float(tc[1]))
        self.home = home or nav.make(shot, tap, adb, log, base=base)
        self.t = {k: nav._T(self.base / "templates_watchdog" / f"{k}.png", crop=s.get("crop", (0, 0, 1, 1)), thr=s["thr"])
                  for k, s in SPEC.items()}
        self.last_progress = time.time()
        self.last_move, self.sig = time.time(), None            # derniere fois que l'image a bouge (combat vivant)
        self.last_fg = 0.0
        self.restarts = []                                      # heures des recuperations recentes
        self.n_restarts = 0

    # ------------------------------------------------------------ etat du bot
    def kick(self):
        """Du progres a ete vu (Challenge Again, Continue, Start...) : le chrono 'bloque' repart a zero."""
        self.last_progress = time.time()
        self.restarts = []

    def missing(self):
        return [f"templates_watchdog/{k}.png" for k, t in self.t.items() if not t.ok and k not in OPTIONAL]

    def ensure_package(self):
        """Memorise le paquet du jeu (a l'ecran au demarrage du bot) : sans lui, pas de fermeture / relance."""
        if self.pkg:
            return self.pkg
        p = foreground_package(self.adb)
        if p and not NOT_GAME.search(p):
            self.pkg = p
            try:
                _save_cfg(game_package=p)
            except Exception:
                pass
            self.log(f"watchdog : jeu detecte = {p} (memorise dans config.json)")
        else:
            self.log("(!) watchdog : paquet du jeu inconnu (le jeu n'etait pas a l'ecran ?). Tu peux l'ecrire dans config.json "
                     "(\"game_package\"). Sans lui, le bot ne peut pas FERMER le jeu : il le relancera par l'icone du bureau.")
        return self.pkg

    # ------------------------------------------------------------ lecture de l'ecran
    def see(self, key, screen):
        t = self.t[key]
        return t.find(screen) if t.ok else None

    def bar(self, screen):
        """Barre du serveur sur l'ecran d'accueil -> ((x, y) du centre de la barre, score) ou None."""
        h = self.see("iconverte", screen)
        return ((h[0], h[1]), h[2]) if h else None

    def bar_color(self, screen, center):
        """Couleur de la pastille a gauche de la barre -> 'verte' | 'orange' | '?'."""
        W, H = self.t["iconverte"].size                         # taille de la barre entiere
        cx = center[0] + (HEX_C[0] - 0.5) * W
        cy = center[1] + (HEX_C[1] - 0.5) * H
        rx, ry = max(3, HEX_R[0] * W), max(3, HEX_R[1] * H)
        y0, y1 = int(max(0, cy - ry)), int(min(screen.shape[0], cy + ry))
        x0, x1 = int(max(0, cx - rx)), int(min(screen.shape[1], cx + rx))
        if y1 - y0 < 3 or x1 - x0 < 3:
            return "?"
        hsv = cv2.cvtColor(screen[y0:y1, x0:x1], cv2.COLOR_BGR2HSV).reshape(-1, 3)
        px = hsv[(hsv[:, 1] > 70) & (hsv[:, 2] > 70)]           # on ignore le fond noir de la barre
        if len(px) < 8:
            return "?"
        hue = int(np.median(px[:, 0]))                          # OpenCV : teinte sur 0-179
        if 35 <= hue <= 95:
            return "verte"
        if hue <= 30 or hue >= 170:
            return "orange"
        return "?"

    def screen_state(self, screen):
        """Ecran hors du jeu normal -> 'maintenance' | 'accueil' | 'bureau' | None."""
        if self.see("maintenance", screen):                     # "Back to login" : le serveur vient de passer en maintenance
            return "maintenance"
        if self.see("restart", screen):
            return "accueil"
        b = self.bar(screen)
        if b:
            return "maintenance" if self.bar_color(screen, b[0]) == "orange" else "accueil"
        if self.see("understood", screen):
            return "accueil"
        if self.see("iconeacc", screen) or self.see("croixpub", screen):
            return "bureau"
        return None

    # ------------------------------------------------------------ surveillance (appelee a chaque tour de boucle)
    def poll(self, screen, in_battle=False, frozen_only=False):
        """-> True si une recuperation a eu lieu (le bot doit alors remettre son etat a zero et repartir du menu).
        in_battle : le bot est en plein combat -> on laisse BEAUCOUP plus de temps avant de relancer le jeu."""
        now = time.time()
        st = self.screen_state(screen)
        if st:
            why = {"maintenance": "serveur en maintenance (pastille orange)",
                   "accueil": "retour a l'ecran d'accueil du jeu",
                   "bureau": "le jeu s'est ferme (bureau de l'emulateur)"}[st]
            return self.recover(why, restart=False)             # le jeu est deja ferme / au titre : inutile de le re-fermer
        if self.pkg and now - self.last_fg > FG_EVERY:
            self.last_fg = now
            fg = foreground_package(self.adb)
            if fg and fg != self.pkg and not fg.startswith(self.pkg):
                return self.recover(f"le jeu n'est plus au premier plan ({fg})", restart=True)
        sig = cv2.resize(cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY), (48, 27), interpolation=cv2.INTER_AREA).astype(np.float32)
        if self.sig is None or float(np.abs(sig - self.sig).mean()) > MOVE_DIFF:
            self.last_move = now                                # l'image bouge : le jeu n'est pas fige
        self.sig = sig
        if frozen_only:
            # modes sans bouton Challenge Again (Agency Assessment) : seul critere = l'ecran ne change plus du tout
            if now - self.last_move > self.stall:
                return self.recover(f"l'ecran ne change plus depuis {int((now - self.last_move) // 60)} min", restart=True)
            return False
        if in_battle:
            # un combat qui BOUGE est un combat vivant : on ne le coupe jamais (c'etait pris pour un crash).
            # On ne relance que si l'ecran est fige depuis 5 min, ou sans aucun vrai progres depuis tres longtemps.
            if now - self.last_move > self.stall:
                return self.recover(f"combat fige : l'ecran ne bouge plus depuis {int((now - self.last_move) // 60)} min", restart=True)
            if now - self.last_progress > self.battle_stall * 3:
                return self.recover(f"aucun Challenge Again depuis {int((now - self.last_progress) // 60)} min (combat en cours)", restart=True)
        elif now - self.last_progress > self.stall:
            return self.recover(f"aucun Continue Challenge / Challenge Again depuis {int((now - self.last_progress) // 60)} min", restart=True)
        return False

    # ------------------------------------------------------------ recuperation
    def recover(self, reason, restart):
        now = time.time()
        self.restarts = [t for t in self.restarts if now - t < WINDOW]
        if len(self.restarts) >= MAX_RESTARTS:
            raise self.Done(f"Watchdog : {MAX_RESTARTS} recuperations en moins de {WINDOW // 60} min sans aucun progres entre deux "
                            f"({reason}) : arret. Regarde les captures dans debug/ pour voir ce qui bloque.")
        self.restarts.append(now)
        self.n_restarts += 1
        self.log(f"=== WATCHDOG : {reason} ===")
        self._debug("avant")
        if restart:
            self.close_game()
        ok = self.login()
        self.last_progress = self.last_move = time.time()
        if not ok:
            self._debug("echec")
            raise self.Done("Watchdog : impossible de revenir au menu principal apres la relance. Verifie les images de "
                            "templates_watchdog/ (capture de l'ecran bloque dans debug/).")
        self.log("watchdog : menu principal retrouve, le script reprend.")
        return True

    def close_game(self):
        if not self.pkg:
            self.log("(!) watchdog : paquet du jeu inconnu : je ne peux pas le fermer, je tente la relance telle quelle.")
            return
        self.log(f"watchdog : fermeture du jeu ({self.pkg})")
        try:
            self.adb("shell", "am", "force-stop", self.pkg)
        except Exception as e:
            self.log(f"(!) fermeture : {e}")
        time.sleep(4)

    def launch_package(self):
        """Lancement par le nom du paquet (secours quand l'icone du bureau n'est pas reconnue)."""
        if not self.pkg:
            return False
        self.log("watchdog : lancement du jeu (par le paquet)")
        try:
            self.adb("shell", "monkey", "-p", self.pkg, "-c", "android.intent.category.LAUNCHER", "1")
        except Exception as e:
            self.log(f"(!) lancement : {e}")
            return False
        time.sleep(10)
        return True

    def login(self):
        """Du bureau de l'emulateur (ou de l'ecran d'accueil) jusqu'au menu principal.
        pub -> icone du jeu -> Understood -> attente de la pastille VERTE -> Tap to continue -> annonces -> menu."""
        t_seen = time.time()                                    # derniere chose reconnue : sert au delai (hors attente du serveur)
        wait0 = None                                            # debut de l'attente de la pastille verte (maintenance)
        last_tap, last_log, launched, n_generic, taps_cont = 0.0, 0.0, 0.0, 0, 0
        have_imgs = any(self.t[k].ok for k in ("iconverte", "iconeacc", "understood"))
        while True:
            now = time.time()
            if now - last_tap < 1.5:
                time.sleep(0.4)
                continue
            s = self.shot()
            if self.home.on_main(s):
                return True
            if wait0 is not None and now - wait0 > self.maint_max:
                raise self.Done(f"Watchdog : serveur toujours ferme apres {int(self.maint_max // 3600)} h "
                                f"(la pastille n'est jamais redevenue verte) : arret.")
            if wait0 is None and now - t_seen > LOGIN_TIMEOUT:
                return False

            # 1) pub de l'emulateur, bouton Understood, icone du jeu
            for key, what in (("maintenance", "maintenance : bouton Back to login"), ("restart", "bouton Restart : le jeu redemarre"),
                              ("croixpub", "pub de l'emulateur fermee"), ("understood", "bouton Understood"),
                              ("iconeacc", "icone du jeu : lancement")):
                h = self.see(key, s)
                if h:
                    self.log(f"watchdog : {what} ({h[2]:.2f})")
                    self.tap(h[0], h[1])
                    last_tap = t_seen = time.time()
                    wait0 = None
                    if key in ("iconeacc", "restart"):
                        launched = time.time()
                        time.sleep(8)
                    elif key == "maintenance":
                        time.sleep(4)
                    break
            else:
                # 2) barre du serveur : verte -> on entre, orange -> on attend
                b = self.bar(s)
                if b:
                    col = self.bar_color(s, b[0])
                    t_seen = now
                    if col == "orange":
                        if wait0 is None:
                            wait0 = last_log = now
                            self.log("watchdog : serveur en MAINTENANCE (pastille orange) : j'attends qu'elle redevienne verte...")
                        elif now - last_log > 300:
                            last_log = now
                            m = int((now - wait0) // 60)
                            self.log(f"watchdog : toujours en maintenance ({m // 60} h {m % 60:02d} min)")
                        time.sleep(MAINT_POLL)
                        continue
                    if wait0 is not None:
                        self.log(f"watchdog : pastille VERTE, serveur rouvert (attente : {int((now - wait0) // 60)} min)")
                        wait0 = None
                    h = self.see("tapcontinue", s)
                    H, W = s.shape[:2]
                    if h:
                        x, y, how = h[0], h[1], "image"
                    else:
                        x, y, how = self.tap_continue[0] * W, self.tap_continue[1] * H, "milieu de l'ecran"
                        bw, bh = self.t["iconverte"].size
                        if abs(y - b[0][1]) < bh and abs(x - b[0][0]) < bw / 2 + 20:   # le milieu tombe SUR la barre :
                            y, how = b[0][1] + 2.2 * bh, "sous la barre"                 # on descend juste dessous
                    taps_cont += 1
                    self.log(f"watchdog : Tap to continue ({how})")
                    self.tap(x, y)
                    last_tap = time.time()
                    time.sleep(2.5)
                    if taps_cont > 25:                          # la barre ne disparait pas : mauvaise position de tap
                        raise self.Done("Watchdog : 'Tap to continue' sans effet (25 essais). Fournis "
                                        "templates_watchdog/tapcontinue.png (une capture du texte), ou regle "
                                        "\"tap_continue\" dans config.json (ex. [0.5, 0.9]).")
                    continue
                # 3) annonces / popups de connexion
                p = self._popup(s)
                if p:
                    self.log(f"watchdog : annonce fermee ({p[0]})")
                    self.tap(p[1][0], p[1][1])
                    last_tap = t_seen = time.time()
                elif self.home.t["home"].find(s) and now - last_tap > 8:
                    self.home.go(timeout=30)                    # deja dans le jeu, mais pas sur le menu principal
                    last_tap = t_seen = time.time()
                elif self.pkg and now - max(launched, last_tap) > 20 and not self._in_game():
                    if self.launch_package():                   # ni icone, ni jeu a l'ecran
                        launched = last_tap = t_seen = time.time()
                elif not have_imgs and now - last_tap > 6:
                    H, W = s.shape[:2]
                    gx, gy = GENERIC_TAPS[n_generic % len(GENERIC_TAPS)]
                    n_generic += 1
                    self.tap(gx * W, gy * H)                    # aucune image fournie : tap generique
                    last_tap = time.time()
            time.sleep(1.0)

    def _popup(self, s):
        for key in ("signin", "close", "continue", "xpopup"):    # Daily Sign-in : on signe, puis on ferme avec la croix
            t = self.home.t.get(key)
            p = t.find(s) if t is not None and t.ok else None
            if p:
                return key, p
        return None

    def _in_game(self):
        if not self.pkg:
            return True
        fg = foreground_package(self.adb)
        return (not fg) or fg == self.pkg or fg.startswith(self.pkg)

    def _debug(self, name):
        try:
            d = self.base / "debug"
            d.mkdir(exist_ok=True)
            cv2.imwrite(str(d / f"watchdog_{name}_{time.strftime('%H%M%S')}.png"), self.shot())
        except Exception:
            pass


def make(shot, tap, adb, log, Done):
    """Cree le chien de garde si l'option est active ; None sinon."""
    if not enabled():
        return None
    wd = Watchdog(shot, tap, adb, log, Done)
    wd.ensure_package()
    miss = wd.missing()
    if miss:
        log("(!) Chien de garde : images manquantes (" + ", ".join(m.split("/")[1] for m in miss)
            + ") : la relance du jeu risque d'echouer.")
    log(f"Chien de garde : ACTIF (relance le jeu si rien ne progresse en {wd.stall // 60} min ; maintenance : attente de la pastille verte)")
    return wd


def run_test(shot, adb):
    nav.fastmatch.FAST = False                 # --test : scores exacts (comparaison couleur partout)
    w = Watchdog(shot, lambda x, y: None, adb, print, RuntimeError)
    s = shot()
    print(f"Capture : {s.shape[1]}x{s.shape[0]} | actif : {enabled()} | paquet du jeu : {w.pkg or '(inconnu)'} "
          f"| au premier plan : {foreground_package(adb) or '?'}")
    for k, t in w.t.items():
        sc = t.best(s)[0] if t.ok else 0
        print(f"{k:<12}{sc:>6.2f}  seuil {SPEC[k]['thr']:.2f}  "
              f"{'TROUVE' if t.ok and sc >= SPEC[k]['thr'] else '-' if t.ok else 'fichier manquant'}")
    b = w.bar(s)
    print(f"pastille du serveur : {w.bar_color(s, b[0]) if b else 'barre non visible'}")
    print("etat :", w.screen_state(s) or "aucun (le jeu tourne normalement)")


if __name__ == "__main__":
    import auto_nightops as nt
    nt.init_adb()
    if "--test" in sys.argv:
        run_test(nt.screenshot, nt.adb)
    elif "--now" in sys.argv:
        w = Watchdog(nt.screenshot, nt.tap, nt.adb, nt.log, nt.Done)
        w.ensure_package()
        try:
            w.recover("test manuel (--now)", restart=True)
        except nt.Done as e:
            print(e)
    else:
        print(__doc__)
