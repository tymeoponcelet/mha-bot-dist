"""
Automatisation du mode "Supply" (Cafeteria / Bounty / Data Collection / Reinforcement / Recovery)
de MHA The Strongest Hero (emulateur + ADB). Reutilise le moteur de auto_nightops.py.

Parcours :
  menu principal -> ua -> solo -> supply -> (on fait defiler la liste) carte du mode -> niveau (carte lvl*.png)
  -> confirm -> plus -> am -> multirun -> max -> start
  Pendant le multi-run : continuechall ("3s Continue Challenge", a la fin de chaque run), continue (apres chaque combat), controlbattle, auto, + clic en rafale sur le viseur quand
  aucun bouton n'est reconnu. A la fin d'un multi-run : "Challenge Again (50 times)" (challagain) RELANCE un multi-run
  et compte comme un lancement ; une fois le nombre de lancements atteint, le bot s'arrete sans le taper.

Utilisation :
    python auto_supply.py                              # mode / niveau / lancements : config.json
    python auto_supply.py --mode food --level 4        # food | money | gear | card | crystals ; niveau 1-4 (gear : 1-2)
    python auto_supply.py --runs 3                     # nombre de lancements de "start" (1 multi-run = 50 combats)
    python auto_supply.py --test                       # UNE capture : score de chaque image

Dossier templates_supply/ : supply, food, money, gear, card, crystals (cartes de mode) + lvlfood, lvlmoney, lvlgear,
lvlcard, lvlcrystals (carte des niveaux), confirm (bouton "Confirm" du panneau qui s'ouvre apres le choix du niveau). Les images communes (ua, solo, plus, am, multirun, max, start, continue,
challagain, controlbattle, auto) sont reprises de templates_nightops/ si elles ne sont pas dans templates_supply/.
"""
import json

import sys
import time
from pathlib import Path

import cv2
import numpy as np

import auto_nightops as nt
import fastmatch
import heroes
import nav
import watchdog
from auto_nightops import Done, Tpl, log

class PairTpl(Tpl):
    """"Challenge Again (50 times)" : on cherche le mot \"Again\" (ancre) PUIS la ligne \"Challenge\" juste au-dessus, a la bonne distance.
    Le nombre n'est jamais compare. Les deux zones doivent concorder : \"Continue Challenge\" (qui contient aussi \"Challenge\")
    ne passe plus (score 0,70 -> 0,44), ce qui permet un seuil plus souple (0,80 au lieu de 0,88) sans faux positif."""

    def __init__(self, path, check_crop, **kw):
        super().__init__(path, **kw)
        full = cv2.imread(str(path), cv2.IMREAD_COLOR)
        H, W = full.shape[:2]
        x0, y0, x1, y1 = int(check_crop[0] * W), int(check_crop[1] * H), int(check_crop[2] * W), int(check_crop[3] * H)
        self.chk, self.chk_off = full[y0:y1, x0:x1], (x0, y0)

    def best(self, screen):
        sa, loc = super().best(screen)
        if sa < 0.5:
            return sa, loc
        m, (h, w) = 8, self.chk.shape[:2]
        ex, ey = loc[0] + self.chk_off[0] - self.off[0], loc[1] + self.chk_off[1] - self.off[1]
        rx0, ry0 = max(0, ex - m), max(0, ey - m)
        roi = screen[ry0:min(screen.shape[0], ey + h + m), rx0:min(screen.shape[1], ex + w + m)]
        if roi.shape[0] < h or roi.shape[1] < w:
            return 0.0, loc
        sb = float(cv2.matchTemplate(roi, self.chk, cv2.TM_CCOEFF_NORMED).max())
        return (0.0 if np.isnan(sb) else min(sa, sb)), loc



BASE = Path(__file__).resolve().parent
TPL_DIRS = [BASE / "templates_supply", BASE / "templates_nightops"]

MODES = {"food": "Cafeteria", "money": "Bounty", "gear": "Data Collection", "card": "Reinforcement", "crystals": "Recovery"}
MAX_LEVEL = {"food": 4, "money": 4, "gear": 2, "card": 4, "crystals": 4}

# Point de clic de chaque niveau, en pixels SUR l'image lvl<mode>.png (le bot retrouve l'image a l'ecran puis tape a cet endroit)
LEVEL_PTS = {
    "food":     [(95, 480), (340, 335), (688, 385), (880, 215)],
    "money":    [(78, 490), (325, 335), (672, 390), (872, 225)],
    "crystals": [(110, 435), (370, 275), (718, 350), (910, 185)],
    "card":     [(100, 470), (345, 305), (693, 370), (888, 190)],
    "gear":     [(80, 415), (320, 230)],
}

# ======================= REGLAGES =======================
MODE, LEVEL, MAX_RUNS = "food", 4, 1
LEVEL_THR = 0.70               # la carte des niveaux change un peu (badge ONGOING) -> seuil plus souple
CARD_THR = 0.80
SWIPE_Y = 0.50                 # hauteur de la ligne de cartes
SWIPE_DURATION = 700           # ms
SWIPE_MAX = 6                  # glissements max dans un sens avant de repartir dans l'autre
SWIPE_END_DIFF = 2.0           # difference d'image < a ca apres un glissement = bout de la liste
SWIPE_STILL = 2
MAX_ENDS = 4                   # bouts atteints avant d'abandonner (jamais de touche Retour)
NAV_TAP_LIMIT = 40             # securite : taps de navigation sans jamais atteindre "start"

# --- VITESSE de reaction en fin de combat ---
# Avant : a chaque tour le bot comparait ~15 images (~100 ms chacune) + 0.6 s de pause apres chaque tap + une
# rafale de 5 taps sur le viseur (~1 s) -> "3s Continue Challenge" etait tape avec 1 a 3 s de retard.
# Maintenant : juste apres la capture, on teste UNIQUEMENT ces boutons et on tape tout de suite.
FAST_KEYS = ("continuechall", "continue")    # du plus prioritaire au moins prioritaire
FAST_SETTLE = 0.12             # pause apres un tap rapide (s) ; l'ancienne pause etait nt.SETTLE = 0.6
FAST_CD = {"continuechall": 0.6, "continue": 0.8}   # delai mini entre 2 taps du meme bouton (s)
BURST = 2                      # taps par rafale sur le viseur (avant : 5) -> la boucle revient vite verifier l'ecran
DEBUG_EVERY = 10.0             # en combat : toutes les X s, si Challenge Again / Continue Challenge est presque reconnu, on le note + capture dans debug/
DEBUG_MAX = 30                 # nombre max de captures gardees dans debug/

_CFG = BASE / "config.json"
if _CFG.exists():
    try:
        _c = json.loads(_CFG.read_text(encoding="utf-8"))
        MODE = _c.get("supply_mode", MODE)
        LEVEL = int(_c.get("supply_level", LEVEL) or LEVEL)
        MAX_RUNS = int(_c.get("supply_runs", MAX_RUNS) or MAX_RUNS)
    except Exception:
        pass


def _arg(flag):
    if flag in sys.argv:
        try:
            return sys.argv[sys.argv.index(flag) + 1]
        except IndexError:
            return None
    return None


if _arg("--mode"):
    MODE = _arg("--mode").strip().lower()
if _arg("--level"):
    try:
        LEVEL = int(_arg("--level"))
    except ValueError:
        pass
if _arg("--runs"):
    try:
        MAX_RUNS = max(1, int(_arg("--runs")))
    except ValueError:
        pass


def tpl_path(name):
    for d in TPL_DIRS:
        if (d / f"{name}.png").exists():
            return d / f"{name}.png"
    return None


def build_spec():
    """name -> (fichier, options Tpl). Ordre = du plus avance au plus ancien (le plus avance gagne si plusieurs sont visibles)."""
    shared = lambda k: dict(nt.SPEC[k])
    img = cv2.imread(str(tpl_path(f"lvl{MODE}") or ""))
    px, py = LEVEL_PTS[MODE][LEVEL - 1]
    tap = (px / img.shape[1], py / img.shape[0]) if img is not None else (0.5, 0.5)
    return {
        # "Challenge Again (50 times)" : on ne compare que le mot "Again" (le nombre change, et la ligne "Challenge"
        # est aussi dans "Continue Challenge" -> risque de confusion)
        "challagain":    ("challagain", dict(cd=3.0, crop=(0.12, 0.50, 0.40, 0.88), thr=0.80, pair=(0.22, 0.14, 0.74, 0.52))),
        "continue":      ("continue", shared("continue")),
        # "3s Continue Challenge" : le compte a rebours orange a gauche change -> on ne compare que le texte
        "continuechall": ("continuechall", dict(cd=1.5, crop=(0.30, 0.15, 0.85, 0.90), thr=0.80)),
        "controlbattle": ("controlbattle", shared("controlbattle")),
        "auto":          ("auto", shared("auto")),
        "start":         ("start", shared("start")),
        "max":           ("max", shared("max")),
        "multirun":      ("multirun", shared("multirun")),
        "am":            ("am", shared("am")),
        "plus":          ("plus", shared("plus")),
        "confirm":       ("confirm", dict(cd=3.0)),          # panneau de droite apres avoir tape un niveau
        "level":         (f"lvl{MODE}", dict(cd=3.0, thr=LEVEL_THR, tap=tap)),
        "modecard":      (MODE, dict(cd=3.0, thr=CARD_THR, crop=(0, 0, 1, 0.78))),   # on ignore le bas ("Attempts: 20/20" change)
        "supply":        ("supply", dict(cd=3.0)),
        "solo":          ("solo", shared("solo")),
        "ua":            ("ua", shared("ua")),
    }


BATTLE_ONLY = set(nt.BATTLE_ONLY) | {"continuechall"}
# Ecrans "derriere" le panneau : la carte du niveau, Confirm, etc. restent VISIBLES a l'ecran pendant qu'on choisit le perso.
# Des que le panneau (Multi-run / +) ou la liste des persos est ouvert, on ne doit plus jamais les retaper.
BEHIND_KEYS = {"confirm", "level", "modecard", "supply", "solo", "ua"}
T, OTHERS, SPEC = {}, {}, {}


PICK = None                                   # choix du perso (heroes.Picker), cree par load_templates()
GATE = None                                   # barriere "bon perso place ?" avant Multi-run (heroes.SlotGate)


def load_templates(strict=True):
    global PICK, GATE
    SPEC.update(build_spec())
    missing = []
    for key, (fname, opts) in SPEC.items():
        if key == "am":
            continue                          # le perso n'est plus une image fixe : voir heroes.py / templates_chars/
        p = tpl_path(fname)
        if p is None:
            missing.append(f"{fname}.png")
            continue
        opts = dict(opts)
        pair = opts.pop("pair", None)
        T[key] = PairTpl(p, pair, **opts) if pair else Tpl(p, **opts)
    try:
        PICK = heroes.Picker(heroes.chosen_name(), nt.adb, log, fail=Done)
        T["am"] = heroes.RowTpl(PICK, cd=SPEC["am"][1]["cd"] * nt.CD_SCALE)
        VERIFY = "--no-verify" not in sys.argv and heroes.chosen_explicit()
        if not heroes.chosen_explicit():      # aucun perso choisi : on garde celui qui est deja place, on ne le remplace pas
            log("aucun perso choisi : je garde celui qui est deja place "
                "(choisis-en un dans l'assistant, sur Discord, ou avec --hero, pour que le bot le change)")
        GATE = heroes.SlotGate(heroes.SlotReader(), PICK.name, log, Done, enabled=VERIFY)
    except FileNotFoundError as e:
        missing.append(str(e))
    # les autres cartes de mode : servent seulement a savoir qu'on est sur l'ecran de choix (-> faire defiler)
    for m in MODES:
        p = tpl_path(m)
        if p is not None:
            OTHERS[m] = Tpl(p, crop=(0, 0, 1, 0.78), thr=CARD_THR)
    if missing and strict:
        raise SystemExit("Images manquantes (templates_supply/ ou templates_nightops/) : " + ", ".join(missing))
    return missing


def grab():
    """Capture d'ecran (capture brute rapide geree par auto_nightops.screenshot)."""
    return nt.screenshot()


def card_sig(screen):
    h = screen.shape[0]
    band = cv2.cvtColor(screen[int(0.15 * h):int(0.85 * h)], cv2.COLOR_BGR2GRAY)
    return cv2.resize(band, (96, 48), interpolation=cv2.INTER_AREA).astype(np.float32)


class Sw:
    dir, n, ends, still, miss, sig = -1, 0, 0, 0, 0, None   # dir -1 : le doigt va vers la gauche (la liste avance)

    @classmethod
    def reset(cls):
        cls.dir, cls.n, cls.ends, cls.still, cls.miss, cls.sig = -1, 0, 0, 0, 0, None


def swipe_list(screen):
    h, w = screen.shape[:2]
    if Sw.sig is not None:                       # on vient de glisser : la liste a-t-elle bouge ?
        diff = float(np.abs(card_sig(screen) - Sw.sig).mean())
        Sw.sig = None
        Sw.still = Sw.still + 1 if diff < SWIPE_END_DIFF else 0
        if Sw.still >= SWIPE_STILL or Sw.n >= SWIPE_MAX:
            Sw.ends += 1
            Sw.dir, Sw.n, Sw.still = -Sw.dir, 0, 0
            log("bout de la liste atteint, on repart dans l'autre sens")
    if Sw.ends >= MAX_ENDS:
        raise Done(f"Carte '{MODE}' introuvable apres un aller-retour complet. Verifie templates_supply/{MODE}.png "
                   f"ou SWIPE_Y (le bot ne touche jamais a Retour).")
    x1, x2 = (0.75 * w, 0.25 * w) if Sw.dir < 0 else (0.25 * w, 0.75 * w)
    log(f"carte '{MODE}' introuvable : glissement {'vers la gauche' if Sw.dir < 0 else 'vers la droite'}")
    nt.adb("shell", "input", "swipe", str(int(x1)), str(int(SWIPE_Y * h)), str(int(x2)), str(int(SWIPE_Y * h)), str(SWIPE_DURATION))
    Sw.sig = card_sig(screen)
    Sw.n += 1
    time.sleep(nt.SETTLE + 0.4)



DBG_N = 0


def debug_near_miss(screen, now):
    """Rien n'a ete tape depuis DEBUG_EVERY s : si Challenge Again / Continue Challenge est 'presque' reconnu, on garde les scores et la capture."""
    global DBG_N
    sc = {k: T[k].best(screen)[0] for k in ("challagain", "continuechall") if k in T}
    if any(0.55 <= v < T[k].thr for k, v in sc.items()) and DBG_N < DEBUG_MAX:
        d = BASE / "debug"
        d.mkdir(exist_ok=True)
        f = d / f"{time.strftime('%H%M%S')}_{'_'.join(f'{k}{v:.2f}' for k, v in sc.items())}.png"
        cv2.imwrite(str(f), screen)
        DBG_N += 1
        log(f"(debug) presque reconnu : " + ", ".join(f"{k} {v:.2f} (seuil {T[k].thr:.2f})" for k, v in sc.items()) + f" -> capture {f.name}")


def run_test():
    nt.fastmatch.FAST = False                 # --test : scores exacts (comparaison couleur partout)
    load_templates(strict=False)
    screen = nt.screenshot()
    print(f"Capture : {screen.shape[1]}x{screen.shape[0]} | mode {MODE} ({MODES.get(MODE)}) niveau {LEVEL}")
    print(f"{'image':<15}{'score':>7}  {'seuil':>6}  position")
    for key, t in T.items():
        score, loc = t.best(screen)
        print(f"{SPEC[key][0]:<15}{score:>7.2f}  {t.thr:>6.2f}  {'TROUVE ' + str(loc) if score >= t.thr else '-'}")
    for m, t in OTHERS.items():
        if m != MODE:
            score, loc = t.best(screen)
            print(f"{m + ' (carte)':<15}{score:>7.2f}  {t.thr:>6.2f}  {'TROUVE ' + str(loc) if score >= t.thr else '-'}")
    for key, (fname, _o) in SPEC.items():
        if key not in T:
            print(f"{fname:<15}  fichier manquant : {fname}.png")


def main():
    global MODE, LEVEL
    if "--list" in sys.argv:
        for m, n in MODES.items():
            print(f"{m:<9} {n:<16} niveaux 1-{MAX_LEVEL[m]}")
        return
    if MODE not in MODES:
        raise SystemExit(f"Mode inconnu '{MODE}'. Choix : {', '.join(MODES)}")
    LEVEL = min(max(1, LEVEL), MAX_LEVEL[MODE])
    if "--test" in sys.argv:
        nt.init_adb()
        return run_test()
    load_templates()
    nt.init_adb()
    if "--no-home" not in sys.argv:
        nav.Home(nt.screenshot, nt.tap, log, back=lambda: nt.adb("shell", "input", "keyevent", "4")).go()   # retour au menu principal, d'ou qu'on parte
    last_tap = {n: 0.0 for n in SPEC}
    runs, auto_taps, nav_taps = 0, 0, 0
    max_done = False
    in_battle, t_start = False, 0.0
    awaiting_pick, pick_t = False, 0.0        # "plus" tape : la liste des persos va s'ouvrir
    last_action = time.time()
    RF = nt.make_refill()                     # recharge de stamina par code (option) ; None si desactivee
    REPRISE = nav.Reprise(nav.Home(nt.screenshot, nt.tap, log, back=lambda: nt.adb("shell", "input", "keyevent", "4")), log)   # ecran inconnu 60 s -> menu + reprise
    seen_t = time.time()                # derniere fois qu'une image a ete reconnue
    WD = watchdog.make(nt.screenshot, nt.tap, nt.adb, log, Done)   # chien de garde (option) ; None si desactive
    print(f"Perso : {heroes.pretty(PICK.name)}")
    print(f"Supply :{MODES[MODE]} (niveau {LEVEL}) | {MAX_RUNS} lancement(s) de multi-run. Ctrl+C pour arreter. "
          f"(commence depuis le menu principal)")

    last_dbg, rr = 0.0, 0
    NAV_KEYS = [k for k in SPEC if k in T and k not in BATTLE_ONLY]
    panel_prev = False
    while True:
        screen = grab()
        now = time.time()

        # --- plus de stamina (bouton jaune a la place de Continue Challenge / message / fenetre Stamina Supply) ---
        case = RF.detect(screen) if RF else None
        if case:
            if case != "insustam" and in_battle and now - t_start < 15:
                runs = max(0, runs - 1)             # le Start n'a rien lance (pas de stamina) : ce lancement sera refait
            RF.run(case, screen)
            if case == "insustam" and runs >= MAX_RUNS:     # dernier multi-run termine, Challenge Again n'etait pas a relancer
                raise Done(f"{runs}/{MAX_RUNS} lancement(s) termine(s). Stamina rechargee. Fini !")
            in_battle, awaiting_pick, max_done, auto_taps, nav_taps, panel_prev = False, False, False, 0, 0, False
            PICK.reset()
            GATE.reset()
            last_tap = {n: 0.0 for n in SPEC}
            last_action = time.time()
            continue

        if WD and WD.poll(screen, in_battle):          # maintenance / accueil / jeu bloque ou ferme : le jeu a ete relance, on repart du menu
            if in_battle:
                runs = max(0, runs - 1)             # le multi-run en cours est perdu : il sera refait
            in_battle, awaiting_pick, max_done, auto_taps, nav_taps, panel_prev = False, False, False, 0, 0, False
            PICK.reset()
            GATE.reset()
            last_tap = {n: 0.0 for n in SPEC}
            last_action = time.time()
            continue

        # --- CHEMIN RAPIDE : fin de combat / fin de run -> on tape tout de suite, sans tester les ~15 autres images ---
        fast_acted = False
        fast_seen = {}                           # key -> resultat deja calcule ce tour (None = pas vu)
        for key in FAST_KEYS:
            if key not in T or now - last_tap[key] < FAST_CD[key]:
                continue
            h = fast_seen[key] = T[key].find(screen)
            if h:
                nt.tap(h[0], h[1])
                last_tap[key] = last_action = time.time()
                if WD and key == "continuechall":
                    WD.kick()                    # fin d'un run = vrai progres (le simple "Continue" ne compte pas)
                log(f"{key} ({h[2]:.2f}) | run {runs}/{MAX_RUNS}")
                time.sleep(FAST_SETTLE)
                fast_acted = True
                break
        if fast_acted:
            continue

        # En plein combat les boutons de combat (Challenge Again, Continue, Controlled Battle, AUTO) sont testes a CHAQUE tour.
        # Les images de navigation (qui ne servent qu'a reperer la fin du multi-run si Challenge Again n'apparait pas) sont testees
        # UNE SEULE par tour, a tour de role (apres le delai de securite) : un test complet coutait ~1,5 s et retardait Challenge Again / Continue Challenge.
        full = not in_battle
        rr_key = None
        if in_battle and now - t_start > nt.BATTLE_END_DELAY and NAV_KEYS:
            rr_key = NAV_KEYS[rr % len(NAV_KEYS)]
            rr += 1
        need = []
        for key, t in T.items():
            if not full and key not in BATTLE_ONLY and key != rr_key:
                continue
            if panel_prev and key in BEHIND_KEYS:
                continue                         # panneau de l'equipe ouvert : inutile de chercher l'ecran qui est derriere (gain ~1 s / tour)
            need.append(key)
        found = fastmatch.find_many([(k, T[k]) for k in need if k not in fast_seen], screen)   # en parallele, meme resultat
        hits = {}
        for key in need:
            h = fast_seen[key] if key in fast_seen else found[key]
            if h:
                hits[key] = h

        if in_battle and now - t_start > nt.BATTLE_END_DELAY and any(n not in BATTLE_ONLY for n in hits):
            in_battle = False
            log("multi-run termine")
            if runs >= MAX_RUNS:
                raise Done(f"{runs}/{MAX_RUNS} lancement(s) termine(s). Fini !")

        if in_battle and now - t_start > nt.BATTLE_END_DELAY and now - last_dbg >= DEBUG_EVERY and now - last_action >= DEBUG_EVERY:
            last_dbg = now
            debug_near_miss(screen, now)

        acted = False
        # panneau de l'equipe ouvert (Multi-run, "+", liste des persos) -> on ne retape JAMAIS la carte du niveau & co,
        # meme quand la barriere du perso bloque Multi-run (attente, liste en cours d'ouverture) ou pendant un cooldown
        panel_open = (not in_battle) and ("multirun" in hits or "plus" in hits or "am" in hits
                                          or awaiting_pick or bool(GATE.swap_t))
        panel_prev = panel_open
        for key in SPEC:                         # du plus avance au plus ancien
            if key not in hits or now - last_tap[key] < T[key].cd:
                continue
            if panel_open and key in BEHIND_KEYS:
                continue
            if key == "auto" and (not in_battle or auto_taps >= nt.AUTO_MAX_TAPS):
                continue
            if key == "start" and runs >= MAX_RUNS:
                continue
            if key == "challagain":
                if "continuechall" in hits:
                    continue                     # c'est le bouton de fin de run, pas celui de fin de multi-run
                if in_battle and now - t_start <= nt.BATTLE_END_DELAY:
                    continue                     # juste apres un lancement : l'ancien ecran peut rester visible un instant
                if runs >= MAX_RUNS:             # tous les lancements voulus sont faits : on ne relance pas
                    log("multi-run termine")
                    raise Done(f"{runs}/{MAX_RUNS} lancement(s) termine(s). Fini !")
            if key == "start" and "max" in hits and not max_done:
                continue                         # fenetre Multi-run : Max d'abord, Start ensuite
            if key == "max" and max_done:
                continue
            if in_battle and key in ("start", "max", "multirun", "am", "plus", "confirm", "level", "modecard", "supply", "solo", "ua") \
                    and now - t_start <= nt.BATTLE_END_DELAY:
                continue                         # juste apres Start : l'ancien ecran peut rester visible un instant
            if key == "multirun" and not in_battle:
                if "plus" in hits or (GATE.swap_t and awaiting_pick):
                    continue                     # emplacement vide, ou liste des persos en cours d'ouverture : on ne lance pas
                state, pt = GATE.check(screen, (hits["multirun"][0], hits["multirun"][1]))
                if state == "wait":
                    continue
                if state == "swap":
                    nt.tap(pt[0], pt[1])
                    last_tap[key] = last_action = time.time()
                    awaiting_pick, pick_t = True, time.time()
                    PICK.reset()
                    log("emplacement du perso -> ouverture de la liste")
                    acted = True
                    time.sleep(nt.SETTLE + 0.5)
                    break
            x, y, score = hits[key]
            nt.tap(x, y)
            last_tap[key] = last_action = time.time()
            extra = ""
            if key == "max":
                max_done = True
            elif key == "start":
                runs += 1
                in_battle, t_start, auto_taps, max_done, nav_taps = True, time.time(), 0, False, 0
            elif key == "auto":
                auto_taps += 1
            elif key == "challagain":               # relance un multi-run = un lancement de plus
                runs += 1
                in_battle, t_start, auto_taps, nav_taps = True, time.time(), 0, 0
                extra = " relance"
            elif key == "level":
                extra = f" niv.{LEVEL}"
            elif key == "plus":
                awaiting_pick, pick_t = True, time.time()
                PICK.reset()
                GATE.note_plus((x, y), (hits["multirun"][0], hits["multirun"][1]) if "multirun" in hits else None)
            elif key == "confirm":
                GATE.reset()
            if key == "am":
                GATE.done()
            if key in ("am", "start", "challagain"):
                awaiting_pick = False
                PICK.reset()
            if key in ("start", "challagain"):
                GATE.reset()
            if key not in BATTLE_ONLY and key != "start":
                nav_taps += 1
                if nav_taps > NAV_TAP_LIMIT:
                    raise Done(f"Securite : {NAV_TAP_LIMIT} taps de navigation sans pouvoir lancer le combat. "
                               f"Regarde l'ecran (stamina vide ? popup inattendu ?).")
            if key == "modecard":
                Sw.reset()
            if WD and key in ("challagain", "start"):
                WD.kick()
            log(f"{key} ({score:.2f}{extra}) | run {runs}/{MAX_RUNS}")
            acted = True
            time.sleep(nt.SETTLE)
            break

        if acted:
            continue
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
        # sur l'ecran de choix des modes mais la carte voulue n'est pas visible -> on fait defiler la liste
        if not in_battle and "modecard" not in hits and any(t.find(screen) for t in OTHERS.values()):
            Sw.miss += 1
            if Sw.miss >= 2:                     # 1re fois : on laisse l'ecran finir de s'afficher
                swipe_list(screen)
                last_action = time.time()
                continue
        else:
            Sw.miss = 0
        if nt.CLICK_ENABLED and in_battle and not any(n not in BATTLE_ONLY for n in hits):
            h, w = screen.shape[:2]
            nt.tap_burst(nt.CLICK_POS[0] * w, nt.CLICK_POS[1] * h, BURST)
            continue
        if hits:
            seen_t = now
        if not in_battle and REPRISE.due(now - max(last_action, seen_t)) and REPRISE.run():
            in_battle, awaiting_pick, max_done, auto_taps, nav_taps, panel_prev = False, False, False, 0, 0, False
            PICK.reset()
            GATE.reset()
            last_tap = {n: 0.0 for n in SPEC}
            last_action = seen_t = time.time()
            continue
        if not in_battle and now - last_action > nt.IDLE_LIMIT and not WD:
            raise Done(f"Plus rien reconnu depuis {nt.IDLE_LIMIT}s : arret (ecran inattendu, plus de stamina ?).")
        time.sleep(nt.LOOP_DELAY)


if __name__ == "__main__":
    import journal
    journal.start("supply")                 # journal de session dans logs/ (garde les 10 derniers)
    journal.run(main, Done)               # meme affichage qu'avant + incident (capture, boite noire) si arret anormal
