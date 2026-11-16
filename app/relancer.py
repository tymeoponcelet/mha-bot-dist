"""Relancer le jeu (ferme -> relance -> menu principal), puis rejouer un farm.

    python relancer.py -- python -u auto_supply.py --mode money --level 4 --runs 10
    python relancer.py --no-farm          # juste remettre le jeu au menu principal

- Instance : MHA_DEVICE (2 comptes) sinon config.json, comme les autres scripts.
- Fermeture et retour au menu : memes etapes que le garde-chien (watchdog.close_game / login).
- Le farm est lance ensuite et sa sortie est recopiee (le bot Discord et le journal la voient).
- Menu principal non atteint -> arret avec incident (alerte Discord).
"""
import os
import subprocess
import sys

import auto_nightops as nt
import watchdog
from auto_nightops import Done, log


def farm_command(argv):
    """Commande du farm : tout ce qui suit « -- » (vide = pas de farm)."""
    return argv[argv.index("--") + 1:] if "--" in argv else []


def run_farm(cmd):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    p = subprocess.Popen(cmd, cwd=str(nt.BASE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", env=env)
    last = ""
    try:
        for line in p.stdout:
            print(line, end="", flush=True)
            if line.strip():
                last = line.strip()
        p.wait()
    except KeyboardInterrupt:
        p.terminate()
        raise
    return p.returncode, last


def main():
    cmd = farm_command(sys.argv)
    nt.init_adb()
    wd = watchdog.Watchdog(nt.screenshot, nt.tap, nt.adb, log, Done)
    wd.ensure_package()
    log("=== RELANCE DU JEU : fermeture, relance, retour au menu principal ===")
    wd.close_game()
    if not wd.login():
        raise Done("Relance : menu principal non atteint apres la relance du jeu. Regarde la capture, puis relance.")
    log("menu principal atteint")
    if not cmd or "--no-farm" in sys.argv:
        raise Done("Jeu relance, menu principal atteint. Fini !")
    log("=== RELANCE DU FARM ===")
    import journal
    journal._last["t"] = 0.0          # le farm relance capture (et se surveille) lui-meme : plus de « Bloque » ici
    code, last = run_farm(cmd)
    if "Fini" in last and code == 0:
        raise Done(last)
    raise Done(f"Le farm relance s'est arrete : {last or code}")


if __name__ == "__main__":
    import journal
    journal.start("relance")                     # journal, incidents ; le farm relance est compte dans les stats a part
    journal.run(main, Done)
