"""Farms programmes : planning.json + une tache du Planificateur de taches Windows par creneau (dossier "MHA-Bot").

    python planificateur.py --list                 # creneaux enregistres (et prochaine execution)
    python planificateur.py --add 02:00 tous "supply:money:4:10 nightops:5"
    python planificateur.py --remove 3
    python planificateur.py --sync                 # recree toutes les taches Windows depuis planning.json
    (--run <n> : appele par la tache Windows a l'heure prevue)

- La tache ne contient QUE le numero du creneau : la sequence est relue (et revalidee) dans planning.json au lancement.
- Elle peut sortir le PC de la veille ; si le PC etait eteint a l'heure prevue, le creneau est saute (pas de rattrapage).
- Au lancement : creneau ignore si un farm tourne deja ; sinon le PC reste eveille pendant le farm (enchainer.py,
  fenetre cachee), puis une notification part sur Discord (logs/incidents, type "planning").
- planning.json est une donnee perso du PC (comme config.json) : jamais publie, jamais ecrase par une mise a jour.
"""
import os  # noqa: E402
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # memoire : sinon numpy reserve ~370 Mo par processus (16/11/2026 : « Insufficient memory » chez un ami)
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
PLAN = BASE / "planning.json"
PID = BASE / "logs" / "planning.pid"
TASK_PATH = "\\MHA-Bot\\"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
JOURS = {"tous": "Tous les jours", "semaine": "Du lundi au vendredi", "weekend": "Le week-end", "une_fois": "Une seule fois"}
PS_DAYS = {"semaine": "Monday,Tuesday,Wednesday,Thursday,Friday", "weekend": "Saturday,Sunday"}
TOKEN_RX = re.compile(r"^[a-z0-9:,_-]+$")
MAX_HOURS = 12                                 # duree max d'un farm programme (la tache Windows l'arrete au-dela)


# ---------------------------------------------------------------- planning.json
def load(path=None):
    try:
        data = json.loads(Path(path or PLAN).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save(items, path=None):
    p = Path(path or PLAN)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def validate(heure, jours, sequence):
    """-> (heure 'HH:MM', jours, [tokens]) ou ValueError avec un message clair."""
    m = re.fullmatch(r"\s*(\d{1,2})[:hH](\d{2})\s*", str(heure))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"Heure invalide : « {heure} » (exemple : 02:00 ou 2h30).")
    heure = f"{int(m.group(1)):02d}:{m.group(2)}"
    if jours not in JOURS:
        raise ValueError(f"Jours invalides : « {jours} » ({', '.join(JOURS)}).")
    tokens = str(sequence).lower().split()
    if not tokens:
        raise ValueError("Séquence vide (exemple : supply:money:4:10 nightops:5).")
    import enchainer as ec
    for t in tokens:
        if not TOKEN_RX.match(t):
            raise ValueError(f"Étape invalide : « {t} ».")
        try:
            ec.validate(ec.parse_token(t))
        except SystemExit as e:
            raise ValueError(f"Étape invalide : « {t} » ({e}).") from None
    return heure, jours, tokens


def describe(it, short=False):
    """'#3  02:00  tous les jours  ·  Supply Bounty (argent) niveau 4 · 10 multi-run(s) → Night Ops · 5 multi-run(s)'."""
    import enchainer as ec
    steps = ec.describe_tokens(it["sequence"]).splitlines()
    steps = [x.split(") ", 1)[-1] for x in steps]
    seq = " → ".join(steps) if not short else (steps[0] + (f" (+{len(steps) - 1} étape(s))" if len(steps) > 1 else ""))
    opts = "".join(f"  ·  {icon} {'oui' if it[k] else 'non'}" for k, icon in (("refill", "🔋"), ("watchdog", "🛡️"))
                   if it.get(k) is not None)
    return (f"#{it['id']}  {it['heure']}  {JOURS.get(it['jours'], it['jours']).lower()}  ·  {seq}{opts}"
            + ("" if it.get("actif", True) else "  (désactivé)"))


def flags(it):
    """Options du creneau pour enchainer.py (absentes = reglage general au moment du farm)."""
    out = []
    if it.get("refill") is not None:
        out.append("--refill" if it["refill"] else "--no-refill")
    if it.get("watchdog") is not None:
        out.append("--watchdog" if it["watchdog"] else "--no-watchdog")
    return out


# ---------------------------------------------------------------- Planificateur de taches Windows
def _ps(script, timeout=60):
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _q(s):
    return "'" + str(s).replace("'", "''") + "'"


def task_name(i):
    return f"creneau-{int(i)}"


def register(it, plan_path=None, extra=""):
    """Cree (ou remplace) la tache Windows du creneau. -> (ok, message)."""
    if os.name != "nt":
        return False, "Planificateur de tâches : Windows seulement."
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    args = f'"{BASE / "planificateur.py"}" --run {int(it["id"])}'
    if plan_path:
        args += f' --plan "{plan_path}"'
    args += f" {extra}".rstrip()
    h = it["heure"]
    if it["jours"] == "tous":
        trig = f"New-ScheduledTaskTrigger -Daily -At {_q(h)}"
    elif it["jours"] == "une_fois":
        trig = f"New-ScheduledTaskTrigger -Once -At {_q(next_time(h))}"
    else:
        trig = f"New-ScheduledTaskTrigger -Weekly -DaysOfWeek {PS_DAYS[it['jours']]} -At {_q(h)}"
    script = (
        f"$a = New-ScheduledTaskAction -Execute {_q(exe)} -Argument {_q(args)} -WorkingDirectory {_q(BASE)};"
        f"$t = {trig};"
        "$s = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        f"-ExecutionTimeLimit (New-TimeSpan -Hours {MAX_HOURS}) -MultipleInstances IgnoreNew;"
        "$s.StartWhenAvailable = $false;"
        f"Register-ScheduledTask -TaskPath {_q(TASK_PATH)} -TaskName {_q(task_name(it['id']))} -Action $a -Trigger $t "
        f"-Settings $s -Description {_q('MHA Bot : ' + ' '.join(it['sequence']))} -Force | Out-Null; 'OK'")
    code, out = _ps(script)
    return code == 0 and "OK" in out, out.strip()[-300:]


def unregister(i):
    code, out = _ps(f"Unregister-ScheduledTask -TaskPath {_q(TASK_PATH)} -TaskName {_q(task_name(i))} -Confirm:$false "
                    "-ErrorAction SilentlyContinue; 'OK'")
    return code == 0


def next_runs():
    """-> {id: 'JJ/MM HH:MM'} prochaine execution prevue par Windows."""
    code, out = _ps(f"Get-ScheduledTask -TaskPath {_q(TASK_PATH)} -ErrorAction SilentlyContinue | ForEach-Object {{ "
                    "$i = $_ | Get-ScheduledTaskInfo; \"$($_.TaskName)|$($i.NextRunTime.ToString('dd/MM HH:mm'))\" }")
    res = {}
    for l in out.splitlines():
        m = re.match(r"creneau-(\d+)\|(.*)", l.strip())
        if m:
            res[int(m.group(1))] = m.group(2).strip() or "-"
    return res


def next_time(h):
    """Prochaine occurrence de h ('HH:MM') : aujourd'hui si pas encore passee, sinon demain (format pour PowerShell)."""
    hh, mm = (int(x) for x in h.split(":"))
    lt = time.localtime()
    t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, -1))
    if t <= time.time() + 30:
        t += 86400
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


# ---------------------------------------------------------------- ajout / suppression
def add(heure, jours, sequence, path=None, do_register=True, refill=None, watchdog=None):
    """-> (creneau, message). ValueError si la demande est invalide.
    refill / watchdog : True / False = choix du creneau ; None = reglage general au moment du farm."""
    heure, jours, tokens = validate(heure, jours, sequence)
    items = load(path)
    it = {"id": max([x["id"] for x in items] + [0]) + 1, "heure": heure, "jours": jours, "sequence": tokens,
          "actif": True, "cree": time.strftime("%Y-%m-%d %H:%M")}
    for k, v in (("refill", refill), ("watchdog", watchdog)):
        if v is not None:
            it[k] = bool(v)
    msg = ""
    if do_register:
        ok, msg = register(it, plan_path=path if path else None)
        if not ok:
            raise ValueError(f"Le Planificateur de tâches Windows a refusé : {msg}")
    items.append(it)
    save(items, path)
    return it, msg


def remove(i, path=None, do_register=True):
    items = load(path)
    keep = [x for x in items if x["id"] != int(i)]
    if len(keep) == len(items):
        return False
    if do_register:
        unregister(i)
    save(keep, path)
    return True


# ---------------------------------------------------------------- farm en cours ?
def farm_en_cours():
    """True si un bot (auto_*.py / enchainer.py, hors --test) tourne deja sur ce PC."""
    code, out = _ps("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | ForEach-Object { $_.CommandLine }")
    for l in out.splitlines():
        if re.search(r"(auto_\w+|enchainer)\.py", l) and "--test" not in l and "--dry" not in l:
            return True
    return False


def stop_planned():
    """Arrete le farm programme en cours (/stop). -> True si quelque chose a ete arrete."""
    try:
        pid = int(PID.read_text().strip())
    except (OSError, ValueError):
        return False
    r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    PID.unlink(missing_ok=True)
    return r.returncode == 0


def _awake(on):
    """Empeche la mise en veille pendant le farm (Windows)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:                          # noqa: BLE001
        pass


def _notify(title, lines, live=True):
    import journal
    journal._state["script"] = "planning"
    for l in lines:
        journal._lines.append(l)
    journal.incident(title, live=live, kind="planning")


def run(i, path=None, dry=False):
    """Execution d'un creneau (par la tache Windows)."""
    items = load(path)
    it = next((x for x in items if x["id"] == int(i)), None)
    if not it or not it.get("actif", True):
        return 0
    try:
        _h, _j, tokens = validate(it["heure"], it["jours"], " ".join(it["sequence"]))
    except ValueError as e:
        _notify(f"Farm programmé #{i} non lancé : {e}", [])
        return 1
    if farm_en_cours():
        _notify(f"Farm programmé #{i} ignoré : un farm tourne déjà ({' '.join(tokens)})", [])
        return 0
    PID.parent.mkdir(parents=True, exist_ok=True)
    _awake(True)
    t0 = time.time()
    cmd = [str(Path(sys.executable).with_name("python.exe")) if Path(sys.executable).name.lower() == "pythonw.exe"
           else sys.executable, str(BASE / "enchainer.py"), *tokens] + flags(it) + (["--dry"] if dry else [])
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", MHA_SOURCE="planning")
    env.pop("MHA_LOG", None)
    try:
        p = subprocess.Popen(cmd, cwd=str(BASE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", env=env, creationflags=NO_WINDOW)
        PID.write_text(str(p.pid))
        out = [l.rstrip() for l in p.stdout]
        code = p.wait()
    finally:
        _awake(False)
        PID.unlink(missing_ok=True)
    dur = int(time.time() - t0)
    recap = [l for l in out if l.strip()][-10:]
    ok = code == 0 and not any("ECHEC" in l for l in out)
    _notify(f"Farm programmé #{i} {'terminé' if ok else 'terminé avec des erreurs'} ({dur // 60} min) : {' '.join(tokens)}",
            recap)
    if it["jours"] == "une_fois":
        remove(i, path, do_register=not dry)
    return code


def main():
    a = sys.argv[1:]
    path = a[a.index("--plan") + 1] if "--plan" in a else None
    if "--run" in a:
        return run(int(a[a.index("--run") + 1]), path, dry="--dry" in a)
    if "--add" in a:
        k = a.index("--add")
        try:
            it, _m = add(a[k + 1], a[k + 2], a[k + 3], path)
        except ValueError as e:
            print("ERREUR :", e)
            return 1
        print("Ajouté :", describe(it))
        return 0
    if "--remove" in a:
        print("Supprimé." if remove(int(a[a.index("--remove") + 1]), path) else "Créneau introuvable.")
        return 0
    if "--sync" in a:
        for it in load(path):
            print(describe(it), "->", "OK" if register(it)[0] else "ECHEC")
        return 0
    nxt = next_runs()
    items = load(path)
    for it in items:
        print(describe(it) + f"  ·  prochaine : {nxt.get(it['id'], 'tâche Windows absente !')}")
    if not items:
        print("Aucun farm programmé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
