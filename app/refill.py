"""
Recharge de stamina avec le code cadeau (BETASTAMINA : +1000 de stamina par utilisation, 15 fois max = 15000).

Declencheurs (ecrans reconnus pendant un multi-run) :
  A. insustam.png   bouton jaune "Insufficient Stamina" a la place de "Continue Challenge"
                    -> on clique sur "Exit" (le bouton a droite), puis menu principal
  B. insustam2.png  message "Insufficient stamina." (on lance sans stamina)
                    -> on clique sur la croix rouge en haut a droite, puis menu principal
  C. morestam.png   grande fenetre "Stamina Supply" (croix rouge en haut a droite : closestam.png)
                    -> on ferme la fenetre, puis menu principal
Ensuite : menu principal -> cadeau.png -> liste de gauche : on descend jusqu'a code.png ("Gift Pack Exchange")
-> fenetre Hero Box (codearea.png) : clic sur "Please enter the pick-up code.", on tape le code, clic en dehors du champ,
clic sur Take (take.png) ; on recommence 15 fois. Puis retour au menu principal et le bot reprend son travail.

Option (facultative, desactivee par defaut) :
  - assistant : case "Recharger la stamina avec le code"   -> config.json : "stamina_refill": true
  - Discord   : /refill on|off                              -> meme cle dans config.json
  - ligne de commande : --refill / --no-refill (prioritaire sur config.json)
Autres reglages (config.json) : "refill_code" (defaut BETASTAMINA), "refill_count" (defaut 15).

Test sur l'emulateur :  python refill.py --test     (scores de chaque image, ne clique sur rien)
                        python refill.py --now      (lance la recharge tout de suite, depuis n'importe quel ecran)
"""
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import nav

BASE = Path(__file__).resolve().parent
TPL = BASE / "templates_stamina"
CODE = "BETASTAMINA"
COUNT = 15
MIN_GAP = 90                  # s : si l'ecran "plus de stamina" revient moins de 90 s apres une recharge -> on arrete (code epuise ?)

# crop = zone de l'image comparee (fractions) ; thr = seuil ; region = zone de l'ECRAN ou chercher (fractions x0,y0,x1,y1)
SPEC = {
    "insustam":  dict(crop=(0.08, 0.12, 0.92, 0.88), thr=0.85),
    "insustam2": dict(crop=(0.04, 0.12, 0.96, 0.88), thr=0.85),
    "morestam":  dict(crop=(0.0, 0.0, 0.22, 0.11), thr=0.85),            # titre "Stamina Supply"
    "closestam": dict(thr=0.80, region=(0.55, 0.0, 1.0, 0.5)),
    "cadeau":    dict(thr=0.80),
    "code":      dict(thr=0.85),
    "codearea":  dict(crop=(0.03, 0.50, 0.62, 0.78), thr=0.70),          # zone du champ (texte "Please enter...")
    "take":      dict(thr=0.85),
}
# decalages (en pixels, a la resolution des captures) : champ de saisie et zone neutre, par rapport au centre de Take.
# Mesures sur codearea.png (Take au centre (744,376), champ (344,376), titre "THANK YOU..." (470,215)).
FIELD_DX, OUT_DX, OUT_DY = -400, -274, -161
CLICK_POS = (0.415, 0.726)    # meme viseur que l'autoclick des bots : sert a faire disparaitre les popups de recompense apres Take
CLICK_BURST = 2


def _cfg():
    try:
        return json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def enabled():
    """--refill / --no-refill sur la ligne de commande, sinon config.json ("stamina_refill"), sinon desactive."""
    if "--no-refill" in sys.argv:
        return False
    if "--refill" in sys.argv:
        return True
    return bool(_cfg().get("stamina_refill", False))


def settings():
    c = _cfg()
    code = str(c.get("refill_code") or CODE).strip() or CODE
    try:
        n = max(1, min(15, int(c.get("refill_count", COUNT))))
    except (TypeError, ValueError):
        n = COUNT
    return code, n


class Refill:
    def __init__(self, shot, tap, adb, log, Done, base=BASE, home=None):
        self.shot, self.tap, self.adb, self.log, self.Done = shot, tap, adb, log, Done
        self.base = Path(base)
        self.code, self.count = settings()
        self.home = home or nav.Home(shot, tap, log, back=self._back, base=base)
        self.t = {k: nav._T(self.base / "templates_stamina" / f"{k}.png", crop=s.get("crop", (0, 0, 1, 1)), thr=s["thr"])
                  for k, s in SPEC.items()}
        self.exit_t = nav._T(self.base / "templates_hero" / "exit.png", crop=(0.1, 0.2, 0.9, 0.8), thr=0.80)
        self.typing = False                                     # True pendant la saisie du code : autoclick COUPE
        self.done_t = 0.0                                       # heure de la derniere recharge terminee
        self.times = 0

    # ------------------------------------------------------------ utilitaires
    def _back(self):
        self.adb("shell", "input", "keyevent", "4")

    def missing(self):
        return [f"templates_stamina/{k}.png" for k, t in self.t.items() if not t.ok]

    def find(self, key, screen):
        """-> (x, y, score) ou None ; tient compte de la zone de l'ecran (region) de l'image."""
        reg = SPEC[key].get("region")
        if reg:
            H, W = screen.shape[:2]
            x0, y0 = int(reg[0] * W), int(reg[1] * H)
            h = self.t[key].find(screen[y0:int(reg[3] * H), x0:int(reg[2] * W)])
            return (h[0] + x0, h[1] + y0, h[2]) if h else None
        return self.t[key].find(screen)

    def find_cross(self, screen):
        """Croix rouge de fermeture (haut a droite). 1) image closestam.png 2) la meme a plusieurs echelles
        3) detection par la couleur : plus grosse tache rouge ~carree dans le quart haut-droit. -> (x, y, score) ou None."""
        h = self.find("closestam", screen)
        if h:
            return h
        H, W = screen.shape[:2]
        x0, y0, x1, y1 = int(0.5 * W), 0, W, int(0.5 * H)
        reg = screen[y0:y1, x0:x1]
        t = self.t["closestam"]
        if t.ok:
            best = (0.0, None)
            for sc in (0.6, 0.7, 0.8, 0.9, 1.1, 1.25, 1.5, 1.8):
                tpl = cv2.resize(t.img, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_CUBIC)
                if tpl.shape[0] >= reg.shape[0] or tpl.shape[1] >= reg.shape[1]:
                    continue
                _, v, _, loc = cv2.minMaxLoc(cv2.matchTemplate(reg, tpl, cv2.TM_CCOEFF_NORMED))
                if v > best[0]:
                    best = (float(v), (loc[0] + tpl.shape[1] / 2 + x0, loc[1] + tpl.shape[0] / 2 + y0))
            if best[0] >= 0.70:
                return (best[1][0], best[1][1], best[0])
        hsv = cv2.cvtColor(reg, cv2.COLOR_BGR2HSV)
        m = cv2.inRange(hsv, (0, 150, 110), (10, 255, 255)) | cv2.inRange(hsv, (170, 150, 110), (180, 255, 255))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cand = []
        for c in cs:
            x, y, w, hh = cv2.boundingRect(c)
            area = cv2.contourArea(c)
            if w >= W * 0.02 and hh >= W * 0.02 and 0.7 <= w / hh <= 1.4 and 0.25 <= area / (w * hh) <= 0.85:
                cand.append((area, x + w / 2 + x0, y + hh / 2 + y0))
        if cand:
            a, cx, cy = max(cand)
            return (cx, cy, 0.5)
        return None

    def detect(self, screen):
        """-> "insustam" | "insustam2" | "morestam" | None (ecran de manque de stamina)."""
        for key in ("morestam", "insustam", "insustam2"):
            if self.t[key].ok and self.t[key].find(screen):
                return key
        return None

    def _wait_for(self, key, timeout, tap_popups=True):
        """Attend qu'une image apparaisse (ferme au passage les popups close / continue). -> (x, y, score) ou None."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            s = self.shot()
            h = self.find(key, s)
            if h:
                return h
            if tap_popups:
                for k in ("close", "continue"):
                    p = self.home.t[k].find(s)
                    if p:
                        self.tap(p[0], p[1])
                        time.sleep(0.8)
                        break
            time.sleep(0.4)
        return None

    def _debug(self, name):
        try:
            d = self.base / "debug"
            d.mkdir(exist_ok=True)
            f = d / f"refill_{name}_{time.strftime('%H%M%S')}.png"
            cv2.imwrite(str(f), self.shot())
            self.log(f"(debug) capture : debug/{f.name}")
        except Exception:
            pass

    # ------------------------------------------------------------ 1. fermer l'ecran "plus de stamina"
    def leave(self, case, screen):
        if case == "insustam":
            # le bouton "Exit" est a droite du bouton jaune : on le cherche (meme image que "Exit Stage" des resultats),
            # sinon on tape a droite du bloc jaune
            h = self.exit_t.find(screen)
            if h and h[0] > self.t["insustam"].find(screen)[0]:
                x, y = h[0], h[1]
            else:
                ix, iy, _ = self.t["insustam"].find(screen)
                W, _H = self.t["insustam"].size
                x, y = ix + W * 1.15, iy
            self.log(f"stamina insuffisante : clic sur Exit ({int(x)},{int(y)})")
            self.tap(x, y)
            time.sleep(1.2)
            ix, iy, _ = self.t["insustam"].find(screen)
            W, _H = self.t["insustam"].size
            for k in (1.15, 1.35, 1.6):                 # le bouton jaune est toujours la : Exit a ete rate, on essaie plus a droite
                if not self.t["insustam"].find(self.shot()):
                    break
                x, y = ix + W * k, iy
                self.log(f"le bouton jaune est toujours la : nouveau clic a droite ({int(x)},{int(y)})")
                self.tap(x, y)
                time.sleep(1.2)
        else:
            h = self.find_cross(screen)
            if h:
                self.log(f"stamina insuffisante ({case}) : clic sur la croix rouge ({int(h[0])},{int(h[1])} ; {h[2]:.2f})")
                self.tap(h[0], h[1])
            else:
                self.log(f"stamina insuffisante ({case}) : croix rouge NON reconnue, on remonte au menu")
                self._debug("croix")
                p = self.home.t["close"].find(screen)
                if p:
                    self.tap(p[0], p[1])
        time.sleep(1.2)

    # ------------------------------------------------------------ 1b. menu principal : on appuie sur home.png
    def to_menu(self):
        """Apres la fermeture (Exit / croix) : appui sur le bouton home.png jusqu'au menu principal, puis nav.Home en renfort."""
        for i in range(6):
            s = self.shot()
            if self.home.on_main(s):
                return True
            h = self.home.t["home"].find(s)
            sc = self.home.t["home"].best(s)[0]
            if not h and sc >= 0.65:                            # un peu moins strict qu'en navigation normale
                h = self.home.t["home"].best(s)[1]
                h = (h[0], h[1], sc)
            if h:
                self.log(f"clic sur home.png ({h[2]:.2f})")
                self.tap(h[0], h[1])
                time.sleep(1.5)
                continue
            self.log(f"home.png non visible (meilleur score {sc:.2f}, seuil {nav.HOME_THR:.2f}) : navigation de secours")
            break
        return self.home.go(timeout=40)

    # ------------------------------------------------------------ 2. aller au menu "Gift Pack Exchange"
    def open_exchange(self):
        s = self.shot()
        if self.find("codearea", s) or self.find("take", s):
            return                                              # deja dans la fenetre du code
        if not self.home.on_main(s) and not self.to_menu():
            self._debug("menu")
            raise self.Done("Recharge stamina : impossible de revenir au menu principal (home.png non reconnu sur cet ecran ?). "
                            "Regarde debug/ : la capture montre l'ecran bloque.")
        h = self._wait_for("cadeau", 6)
        if not h:
            self._debug("cadeau")
            raise self.Done("Recharge stamina : icone cadeau (cadeau.png) introuvable sur le menu principal.")
        self.log("clic sur le cadeau")
        self.tap(h[0], h[1])
        time.sleep(1.5)
        prev, still = None, 0
        XS = (0.05, 0.09, 0.13)                                 # le menu cadeau est tout a gauche : on varie la colonne du glissement
        for i in range(18):                                     # on descend dans la liste de gauche jusqu'a "Gift Pack Exchange"
            s = self.shot()
            if self.find("take", s):
                return
            h = self.find("code", s)
            if h:
                self.log(f"Gift Pack Exchange trouve ({h[2]:.2f})")
                self.tap(h[0], h[1])
                time.sleep(1.5)
                return
            sig = cv2.resize(cv2.cvtColor(s, cv2.COLOR_BGR2GRAY), (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)
            if prev is not None:
                still = still + 1 if float(np.abs(sig - prev).mean()) < 0.8 else 0
                if still >= 4:                                  # 4 glissements sans effet, a 3 endroits differents : fond de liste
                    break
            prev = sig
            H, W = s.shape[:2]
            x = int(XS[i % len(XS)] * W)
            self.log(f"defilement du menu cadeau ({i + 1}) a x={x}")
            self.adb("shell", "input", "swipe", str(x), str(int(0.85 * H)), str(x), str(int(0.25 * H)), "500")
            time.sleep(1.0)
        self._debug("code")
        raise self.Done("Recharge stamina : 'Gift Pack Exchange' (code.png) introuvable dans le menu cadeau.")

    def autoclick(self, screen):
        """Autoclick : quelques taps sur le viseur (CLICK_POS) pour fermer un popup de recompense (jamais si Take est visible)."""
        if self.typing:                                         # un tap pendant la saisie annulerait l'entree du code
            return
        H, W = screen.shape[:2]
        for _ in range(CLICK_BURST):
            self.tap(CLICK_POS[0] * W, CLICK_POS[1] * H)
            time.sleep(0.15)

    # ------------------------------------------------------------ 3. taper le code N fois
    def redeem_once(self, k):
        s = self.shot()
        take = self.find("take", s)
        if not take:
            return False
        tx, ty = take[0], take[1]
        self.typing = True                                       # saisie en cours : plus aucun autoclick jusqu'au Take
        self.tap(tx + FIELD_DX, ty)                              # clic sur "Please enter the pick-up code."
        time.sleep(0.7)
        self.adb("shell", "input", "text", self.code)
        time.sleep(0.6)
        self.tap(tx + OUT_DX, ty + OUT_DY)                       # clic en dehors du champ (texte du titre) : ferme le clavier
        time.sleep(0.8)
        s = self.shot()
        take = self.find("take", s)
        if not take:                                             # clavier encore la : Retour le ferme
            self._back()
            time.sleep(0.8)
            take = self.find("take", self.shot())
        if not take:
            self._debug("take")
            return False
        self.tap(take[0], take[1])
        self.log(f"code {self.code} envoye ({k}/{self.count})")
        self.typing = False                                      # code entre : l'autoclick est de nouveau permis
        time.sleep(1.5)
        return True

    def redeem(self):
        ok = 0
        for k in range(1, self.count + 1):
            for attempt in range(3):
                if self.redeem_once(k):
                    ok += 1
                    break
                self.log(f"code {k}/{self.count} : fenetre du code non reconnue (essai {attempt + 1}/3)")
                # un popup de recompense peut recouvrir la fenetre : on le ferme
                s = self.shot()
                for key in ("close", "continue"):
                    p = self.home.t[key].find(s)
                    if p:
                        self.tap(p[0], p[1])
                        time.sleep(1.0)
                        break
                else:
                    self.autoclick(s)                           # popup inconnu : autoclick sur le viseur
                    time.sleep(1.0)
                if self._wait_for("take", 4):
                    continue
            else:
                self._debug("redeem")
                raise self.Done(f"Recharge stamina : la fenetre du code a disparu apres {ok}/{self.count} codes (arret).")
            # popup de recompense apres Take ?
            for _ in range(3):
                s = self.shot()
                if self.find("take", s):
                    break
                p = self.home.t["close"].find(s) or self.home.t["continue"].find(s)
                if p:
                    self.tap(p[0], p[1])
                    time.sleep(1.0)
                else:
                    self.autoclick(s)                           # ni Take ni popup connu : on clique sur le viseur
                    time.sleep(0.8)
        return ok

    # ------------------------------------------------------------ tout enchainer
    def run(self, case=None, screen=None):
        """Recharge complete. case : declencheur (None = on part de n'importe quel ecran). -> nombre de codes utilises."""
        miss = self.missing()
        if miss:
            raise self.Done("Recharge stamina : images manquantes : " + ", ".join(miss))
        if self.done_t and time.time() - self.done_t < MIN_GAP:
            raise self.Done("Plus de stamina juste apres une recharge : le code est peut-etre epuise (15 utilisations). Arret.")
        self.typing = False
        self.log(f"=== RECHARGE DE STAMINA ({self.code} x{self.count}) ===")
        if case:
            self.leave(case, screen if screen is not None else self.shot())
        self.open_exchange()
        n = self.redeem()
        self.times += 1
        self.log(f"recharge terminee : {n}/{self.count} codes. Retour au menu principal.")
        self.home.go()
        self.done_t = time.time()
        return n


def run_test(shot):
    nav.fastmatch.FAST = False                 # --test : scores exacts (comparaison couleur partout)
    r = Refill(shot, lambda x, y: None, lambda *a, **k: "", print, RuntimeError)
    s = shot()
    print(f"Capture : {s.shape[1]}x{s.shape[0]}   code : {r.code} x{r.count}   active : {enabled()}")
    for k in SPEC:
        h = r.find(k, s) if r.t[k].ok else None
        sc = r.t[k].best(s)[0] if r.t[k].ok else 0
        print(f"{k:<11}{sc:>6.2f}  seuil {SPEC[k]['thr']:.2f}  {'TROUVE ' + str((int(h[0]), int(h[1]))) if h else '-' if r.t[k].ok else 'fichier manquant'}")
    print("ecran de manque de stamina :", r.detect(s) or "non")
    c = r.find_cross(s)
    print("croix rouge (haut droite)   :", f"TROUVEE {int(c[0])},{int(c[1])} (score {c[2]:.2f})" if c else "non")


if __name__ == "__main__":
    import auto_nightops as nt
    nt.init_adb()
    if "--test" in sys.argv:
        run_test(nt.screenshot)
    elif "--now" in sys.argv:
        try:
            Refill(nt.screenshot, nt.tap, nt.adb, nt.log, nt.Done).run()
        except nt.Done as e:
            print(e)
    else:
        print(__doc__)
