"""
Enchaine plusieurs modes l'un apres l'autre (chaque bot revient d'abord au menu principal tout seul).

Exemples (depuis le dossier du bot) :
    python enchainer.py supply:money:4:10 supply:gear:2:3 nightops:5
        -> 10 multi-runs Supply Money niveau 4, puis 3 multi-runs Supply Gear niveau 2, puis 5 multi-runs Night Ops
    python enchainer.py supply:money:4:10:deku nightops:5:denki hero:deku,denki
        -> le dernier champ (facultatif) est le perso a placer pour CE mode (sinon : config.json)
    python enchainer.py                      # lit sequence.json (voir sequence.example.json)
    python enchainer.py --dry ...            # affiche seulement le plan, sans rien lancer
    python enchainer.py --refill ...         # recharge la stamina avec le code quand elle manque (Supply / Night Ops) ;
                                             # --no-refill l'interdit ; sans option : config.json ("stamina_refill")

Syntaxe d'une etape :
    supply:<food|money|gear|card|crystals>:<niveau>:<multi-runs>[:perso]
    whm:<niveau 1-3>:<multi-runs>[:perso]            (evenement WHM "Entrance")
    hallway:<niveau 1-3>:<multi-runs>[:perso]        (evenement WHM "Hallway")
    library:<niveau 1-3>:<multi-runs>[:perso]        (evenement WHM "Library")
    aa:<combats>[:bot1,bot2[:floor du reset]]        (Agency Assessment ; ex. aa:10:deku,bakugo:8)
    nightops:<multi-runs>[:perso]
    hero:<perso1,perso2,...>[:perso a placer]        (Hero Trial)

Une etape reussie se termine par "Fini !". Si une etape s'arrete sur une erreur (plus de stamina, ecran inconnu...),
on passe a la suivante, sauf avec --stop-on-error. Ctrl+C arrete tout. Un recapitulatif est affiche a la fin.
"""
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
MAX_LEVEL = {"food": 4, "money": 4, "gear": 2, "card": 4, "crystals": 4}
PAUSE = 3.0                                   # s entre deux etapes
WATCHDOG = None                               # idem pour --watchdog / --no-watchdog
REFILL = None                                 # True / False = --refill / --no-refill transmis aux bots (None : config.json)


def parse_token(tok):
    p = [x.strip() for x in tok.split(":")]
    kind = p[0].lower()
    try:
        if kind == "supply":
            return dict(mode="supply", supply=p[1].lower(), level=int(p[2]), runs=int(p[3]), hero=p[4] if len(p) > 4 else None)
        if kind in ("nightops", "night"):
            return dict(mode="nightops", runs=int(p[1]), hero=p[2] if len(p) > 2 else None)
        if kind in ("hero", "herotrial"):
            return dict(mode="hero", chars=p[1], hero=p[2] if len(p) > 2 else None)
        if kind in ("aa", "agency"):
            return dict(mode="aa", runs=int(p[1]) if len(p) > 1 else 10, bots=p[2] if len(p) > 2 else None,
                        reset=int(p[3]) if len(p) > 3 else None)
        if kind in ("whm", "event", "evenement", "entrance", "hallway", "library"):
            return dict(mode="whm", event=kind if kind in ("hallway", "library") else "entrance", level=int(p[1]),
                        runs=int(p[2]) if len(p) > 2 else 1, hero=p[3] if len(p) > 3 else None)
    except (IndexError, ValueError):
        pass
    raise SystemExit(f"Etape illisible : '{tok}'. Exemples : supply:money:4:10   nightops:5   hero:deku,denki")


def validate(st):
    m = st.get("mode")
    if m == "supply":
        if st.get("supply") not in MAX_LEVEL:
            raise SystemExit(f"Supply : mode '{st.get('supply')}' inconnu. Choix : {', '.join(MAX_LEVEL)}")
        lv = int(st.get("level", 1))
        if not 1 <= lv <= MAX_LEVEL[st["supply"]]:
            raise SystemExit(f"Supply {st['supply']} : niveau {lv} impossible (1 a {MAX_LEVEL[st['supply']]}).")
        st["level"], st["runs"] = lv, max(1, int(st.get("runs", 1)))
    elif m == "nightops":
        st["runs"] = max(1, int(st.get("runs", 1)))
    elif m == "whm":
        lv = int(st.get("level", 3))
        if not 1 <= lv <= 3:
            raise SystemExit(f"Evenement : entree {lv} impossible (1 a 3).")
        st["level"], st["runs"] = lv, max(1, int(st.get("runs", 1)))
        st["event"] = st.get("event") if st.get("event") in ("entrance", "hallway", "library") else "entrance"
    elif m == "aa":
        st["runs"] = max(1, int(st.get("runs", 10)))
    elif m == "hero":
        if not st.get("chars"):
            raise SystemExit("Hero Trial : indique les persos a farmer (ex. hero:deku,denki).")
    else:
        raise SystemExit(f"Mode inconnu : '{m}' (supply, nightops, hero, whm, hallway, library, aa).")
    return st


def label(st):
    h = f" | perso {st['hero']}" if st.get("hero") else ""
    if st["mode"] == "supply":
        return f"Supply {st['supply']} niv.{st['level']} x{st['runs']}{h}"
    if st["mode"] == "nightops":
        return f"Night Ops x{st['runs']}{h}"
    if st["mode"] == "aa":
        return f"Agency Assessment x{st['runs']}" + (f" | bots {st['bots']}" if st.get("bots") else "")
    if st["mode"] == "whm":
        return f"Evenement {st.get('event', 'entrance').capitalize()} {st['level']} x{st['runs']}{h}"
    return f"Hero Trial ({st['chars']}){h}"


def command(st):
    py = [sys.executable, "-u"]
    if st["mode"] == "supply":
        c = py + [str(BASE / "auto_supply.py"), "--mode", st["supply"], "--level", str(st["level"]), "--runs", str(st["runs"])]
    elif st["mode"] == "nightops":
        c = py + [str(BASE / "auto_nightops.py"), "--runs", str(st["runs"])]
    elif st["mode"] == "aa":
        c = py + [str(BASE / "auto_aa.py"), "--runs", str(st["runs"])]
        if st.get("bots"):
            c += ["--bots", str(st["bots"])]
        if st.get("reset") is not None:
            c += ["--reset-floor", str(st["reset"])]
        if WATCHDOG is not None:
            c.append("--watchdog" if WATCHDOG else "--no-watchdog")
        return c                                   # pas de --hero ni de recharge de stamina dans ce mode
    elif st["mode"] == "whm":
        c = py + [str(BASE / "auto_whm.py"), "--event", st.get("event", "entrance"), "--level", str(st["level"]),
                  "--runs", str(st["runs"])]
    else:
        c = py + [str(BASE / "auto_hero_trial.py"), "--chars", st["chars"]]
    if st.get("hero"):
        c += ["--hero", str(st["hero"])]
    if REFILL is not None:
        c.append("--refill" if REFILL else "--no-refill")
    if WATCHDOG is not None:
        c.append("--watchdog" if WATCHDOG else "--no-watchdog")
    return c


def run_step(st):
    """-> (ok, derniere ligne). ok = le bot a fini par 'Fini !'."""
    last = ""
    proc = subprocess.Popen(command(st), cwd=str(BASE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    try:
        for line in proc.stdout:
            print(line, end="", flush=True)
            if line.strip():
                last = line.strip()
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        raise
    return ("Fini !" in last and proc.returncode == 0), last


def load_plan(args):
    toks = [a for a in args if not a.startswith("--")]
    if toks:
        return [validate(parse_token(t)) for t in toks]
    f = BASE / "sequence.json"
    if not f.exists():
        raise SystemExit("Aucune etape. Exemple : python enchainer.py supply:money:4:10 supply:gear:2:3 nightops:5\n"
                         "(ou cree sequence.json : voir sequence.example.json)")
    data = json.loads(f.read_text(encoding="utf-8"))
    return [validate(dict(s)) for s in data]


def main():
    # arret propre (Stop de l'assistant sous Linux/Mac) : on coupe aussi le bot en cours
    try:
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    except Exception:
        pass
    global REFILL, WATCHDOG
    args = sys.argv[1:]
    if "--refill" in args:
        REFILL = True
    elif "--no-refill" in args:
        REFILL = False
    if "--watchdog" in args:
        WATCHDOG = True
    elif "--no-watchdog" in args:
        WATCHDOG = False
    plan = load_plan(args)
    print("Plan :")
    for i, st in enumerate(plan, 1):
        print(f"  {i}. {label(st)}")
    if "--dry" in args:
        return
    stop_on_error = "--stop-on-error" in args
    results = []
    try:
        for i, st in enumerate(plan, 1):
            print(f"\n===== Etape {i}/{len(plan)} : {label(st)} =====", flush=True)
            ok, last = run_step(st)
            results.append((label(st), ok, last))
            if not ok:
                print(f"\n(!) Etape {i} NON terminee : {last}")
                if stop_on_error:
                    break
                print("    -> on passe a l'etape suivante.")
            if i < len(plan):
                time.sleep(PAUSE)
    except KeyboardInterrupt:
        print("\nArrete.")
    print("\n===== Recapitulatif =====")
    for name, ok, last in results:
        print(f"  {'OK    ' if ok else 'ECHEC '} {name}" + ("" if ok else f"  ({last[:90]})"))
    if len(results) < len(plan):
        print(f"  ({len(plan) - len(results)} etape(s) non lancee(s))")


if __name__ == "__main__":
    main()
