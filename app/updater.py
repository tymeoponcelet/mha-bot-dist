#!/usr/bin/env python3
"""MHA Bot - lanceur + mise a jour automatique.

Ce fichier a DEUX roles :
  1. il est compile en MHA-Launcher.exe (build_launcher.bat) : le seul fichier que tes amis recoivent, une fois ;
  2. il est aussi publie dans app/updater.py : le .exe lui "passe la main", donc la logique de mise a jour
     elle-meme se met a jour sans refaire le .exe.

Dossier d'installation (ROOT) : %LOCALAPPDATA%\\MHA-Bot   (ou --root, ou variable MHA_ROOT)
    runtime/python/   Python installe tout seul au 1er lancement (aucune installation manuelle)
    app/              scripts + images, mis a jour depuis GitHub (config.json et discord_config.json ne sont JAMAIS touches)
    installed.json    ce qui est installe (version + empreintes)

Utilisation :
    MHA-Launcher.exe            lance tout (python -> mise a jour -> modules -> fenetre MHA)
    updater.py --update         met a jour et affiche une ligne "RESULT {json}"  (utilise par /maj et par la fenetre)
    updater.py --check          regarde s'il y a du nouveau sans rien modifier
    updater.py --launch         comme le .exe, mais depuis Python
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_REPO = "REMPLACE/MON-DEPOT"        # remplace automatiquement par build_launcher.py ("compte/depot")
DEFAULT_BRANCH = "main"
PY_VERSION = "3.12.8"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-amd64.exe"

FROZEN = getattr(sys, "frozen", False)
IS_WIN = os.name == "nt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
# Jamais ecrases, jamais supprimes, meme si un jour ils se retrouvaient dans le manifeste.
PROTECTED_NAMES = {"config.json", "discord_config.json", "sequence.json", "planning.json", "profils.json"}
PROTECTED_DIRS = {"debug", "platform-tools", "__pycache__"}


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- reglages
def default_root():
    if os.environ.get("MHA_ROOT"):
        return Path(os.environ["MHA_ROOT"])
    if FROZEN:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "MHA-Bot"
    here = Path(__file__).resolve().parent
    return here.parent if here.name == "app" else here


def load_settings(root):
    s = {"repo": DEFAULT_REPO, "branch": DEFAULT_BRANCH}
    p = root / "launcher.json"                      # facultatif : permet de changer de depot sans refaire le .exe
    if p.exists():
        try:
            s.update(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return s


def raw_base(s):
    base = os.environ.get("MHA_BASE_URL")           # pour les tests (serveur local)
    if base:
        return base.rstrip("/")
    if s["repo"].startswith("REMPLACE"):
        raise RuntimeError("Lanceur non configure : refais le .exe avec build_launcher.bat (il demande ton depot GitHub).")
    return f"https://raw.githubusercontent.com/{s['repo']}/{s['branch']}"


# ---------------------------------------------------------------- outils
def http_get(url, timeout=30, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mha-updater"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:                      # noqa: BLE001 - on reessaie quoi qu'il arrive
            last = e
            time.sleep(1 + i)
    raise RuntimeError(f"telechargement impossible : {url} ({last})")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_path(app, rel):
    """Chemin local d'un fichier du manifeste, ou ValueError s'il est suspect / protege."""
    r = Path(rel)
    if r.is_absolute() or ".." in r.parts or not r.parts:
        raise ValueError(f"chemin refuse : {rel}")
    if r.name in PROTECTED_NAMES or any(part in PROTECTED_DIRS for part in r.parts[:-1]):
        raise ValueError(f"fichier protege : {rel}")
    return app.joinpath(*r.parts)


def replace_retry(src, dst, tries=10):
    dst.parent.mkdir(parents=True, exist_ok=True)
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:                     # fichier brievement ouvert par un script qui tourne
            if i == tries - 1:
                raise
            time.sleep(0.5)


def read_installed(root):
    try:
        return json.loads((root / "installed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": None, "files": []}


# ---------------------------------------------------------------- mise a jour
def fetch_manifest(settings):
    data = http_get(f"{raw_base(settings)}/version.json?t={int(time.time())}")
    m = json.loads(data.decode("utf-8"))
    if not isinstance(m.get("files"), dict) or not m.get("version"):
        raise RuntimeError("version.json invalide")
    return m


def plan(root, m):
    app = root / "app"
    todo = []
    for rel, info in m["files"].items():
        try:
            p = safe_path(app, rel)
        except ValueError:
            continue
        if not p.exists() or p.stat().st_size != info["size"] or sha256_file(p) != info["sha256"]:
            todo.append(rel)
    return todo


def do_update(root, settings, say=log, check_only=False):
    m = fetch_manifest(settings)
    installed = read_installed(root)
    todo = plan(root, m)
    removed = []
    for rel in installed.get("files", []):
        if rel in m["files"]:
            continue
        try:
            if safe_path(root / "app", rel).exists():
                removed.append(rel)
        except ValueError:
            pass
    result = {"version": m["version"], "previous": installed.get("version"), "message": m.get("message", ""),
              "updated": len(todo), "removed": len(removed), "files": (todo + removed)[:40]}
    result["restart_needed"] = any(r.endswith(".py") for r in todo + removed)
    result["hub_changed"] = "hub.py" in todo
    if check_only:
        result["available"] = bool(todo or removed)
        return result
    app = root / "app"
    app.mkdir(parents=True, exist_ok=True)
    if todo:
        stage = root / "staging"
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True)
        base, done = raw_base(settings), [0]

        def fetch(rel):
            url = f"{base}/app/{urllib.parse.quote(rel, safe='/')}"
            data = http_get(url)
            if sha256_bytes(data) != m["files"][rel]["sha256"]:
                raise RuntimeError(f"fichier corrompu ou depot modifie en cours de route : {rel}")
            dst = stage.joinpath(*Path(rel).parts)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            done[0] += 1
            if done[0] % 25 == 0 or done[0] == len(todo):
                say(f"  {done[0]}/{len(todo)} fichiers telecharges")

        say(f"Mise a jour : {len(todo)} fichier(s) a telecharger...")
        try:
            with ThreadPoolExecutor(6) as ex:
                list(ex.map(fetch, todo))           # tout est telecharge ET verifie AVANT de toucher a app/
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        for rel in todo:
            replace_retry(stage.joinpath(*Path(rel).parts), safe_path(app, rel))
        shutil.rmtree(stage, ignore_errors=True)
    for rel in removed:
        try:
            safe_path(app, rel).unlink()
        except (OSError, ValueError):
            pass
    (root / "installed.json").write_text(
        json.dumps({"version": m["version"], "files": sorted(m["files"])}, indent=1), encoding="utf-8")
    return result


# ---------------------------------------------------------------- Python + modules
def runtime_python(root):
    return root / "runtime" / "python" / "python.exe" if IS_WIN else Path(sys.executable)


def ensure_python(root, say=log):
    py = runtime_python(root)
    if py.exists() or not IS_WIN:
        return py
    say(f"Installation de Python {PY_VERSION} (une seule fois, ~1 minute)...")
    inst = root / "runtime" / f"python-{PY_VERSION}-installer.exe"
    inst.parent.mkdir(parents=True, exist_ok=True)
    inst.write_bytes(http_get(PY_URL, timeout=180))
    r = subprocess.run([str(inst), "/quiet", "InstallAllUsers=0", "PrependPath=0", "Include_launcher=0",
                        "Include_test=0", "Include_doc=0", "Include_pip=1", "Shortcuts=0", "AssociateFiles=0",
                        f"TargetDir={py.parent}"], timeout=900)
    inst.unlink(missing_ok=True)
    if r.returncode != 0 or not py.exists():
        raise RuntimeError(f"l'installation de Python a echoue (code {r.returncode})")
    return py


def ensure_deps(root, py, say=log):
    req = root / "app" / "requirements.txt"
    if not req.exists():
        return
    want = sha256_file(req)
    mark = root / "runtime" / "deps.sha"
    if mark.exists() and mark.read_text().strip() == want:
        return
    say("Installation des modules (opencv, numpy, discord.py) : 1 a 2 minutes, patience...")
    r = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError("pip a echoue :\n" + (r.stdout + r.stderr)[-1200:])
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text(want)


def launch_hub(root, py):
    hub = root / "app" / "hub.py"
    if not hub.exists():
        raise RuntimeError("app/hub.py est introuvable : la mise a jour n'a pas abouti.")
    kw = {"creationflags": NO_WINDOW | NEW_GROUP} if IS_WIN else {"start_new_session": True}
    subprocess.Popen([str(py), str(hub), "--root", str(root)], cwd=str(root / "app"), close_fds=True, **kw)


# ---------------------------------------------------------------- commandes
def cmd_launch(root, settings):
    py = ensure_python(root)
    app_ok = (root / "app" / "hub.py").exists()
    try:
        r = do_update(root, settings)
        log(f"A jour : version {r['version']}" + (f" ({r['updated']} fichier(s) mis a jour)" if r["updated"] else ""))
    except Exception as e:                          # noqa: BLE001
        if not app_ok:
            raise RuntimeError(f"premiere installation impossible : {e}") from e
        log(f"(!) Mise a jour impossible ({e}) : je lance la version deja installee.")
    ensure_deps(root, py)
    launch_hub(root, py)
    log("MHA Bot demarre.")


def cmd_frozen(root, settings):
    """Le .exe : il ne sait faire que le strict minimum, puis laisse app/updater.py faire le reste."""
    # app/updater.py (publie) ne connait pas le depot : seul le .exe l'a en dur. On le note dans launcher.json,
    # sinon /maj, le bouton "Mettre a jour" et les lancements suivants echoueraient ("Lanceur non configure").
    lj = root / "launcher.json"
    if not lj.exists() and not settings["repo"].startswith("REMPLACE"):
        lj.write_text(json.dumps({"repo": settings["repo"], "branch": settings["branch"]}), encoding="utf-8")
    py = ensure_python(root)
    app_updater = root / "app" / "updater.py"
    if not app_updater.exists():
        log("Premiere installation...")
        do_update(root, settings)
    return subprocess.call([str(py), str(app_updater), "--launch", "--root", str(root)])


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="MHA Bot - lanceur et mise a jour")
    ap.add_argument("--root", help="dossier d'installation")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--update", action="store_true", help="met a jour puis affiche RESULT {json}")
    g.add_argument("--check", action="store_true", help="regarde s'il y a du nouveau (sans rien changer)")
    g.add_argument("--launch", action="store_true", help="met a jour puis ouvre la fenetre MHA")
    a = ap.parse_args()
    root = Path(a.root) if a.root else default_root()
    root.mkdir(parents=True, exist_ok=True)
    settings = load_settings(root)
    try:
        if a.update or a.check:
            r = do_update(root, settings, say=lambda m: print(m, file=sys.stderr, flush=True), check_only=a.check)
            if a.update and "requirements.txt" in r["files"]:
                try:
                    ensure_deps(root, runtime_python(root), say=lambda m: print(m, file=sys.stderr, flush=True))
                except Exception as e:                  # noqa: BLE001 - les fichiers sont installes : on le signale sans tout annuler
                    r["deps_error"] = str(e)[-300:]
            print("RESULT " + json.dumps(r, ensure_ascii=False), flush=True)
            return 0
        if a.launch or not FROZEN:
            cmd_launch(root, settings)
            return 0
        return cmd_frozen(root, settings)
    except Exception as e:                          # noqa: BLE001
        if a.update or a.check:
            print("RESULT " + json.dumps({"error": str(e)}, ensure_ascii=False), flush=True)
        else:
            log(f"\nERREUR : {e}")
            if sys.stdin and sys.stdin.isatty():
                input("Appuie sur Entree pour fermer...")
        return 1


if __name__ == "__main__":
    sys.exit(main())
