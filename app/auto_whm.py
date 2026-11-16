"""
EVENEMENT "Assault on Humarise Branch!" (WHM) - farm automatique.

Chemin suivi (tout est pilote par ce qui est reconnu a l'ecran, jamais par des positions fixes) :
    menu principal -> icone de l'evenement (whmevent) -> la banniere de l'evenement (entrance)
    -> la carte du niveau choisi (whmlvl1 / whmlvl2 / whmlvl3) -> le bouton "Confirm"
    -> l'emplacement du perso ("+" ou perso deja place : le bot place celui que tu as choisi)
    -> "Consecutive Challenges" : il COCHE la case (plusieursfois) = le maximum de runs d'un coup
    -> le bouton de lancement (start) -> combats : il active AUTO, puis tape vite "Continue Challenge"
       a la fin de chaque run ; quand il n'y a plus de runs a enchainer, il quitte par "Exit Stage".

Utilisation (depuis le dossier du bot) :
    python auto_whm.py                     # niveau de config.json ("whm_level", defaut 3), 1 multi-run
    python auto_whm.py --level 2 --runs 3  # Entrance 2, 3 multi-runs a la suite
    python auto_whm.py --event hallway --level 3   # le 2e evenement, "Hallway" (meme endroit, meme fonctionnement)
    python auto_whm.py --hero deku         # le perso qui combat (sinon : config.json "pick_hero")
    python auto_whm.py --test              # UNE capture : score de chaque image (pour regler les seuils)
    python auto_whm.py --no-home           # ne pas revenir au menu principal avant de commencer
    python auto_whm.py --refill            # recharger la stamina avec le code quand il n'y en a plus
    python auto_whm.py --watchdog          # relancer le jeu en cas de maintenance / blocage
Un multi-run = une case cochee + un "Challenge", puis le bot reappuie sur "Challenge" (le MEME bouton) a la fin de chaque
combat, tant que le jeu le propose : ca enchaine tous les runs possibles. Il ne quitte (Exit Stage) que quand le nombre de
multi-runs demande est atteint ET que le jeu ne propose plus de relancer.

IMAGES (dossier templates_whm/, a la resolution de ton emulateur) :
    whmevent.png       l'icone de l'evenement sur le menu principal
    entrance.png       la banniere de l'evenement (on clique dessus pour entrer)
    whmlvl1/2/3.png    les cartes Entrance 1, 2 et 3 (le bot compare l'ILLUSTRATION : c'est ce qui les distingue)
    entrance2.png / hallway2.png   la VIGNETTE de l'evenement quand c'est l'autre qui est selectionne : le bot clique dessus
                       pour le selectionner, puis sur sa banniere (entrance.png / hallway.png) pour entrer
    hallway.png        la banniere du 2e evenement, "Hallway" ; hallwaylvl1/2/3.png : ses cartes Hallway 1, 2 et 3
    library.png        la banniere du 3e evenement, "Library" ; librarylvl1/2/3.png : ses cartes ; library2.png : sa vignette
    whmchallenge.png   le bouton jaune "Challenge" qui LANCE le combat (different du "Start" des autres modes)
    whmchallengeagain.png  le bouton "Challenge Again (3s)" de FIN de combat (le compte a rebours n'est pas compare)
    whmexit.png        (facultatif) le bouton "Exit Stage" de l'evenement, SEULEMENT s'il differe de templates/exitstage.png
    plusieursfois.png  le bloc "Consecutive Challenges" + sa case a cocher (le nombre de runs n'est pas compare)
Le reste est repris des autres modes : plus, auto, continue, controlbattle (templates_nightops/),
continuechall + confirm (templates_supply/), exit (templates_hero/), ua (templates_nightops/).
"""
import os  # noqa: E402
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # memoire : sinon numpy reserve ~370 Mo par processus (16/11/2026 : « Insufficient memory » chez un ami)
import json
import sys
import time
from pathlib import Path

import cv2

import auto_nightops as nt
import fastmatch
import heroes
import nav
import watchdog
from auto_nightops import Done, Tpl, log, tap_burst

BASE = Path(__file__).resolve().parent
TPL_DIRS = [BASE / "templates_whm", BASE / "templates_supply", BASE / "templates_nightops",
            BASE / "templates_hero", BASE / "templates"]

EVENT = "entrance"             # lequel des evenements : "entrance", "hallway" ou "library" (meme fonctionnement)
# pour chaque evenement : (nom affiche, image de la banniere, debut du nom des cartes de niveau)
EVENTS = {"entrance": ("Entrance", "entrance.png", "whmlvl"), "hallway": ("Hallway", "hallway.png", "hallwaylvl"),
          "library": ("Library", "library.png", "librarylvl")}
LEVEL = 3                      # niveau 1, 2 ou 3
MAX_RUNS = 1                   # nombre de multi-runs (case cochee + Start)
CHECK_THR = 0.80
CARD_CROP = (0.18, 0.16, 0.88, 0.62)      # l'illustration SEULE (sans le cadre : il est jaune si la carte est selectionnee,
                                          # blanc sinon, et la carte doit etre reconnue dans les deux etats)


def card_selected(screen, hit, size):
    """La carte du niveau est-elle SELECTIONNEE ? Selectionnee = bandeau du nom JAUNE ; non selectionnee = bandeau blanc.
    hit = centre de la carte a l'ecran, size = (largeur, hauteur) de son image."""
    w, h = size
    X, Y = hit[0] - w / 2, hit[1] - h / 2
    H, W = screen.shape[:2]
    za = screen[int(max(0, Y + 0.66 * h)):int(min(H, Y + 0.92 * h)), int(max(0, X + 0.40 * w)):int(min(W, X + 0.92 * w))]
    if za.size == 0:
        return False
    hsv = cv2.cvtColor(za, cv2.COLOR_BGR2HSV)
    yellow = ((hsv[..., 0] > 15) & (hsv[..., 0] < 38) & (hsv[..., 1] > 110) & (hsv[..., 2] > 140)).mean()
    return float(yellow) > 0.15
CHECKBOX = (0.913, 0.730)                 # la petite case a cocher, en fraction de plusieursfois.png
FAST_KEYS = ("challagain", "continuechall", "continue")   # testees a chaque tour, avant tout le reste (reaction immediate)
FAST_CD = {"challagain": 0.6, "continuechall": 0.6, "continue": 0.8}
FAST_SETTLE = 0.12
BURST = 2                      # taps par rafale sur le viseur pendant le combat
NAV_TAP_LIMIT = 40             # securite : taps de navigation sans jamais lancer un combat

_c = {}
try:
    _c = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    LEVEL = int(_c.get("whm_level", LEVEL) or LEVEL)
    EVENT = str(_c.get("whm_event", EVENT) or EVENT).lower()
    MAX_RUNS = int(_c.get("whm_runs", MAX_RUNS) or MAX_RUNS)
except Exception:
    pass


from commun import arg as _arg                   # valeur d'une option de la ligne de commande (commun.py)


LEVEL = min(3, max(1, _arg("--level") or LEVEL))
EVENT = str(_arg("--event", str) or EVENT).lower()
if EVENT not in EVENTS:
    raise SystemExit(f"Evenement inconnu : '{EVENT}'. Choix : {', '.join(EVENTS)}")
EV_NAME, EV_BANNER, EV_CARD = EVENTS[EVENT]
MAX_RUNS = max(1, _arg("--runs") or MAX_RUNS)

# de l'ecran le plus avance au plus ancien : le bot tape toujours le bouton le plus avance qu'il voit
SPEC = {
    # "Challenge Again (3s)" de l'evenement : on ne compare PAS le compte a rebours (3s, 2s, 1s), seulement le texte
    "challagain":    dict(cd=1.5, crop=(0.30, 0.08, 0.98, 0.92), thr=0.80),
    "continuechall": dict(cd=1.5, crop=(0.30, 0.15, 0.85, 0.90), thr=0.80),
    "continue":      dict(cd=2.0, thr=0.80),
    "controlbattle": dict(cd=3.0),
    "auto":          dict(cd=8.0, crop=(0.10, 0.28, 0.88, 0.70), mask_white=True, thr=nt.AUTO_THRESHOLD),
    "exit":          dict(cd=3.0, thr=0.80),           # "Exit Stage" de l'ecran de resultats : pour quitter a la fin
    "start":         dict(cd=10.0),
    "plusieursfois": dict(cd=3.0, crop=(0.02, 0.05, 0.42, 0.95), thr=CHECK_THR, tap=CHECKBOX),
    "am":            dict(cd=2.5, crop=(0, 0, 0.28, 1), tap=(0.782, 0.581)),
    "plus":          dict(cd=2.5),
    "confirm":       dict(cd=3.0, thr=0.80),           # apres le choix du niveau : "Confirm" (repris de templates_supply/)
    "card":          dict(cd=2.0, crop=CARD_CROP, thr=0.85),     # whmlvl<LEVEL>.png
    "entrance":      dict(cd=3.0, crop=(0.0, 0.16, 1.0, 0.80), thr=0.80),   # sans le temps restant ni le %
    # vignette de l'evenement quand c'est L'AUTRE qui est selectionne (entrance2.png / hallway2.png) : un clic dessus le
    # selectionne, et sa banniere (entrance.png / hallway.png) apparait. On ne compare ni le bord ni les lumieres du bas.
    "entrance2":     dict(cd=2.5, crop=(0.10, 0.10, 0.95, 0.80), thr=0.80),
    "whmevent":      dict(cd=3.0, thr=0.80),
    "ua":            dict(cd=2.5, crop=(0, 0.30, 1, 1)),
}
BATTLE_ONLY = {"challagain", "continuechall", "continue", "controlbattle", "auto"}
BEHIND_KEYS = {"confirm", "card", "entrance", "entrance2", "whmevent", "ua"}   # ecrans DEJA PASSES une fois le niveau valide
PANEL_KEYS = {"plusieursfois", "start", "plus", "am"}             # on est sur le panneau de lancement (apres Confirm)
BACK_RESET = 12                # s : si plus rien d'autre que ces ecrans n'est reconnu, c'est qu'on est vraiment revenu en arriere

T = {}
OTHER_VIG = {}                 # vignettes des autres evenements (pour ne pas cliquer sur la mauvaise)
PICK = None
GATE = None


BOX_REF = {"img": None}        # la case a cocher telle qu'elle est sur plusieursfois.png (= case VIDE)
BOX_DIFF = 18                  # difference moyenne au-dela de laquelle on considere la case COCHEE


def box_state(screen, hit):
    """La case "Consecutive Challenges" est-elle deja cochee ? -> "vide" | "cochee" | "?"
    On compare la case a l'ecran avec celle de plusieursfois.png (qui est vide sur la capture de reference)."""
    ref = BOX_REF["img"]
    if ref is None or "plusieursfois" not in T:
        return "?"
    h, w = ref.shape[:2]
    x, y = int(hit[0] - w / 2), int(hit[1] - h / 2)      # hit = centre de la case (tap=CHECKBOX)
    if x < 0 or y < 0 or y + h > screen.shape[0] or x + w > screen.shape[1]:
        return "?"
    d = float(cv2.absdiff(screen[y:y + h, x:x + w], ref).mean())
    return "vide" if d <= BOX_DIFF else "cochee"


def slot_dir():
    """Images de l'emplacement du perso : templates_whm/slot/ si tu en as mis (le panneau de l'evenement peut etre
    different de celui du Supply), sinon celles du Supply (templates_slot/)."""
    d = BASE / "templates_whm" / "slot"
    return d if d.is_dir() and any(d.glob("*.png")) else BASE / "templates_slot"


# noms de fichiers a essayer pour certaines images (le 1er trouve gagne)
ALIASES = {
    "start": ("whmchallenge.png", "start.png"),      # dans l'evenement le bouton s'appelle "Challenge", pas "Start"
    # "Challenge Again (3s)" a la fin de chaque combat : bouton PROPRE a l'evenement (il ne ressemble ni au "Challenge"
    # du lancement (0,40) ni au "Challenge Again" du Night Ops (0,41))
    "challagain": ("whmchallengeagain.png", "challagain.png"),
    # "Exit Stage" de l'evenement = la grande fenetre de sortie (templates/exitstage.png), PAS le petit bouton du
    # Hero Trial (templates_hero/exit.png, qui ne lui ressemble qu'a 0,42). whmexit.png permet de le remplacer.
    "exit":  ("whmexit.png", "exitstage.png", "exit.png"),
}


def tpl_path(name):
    """L'image est cherchee dans templates_whm/, puis dans les dossiers des autres modes."""
    if name == "card":
        names = (f"{EV_CARD}{LEVEL}.png",)                 # whmlvl2.png ou hallwaylvl2.png
    elif name == "entrance":
        names = (EV_BANNER,)                               # entrance.png ou hallway.png
    elif name == "entrance2":
        names = (EV_BANNER.replace(".png", "2.png"),)      # entrance2.png ou hallway2.png (vignette non selectionnee)
    else:
        names = ALIASES.get(name, (f"{name}.png",))
    for fname in names:
        for d in TPL_DIRS:
            p = d / fname
            if p.exists():
                return p
    return None


def load_templates(strict=True):
    global PICK, GATE
    missing = []
    for name, spec in SPEC.items():
        if name == "am":
            continue                                   # le perso vient de heroes.py / templates_chars/
        p = tpl_path(name)
        if p is None and name == "entrance2":
            continue                                   # vignette facultative
        if p is None:
            missing.append("templates_whm/" + (f"{EV_CARD}{LEVEL}.png" if name == "card" else EV_BANNER if name == "entrance" else f"{name}.png"))
            continue
        T[name] = Tpl(p, **spec)
        if name == "entrance2":                        # vignettes des AUTRES evenements : elles se ressemblent (Hallway / Library),
            for ev, (_n, banner, _c) in EVENTS.items():   # on verifie que c'est bien la notre qui ressemble le plus
                o = next((d / banner.replace(".png", "2.png") for d in TPL_DIRS
                          if (d / banner.replace(".png", "2.png")).exists()), None)
                if ev != EVENT and o is not None:
                    OTHER_VIG[ev] = Tpl(o, **spec)
        if name == "plusieursfois":                      # on garde l'image de la case VIDE pour reconnaitre son etat
            full = cv2.imread(str(p), cv2.IMREAD_COLOR)
            H, W = full.shape[:2]
            bw, bh = int(0.075 * W), int(0.32 * H)
            cx, cy = int(CHECKBOX[0] * W), int(CHECKBOX[1] * H)
            BOX_REF["img"] = full[max(0, cy - bh // 2):cy + bh // 2, max(0, cx - bw // 2):cx + bw // 2].copy()
    try:
        PICK = heroes.Picker(heroes.chosen_name(), nt.adb, log, fail=Done)
        T["am"] = heroes.RowTpl(PICK, cd=SPEC["am"]["cd"] * nt.CD_SCALE)
        VERIFY = "--no-verify" not in sys.argv and heroes.chosen_explicit()
        if not heroes.chosen_explicit():      # aucun perso choisi : on garde celui qui est deja place, on ne le remplace pas
            log("aucun perso choisi : je garde celui qui est deja place "
                "(choisis-en un dans l'assistant, sur Discord, ou avec --hero, pour que le bot le change)")
        GATE = heroes.SlotGate(heroes.SlotReader(slot_dir()), PICK.name, log, Done, enabled=VERIFY)
    except FileNotFoundError as e:
        missing.append(str(e))
    if missing and strict:
        raise SystemExit("Images manquantes : " + ", ".join(missing))
    return missing


def run_test():
    fastmatch.FAST = False                             # --test : scores exacts (comparaison couleur partout)
    load_templates(strict=False)
    screen = nt.screenshot()
    print(f"Capture : {screen.shape[1]}x{screen.shape[0]}   {EV_NAME} {LEVEL}   perso : {heroes.pretty(heroes.chosen_name())}")
    print(f"{'image':<15}{'score':>7}{'seuil':>7}  resultat")
    for name, t in T.items():
        score, _loc = t.best(screen)
        hit = t.find(screen)
        print(f"{name:<15}{score:>7.2f}{t.thr:>7.2f}  {'TROUVE -> tap ' + str((int(hit[0]), int(hit[1]))) if hit else '-'}")
    for name in SPEC:
        if name not in T:
            print(f"{name:<15}  fichier manquant")
    # diagnostic du choix du perso : que lit le bot dans l'emplacement de l'equipe ?
    want = heroes.chosen_name()
    d = slot_dir()
    print(f"\n--- perso place (images de {d.name}/) ---")
    try:
        reader = heroes.SlotReader(d)
        if not reader.has(want):
            print(f"pas d'image {d.name}/{want}.png : le bot ne peut PAS savoir quel perso est place (il continuera sans verifier)")
        name, score, pt, scores = reader.identify(screen, want)
        for n, sc in sorted(scores.items(), key=lambda kv: -kv[1])[:5]:
            print(f"   {heroes.pretty(n):<12}{sc:>6.2f}{'  <- reconnu' if sc >= heroes.SLOT_ID_THR else ''}")
        print(f"seuil {heroes.SLOT_ID_THR:.2f} | lu : {heroes.pretty(name) if name else 'AUCUN (illisible)'}"
              f"{' a ' + str((int(pt[0]), int(pt[1]))) if pt else ''} | voulu : {heroes.pretty(want)}")
        if name == want:
            print("-> le bon perso est deja place : le bot lancera directement.")
        elif name:
            print("-> ce n'est pas le bon : le bot tapera l'emplacement pour ouvrir la liste et le changer.")
        else:
            print("-> illisible ici. Si l'emplacement de l'evenement ne ressemble pas a celui du Supply, fais une capture de "
                  "CET emplacement et mets-la dans templates_whm/slot/<perso>.png (meme cadrage que templates_slot/).")
    except Exception as e:
        print("lecture impossible :", e)
    plus = T.get("plus")
    print(f"bouton '+' (emplacement vide) : {'visible' if plus and plus.find(screen) else 'non visible'}")


def main():
    if "--list" in sys.argv:
        print("Evenements : entrance, hallway, library | niveaux : 1, 2, 3")
        return
    if "--test" in sys.argv:
        nt.init_adb()
        return run_test()
    load_templates()
    nt.init_adb()
    if "--no-home" not in sys.argv:
        nav.make(nt.screenshot, nt.tap, nt.adb, log).go()
    HOME = nav.make(nt.screenshot, nt.tap, nt.adb, log)
    RF = nt.make_refill()                              # recharge de stamina (option)
    REPRISE = nav.Reprise(HOME, log)    # ecran inconnu 60 s -> menu + reprise
    seen_t = time.time()                # derniere fois qu'une image a ete reconnue
    WD = watchdog.make(nt.screenshot, nt.tap, nt.adb, log, Done)    # chien de garde (option)
    last_tap = {n: 0.0 for n in SPEC}
    runs, auto_taps, nav_taps = 0, 0, 0
    multi_done = False                                 # case "Consecutive Challenges" deja cochee pour ce lancement
    fights = 0                                         # combats enchaines DANS le multi-run en cours (relances "Challenge")
    card_wait = 0.0                                    # depuis quand Confirm est visible sans la carte du niveau voulu
    confirmed, back_t = False, 0.0                     # niveau valide (Confirm passe) : on ne retouche plus aux ecrans d'avant
    in_battle, t_start = False, 0.0
    awaiting_pick, pick_t = False, 0.0
    last_action = time.time()
    print(f"Perso : {heroes.pretty(PICK.name)}")
    print(f"Evenement WHM : {EV_NAME} {LEVEL} | {MAX_RUNS} multi-run(s). Ctrl+C pour arreter. "
          f"(commence depuis le menu principal)")

    while True:
        screen = nt.screenshot()
        now = time.time()

        # --- plus de stamina (bouton jaune / message / fenetre Stamina Supply) ---
        case = RF.detect(screen) if RF else None
        if case:
            if case != "insustam" and in_battle and now - t_start < 15:
                runs = max(0, runs - 1)
            RF.run(case, screen)
            if case == "insustam" and runs >= MAX_RUNS:
                raise Done(f"{runs}/{MAX_RUNS} multi-run(s) termine(s). Stamina rechargee. Fini !")
            in_battle, awaiting_pick, multi_done, auto_taps, nav_taps = False, False, False, 0, 0
            confirmed, back_t, fights = False, 0.0, 0
            PICK.reset()
            GATE.reset()
            last_tap = {n: 0.0 for n in SPEC}
            last_action = time.time()
            continue

        # --- maintenance / jeu bloque ou ferme : le chien de garde relance le jeu ---
        if WD and WD.poll(screen, in_battle):
            if in_battle:
                runs = max(0, runs - 1)                # le multi-run en cours est perdu : il sera refait
            in_battle, awaiting_pick, multi_done, auto_taps, nav_taps = False, False, False, 0, 0
            confirmed, back_t, fights = False, 0.0, 0
            PICK.reset()
            GATE.reset()
            last_tap = {n: 0.0 for n in SPEC}
            last_action = time.time()
            continue

        # --- CHEMIN RAPIDE : fin de run -> on tape tout de suite, sans tester les autres images ---
        fast_seen, fast_acted = {}, False
        for key in FAST_KEYS:
            if key not in T or now - last_tap[key] < FAST_CD[key]:
                continue

            h = fast_seen[key] = T[key].find(screen)
            if h:
                nt.tap(h[0], h[1])
                last_tap[key] = last_action = time.time()
                if key == "challagain":
                    in_battle, t_start, auto_taps, nav_taps = True, time.time(), 0, 0
                    fights += 1
                if WD and key in ("challagain", "continuechall"):
                    WD.kick()                          # du progres : le chrono "bloque" repart a zero
                log(f"{key} ({h[2]:.2f}) | multi-run {runs}/{MAX_RUNS}")
                time.sleep(FAST_SETTLE)
                fast_acted = True
                break
        if fast_acted:
            continue

        panel_open_prev = confirmed or awaiting_pick or bool(GATE.swap_t)
        need = []
        for key, t in T.items():
            if in_battle and key not in BATTLE_ONLY and now - t_start <= nt.BATTLE_END_DELAY:
                continue                               # juste apres Start : l'ancien ecran peut rester visible un instant
            if panel_open_prev and key in BEHIND_KEYS:
                continue                               # panneau de l'equipe ouvert : inutile de chercher l'ecran de derriere
            need.append(key)
        found = fastmatch.find_many([(k, T[k]) for k in need if k not in fast_seen], screen)   # en parallele, meme resultat
        hits = {}
        for key in need:
            h = fast_seen[key] if key in fast_seen else found[key]
            if h:
                hits[key] = h

        # fin du multi-run : un ecran de navigation reapparait bien apres le dernier Start
        if (in_battle and now - t_start > nt.BATTLE_END_DELAY and "challagain" not in hits and "start" not in hits
                and any(n not in BATTLE_ONLY for n in hits)):
            in_battle, multi_done, confirmed, fights = False, False, False, 0   # la case sera relue, et on pourra re-naviguer
            log("multi-run termine")
            if runs >= MAX_RUNS and "exit" not in hits:
                log("tous les multi-runs sont faits : je quitte le mode")
                HOME.go(timeout=45)                    # Exit Stage / Abandon / fermeture, ou touche Retour
                raise Done(f"{runs}/{MAX_RUNS} multi-run(s) termine(s). Fini !")

        # panneau de lancement visible (ou niveau deja valide) -> on ne retape JAMAIS le niveau / l'evenement : sinon on
        # repartait en boucle "choix du niveau -> Confirm -> choix du niveau" quand le perso etait deja place.
        if any(k in hits for k in PANEL_KEYS):
            confirmed, back_t = True, 0.0
        elif confirmed and not in_battle and hits and all(k in BEHIND_KEYS for k in hits):
            back_t = back_t or now                     # plus que des ecrans d'avant : on est peut-etre vraiment revenu en arriere
            if now - back_t > BACK_RESET:
                confirmed, back_t = False, 0.0
                log("retour en arriere constate : je reprends la navigation depuis le debut")
        acted = False
        panel_open = (not in_battle) and (confirmed or any(k in hits for k in PANEL_KEYS)
                                          or awaiting_pick or bool(GATE.swap_t))
        for key in SPEC:                               # du plus avance au plus ancien
            if key not in hits or now - last_tap[key] < T[key].cd:
                continue
            if runs >= MAX_RUNS and not in_battle and key not in BATTLE_ONLY and key != "exit":
                continue                               # tous les multi-runs sont faits : on ne relance rien
            if panel_open and key in BEHIND_KEYS:
                continue
            if key == "auto" and (not in_battle or auto_taps >= nt.AUTO_MAX_TAPS):
                continue
            if key in ("confirm", "card") and "card" in T:
                # Confirm valide la carte SELECTIONNEE (jaune) : on ne confirme que si c'est bien celle du niveau voulu
                sel = "card" in hits and card_selected(screen, hits["card"], T["card"].size)
                if key == "card" and sel:
                    continue                           # deja selectionnee : on ne la retape pas
                if key == "confirm" and not sel:
                    if "card" in hits:
                        continue                       # la bonne carte est la mais pas selectionnee : on la tape d'abord
                    card_wait = card_wait or now
                    if now - card_wait < 6:
                        continue                       # carte pas (encore) vue : on laisse 6 s avant de confirmer sans elle
                    log(f"(!) carte {EV_NAME} {LEVEL} introuvable : je confirme le niveau deja selectionne")
                if key == "confirm":
                    card_wait = 0.0
            if key == "entrance2" and OTHER_VIG:
                ox = [(o.best_exact(screen), ev) for ev, o in OTHER_VIG.items()]
                (osc, oloc), oev = max(ox, key=lambda v: v[0][0])
                mine = T["entrance2"].best_exact(screen)
                if osc > mine[0] and abs(oloc[0] - mine[1][0]) < 60 and abs(oloc[1] - mine[1][1]) < 60:
                    continue                           # a cet endroit c'est la vignette d'un AUTRE evenement
            if key == "ua":
                continue                               # repere du menu principal seulement : on ne tape JAMAIS dessus (ca ouvre
                                                       # le menu U.A., pas l'evenement). On attend que l'icone de l'evenement apparaisse.
            if key == "exit":                          # on ne quitte que quand tous les multi-runs demandes sont faits
                if runs < MAX_RUNS or in_battle:
                    continue
            if key == "start":
                if runs >= MAX_RUNS and fights == 0:
                    continue                           # on ne DEMARRE plus de nouveau multi-run (mais on relance le combat suivant)
                if "plusieursfois" in hits and not multi_done:
                    continue                           # on coche la case AVANT de lancer
            if key == "plusieursfois":
                if multi_done:
                    continue
                etat = box_state(screen, hits[key])
                if etat == "cochee":                     # deja cochee (elle l'est restee du multi-run d'avant)
                    multi_done = True
                    log("case 'Consecutive Challenges' deja cochee : on ne la touche pas")
                    continue
            if key == "plus" and not in_battle and (GATE.swap_t and awaiting_pick):
                continue
            if key in ("plusieursfois", "start") and not in_battle:
                # exactement comme le Supply (barriere posee sur "Multi-run") : avant de toucher au panneau de lancement,
                # on verifie QUEL perso est place ; si ce n'est pas celui choisi, on tape l'emplacement pour ouvrir la liste.
                if "plus" in hits or (GATE.swap_t and awaiting_pick):
                    continue                           # emplacement vide / liste en cours d'ouverture
                try:
                    state, pt = GATE.check(screen, (hits[key][0], hits[key][1]))
                except Done as e:                      # perso place illisible ET emplacement inconnu : on ne bloque pas le farm
                    log(f"(!) verification du perso impossible : {e}")
                    log("    -> je continue SANS verifier. Place le perso a la main une fois, ou mets des captures de "
                        "l'emplacement dans templates_whm/slot/ (un fichier <perso>.png par perso).")
                    GATE.enabled = False
                    state, pt = "ok", None
                if state == "wait":
                    continue
                if state == "swap":
                    nt.tap(pt[0], pt[1])
                    last_tap[key] = last_action = time.time()
                    awaiting_pick, pick_t = True, time.time()
                    PICK.reset()
                    log("mauvais perso place -> ouverture de la liste")
                    acted = True
                    time.sleep(nt.SETTLE + 0.5)
                    break
            x, y, score = hits[key]
            nt.tap(x, y)
            last_tap[key] = last_action = time.time()
            extra = ""
            if key == "plusieursfois":
                multi_done = True
                extra = " (case cochee : maximum de runs)"
            elif key == "start":
                if fights == 0:                        # 1er combat : c'est le lancement du multi-run
                    runs += 1
                else:
                    extra = f" (relance #{fights})"     # meme bouton "Challenge" a la fin de chaque combat
                fights += 1
                in_battle, t_start, auto_taps, nav_taps = True, time.time(), 0, 0
                if WD:
                    WD.kick()
            elif key == "challagain":                  # (normalement pris par le chemin rapide)
                in_battle, t_start, auto_taps, nav_taps = True, time.time(), 0, 0
                fights += 1
                if WD:
                    WD.kick()
            elif key == "auto":
                auto_taps += 1
            elif key == "card":
                extra = f" ({EV_NAME} {LEVEL})"
            elif key == "entrance2":
                extra = f" (on selectionne {EV_NAME})"
            elif key == "confirm":
                confirmed, back_t = True, 0.0
                extra = " (niveau valide)"
            elif key == "plus":
                awaiting_pick, pick_t = True, time.time()
                PICK.reset()
                ref = hits.get("plusieursfois") or hits.get("start")
                GATE.note_plus((x, y), (ref[0], ref[1]) if ref else None)
            elif key == "exit":
                extra = " (on quitte le mode)"
            if key == "am":
                GATE.done()
            if key in ("am", "start"):
                awaiting_pick = False
                PICK.reset()
            if key == "start":
                GATE.reset()
            if key not in BATTLE_ONLY and key != "start":
                nav_taps += 1
                if nav_taps > NAV_TAP_LIMIT:
                    raise Done(f"Securite : {NAV_TAP_LIMIT} taps de navigation sans pouvoir lancer le combat. "
                               f"Regarde l'ecran (plus de stamina ? plus de tickets d'evenement ? popup inattendu ?).")
            log(f"{key} ({score:.2f}{extra}) | multi-run {runs}/{MAX_RUNS}")
            if key == "exit" and runs >= MAX_RUNS:
                raise Done(f"{runs}/{MAX_RUNS} multi-run(s) termine(s). Fini !")
            acted = True
            time.sleep(nt.SETTLE)
            break

        if acted:
            continue
        # liste des persos ouverte mais le perso voulu n'est pas visible : on fait defiler
        if GATE.swap_t and not in_battle and GATE.swap_stalled(PICK.on_list(screen) or "am" in hits):
            raise Done("Le clic sur l'emplacement du perso n'a PAS ouvert la liste des persos. Change-le a la main, "
                       "puis relance (ou lance avec --no-verify).")
        if awaiting_pick and not in_battle and "am" not in hits:
            if now - pick_t > 90:
                awaiting_pick = False
            elif PICK.step(screen):
                last_action = time.time()
                continue
        if nt.CLICK_ENABLED and in_battle and not any(n not in BATTLE_ONLY for n in hits):
            h, w = screen.shape[:2]
            tap_burst(nt.CLICK_POS[0] * w, nt.CLICK_POS[1] * h, BURST)
            continue
        if not in_battle and runs >= MAX_RUNS and not any(n in BATTLE_ONLY for n in hits):
            if "exit" not in hits:
                log("tous les multi-runs sont faits : je quitte le mode")
                HOME.go(timeout=45)
            raise Done(f"{runs}/{MAX_RUNS} multi-run(s) termine(s). Fini !")
        if hits:
            seen_t = now
        if not in_battle and REPRISE.due(now - max(last_action, seen_t)) and REPRISE.run():
            in_battle, awaiting_pick, multi_done, auto_taps, nav_taps = False, False, False, 0, 0     # comme apres le garde-chien
            confirmed, back_t, fights = False, 0.0, 0
            PICK.reset()
            GATE.reset()
            last_tap = {n: 0.0 for n in SPEC}
            last_action = seen_t = time.time()
            continue
        if not in_battle and now - last_action > nt.IDLE_LIMIT and not WD:
            try:                                       # capture de l'ecran bloque : pour savoir quelle image manque
                d = BASE / "debug"
                d.mkdir(exist_ok=True)
                f = d / f"whm_{time.strftime('%H%M%S')}.png"
                cv2.imwrite(str(f), screen)
                log(f"(debug) capture de l'ecran bloque : debug/{f.name}")
            except Exception:
                pass
            raise Done(f"Plus rien reconnu depuis {nt.IDLE_LIMIT}s : arret. Regarde debug/ pour voir l'ecran, "
                       f"et lance 'python auto_whm.py --test' sur cet ecran pour voir quelle image n'est pas reconnue.")
        time.sleep(nt.LOOP_DELAY)


if __name__ == "__main__":
    import journal
    journal.start("whm")                 # journal de session dans logs/ (garde les 10 derniers)
    journal.run(main, Done)               # meme affichage qu'avant + incident (capture, boite noire) si arret anormal
