#!/usr/bin/env python3
"""Fenetre principale MHA : ouvre l'assistant, fait tourner le bot Discord (relance auto) et met a jour.

Lancee par MHA-Launcher.exe. Remplace lancer_assistant.bat + lancer_discord.bat.
Codes de sortie de discord_bot.py :  2 = erreur de configuration (on ne relance pas)
                                     3 = redemarrage demande par /maj (relance tout de suite)
                                     autre = plantage (relance apres 30 s, comme l'ancien .bat)
"""
import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext

APP = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--root")
ARGS, _ = ap.parse_known_args()
ROOT = Path(ARGS.root) if ARGS.root else APP.parent
PY = sys.executable
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
SETTINGS = ROOT / "hub.json"


def load_settings():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def version():
    try:
        return json.loads((ROOT / "installed.json").read_text(encoding="utf-8")).get("version") or "?"
    except (OSError, ValueError):
        return "?"


class Hub(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MHA Bot")
        self.geometry("620x470")
        self.minsize(520, 380)
        self.q = queue.Queue()
        self.proc = None
        self.bot_thread = None
        self.stop_flag = False
        self.fast_restart = False
        self.settings = load_settings()

        top = tk.Frame(self)
        top.pack(fill="x", padx=12, pady=(12, 4))
        tk.Label(top, text="MHA Bot", font=("Segoe UI", 16, "bold")).pack(side="left")
        self.ver = tk.Label(top, text=f"version {version()}", fg="#777")
        self.ver.pack(side="left", padx=10)

        row = tk.Frame(self)
        row.pack(fill="x", padx=12, pady=6)
        self.b_assist = tk.Button(row, text="Ouvrir l'assistant", width=18, command=self.open_assistant)
        self.b_bot = tk.Button(row, text="Demarrer le bot Discord", width=22, command=self.toggle_bot)
        self.b_upd = tk.Button(row, text="Mettre a jour", width=14, command=self.update)
        self.b_dir = tk.Button(row, text="Dossier", width=9, command=lambda: os.startfile(APP))
        for b in (self.b_assist, self.b_bot, self.b_upd, self.b_dir):
            b.pack(side="left", padx=3)

        self.auto = tk.BooleanVar(value=bool(self.settings.get("auto_bot", False)))
        tk.Checkbutton(self, text="Demarrer le bot Discord automatiquement a l'ouverture", variable=self.auto,
                       command=self.save_settings).pack(anchor="w", padx=12)
        self.status = tk.Label(self, text="Bot : arrete", anchor="w", font=("Segoe UI", 10, "bold"))
        self.status.pack(fill="x", padx=12, pady=(6, 2))
        self.log = scrolledtext.ScrolledText(self, height=14, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.pump)
        if self.auto.get():
            self.after(500, self.start_bot)

    # ------------------------------------------------------------ utilitaires
    def save_settings(self):
        try:
            SETTINGS.write_text(json.dumps({"auto_bot": self.auto.get()}), encoding="utf-8")
        except OSError:
            pass

    def say(self, text):
        self.q.put(("log", text))

    def pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.log.config(state="normal")
                    self.log.insert("end", val + "\n")
                    self.log.see("end")
                    if int(self.log.index("end-1c").split(".")[0]) > 600:
                        self.log.delete("1.0", "100.0")
                    self.log.config(state="disabled")
                elif kind == "status":
                    self.status.config(text=val)
                elif kind == "bot_btn":
                    self.b_bot.config(text=val)
                elif kind == "update_done":
                    self.update_done(val)
                elif kind == "cfg_done":
                    self.start_bot()
        except queue.Empty:
            pass
        self.after(100, self.pump)

    # ------------------------------------------------------------ assistant
    def open_assistant(self):
        subprocess.Popen([PY, str(APP / "setup_assistant.py")], cwd=APP, env=ENV, creationflags=NO_WINDOW)

    # ------------------------------------------------------------ bot Discord
    def toggle_bot(self):
        if self.bot_thread and self.bot_thread.is_alive():
            self.stop_bot()
        else:
            self.start_bot()

    def start_bot(self):
        if self.bot_thread and self.bot_thread.is_alive():
            return
        if not (APP / "discord_config.json").exists():
            self.say("Premiere configuration du bot : une fenetre noire va te poser 3 questions "
                     "(token, ton identifiant Discord, identifiant du serveur).")

            def configure():
                subprocess.call([PY, "configurer_discord.py"], cwd=APP, creationflags=NEW_CONSOLE)
                if (APP / "discord_config.json").exists():
                    self.q.put(("cfg_done", None))
                else:
                    self.say("Configuration annulee.")

            threading.Thread(target=configure, daemon=True).start()
            return
        self.stop_flag = False
        self.bot_thread = threading.Thread(target=self.bot_loop, daemon=True)
        self.bot_thread.start()
        self.q.put(("bot_btn", "Arreter le bot Discord"))

    def stop_bot(self):
        self.stop_flag = True
        self.kill_bot()
        self.q.put(("bot_btn", "Demarrer le bot Discord"))

    def kill_bot(self):
        p = self.proc
        if p and p.poll() is None:
            if os.name == "nt":                     # tue aussi les scripts de farm lances par le bot
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
            else:
                p.terminate()

    def bot_loop(self):
        while not self.stop_flag:
            self.q.put(("status", "Bot : en marche"))
            self.proc = subprocess.Popen([PY, "-u", "discord_bot.py"], cwd=APP, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                         env=ENV, creationflags=NO_WINDOW)
            for line in self.proc.stdout:
                if line.strip():
                    self.say(line.rstrip())
            code = self.proc.wait()
            if self.stop_flag:
                break
            if code == 2:
                self.say("Erreur de configuration (voir ci-dessus). Corrige discord_config.json puis redemarre le bot.")
                break
            fast = code == 3 or self.fast_restart
            self.fast_restart = False
            wait = 1 if fast else 30
            self.say("Redemarrage du bot..." if fast else f"Le bot s'est arrete (code {code}). Relance dans {wait} s.")
            for i in range(wait, 0, -1):
                if self.stop_flag:
                    break
                self.q.put(("status", f"Bot : redemarrage dans {i} s"))
                time.sleep(1)
        self.q.put(("status", "Bot : arrete"))
        self.q.put(("bot_btn", "Demarrer le bot Discord"))

    # ------------------------------------------------------------ mise a jour
    def update(self):
        self.b_upd.config(state="disabled")
        self.say("Recherche de mises a jour...")

        def work():
            res = None
            try:
                r = subprocess.run([PY, str(APP / "updater.py"), "--update", "--root", str(ROOT)], cwd=APP,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   env=ENV, creationflags=NO_WINDOW, timeout=900)
                for line in (r.stdout or "").splitlines():
                    if line.startswith("RESULT "):
                        res = json.loads(line[7:])
            except Exception as e:                  # noqa: BLE001
                res = {"error": str(e)}
            self.q.put(("update_done", res or {"error": "reponse illisible"}))

        threading.Thread(target=work, daemon=True).start()

    def update_done(self, r):
        self.b_upd.config(state="normal")
        if r.get("error"):
            self.say(f"Mise a jour impossible : {r['error']}")
            return
        self.ver.config(text=f"version {r.get('version', '?')}")
        if not r.get("updated") and not r.get("removed"):
            self.say("Deja a jour.")
            return
        self.say(f"Mis a jour : {r['updated']} fichier(s), {r['removed']} supprime(s)."
                 + (f"\n  {r['message']}" if r.get("message") else ""))
        if r.get("hub_changed"):
            self.say("Cette fenetre a change : je la rouvre.")
            self.stop_flag = True
            self.kill_bot()
            subprocess.Popen([PY, str(APP / "hub.py"), "--root", str(ROOT)], cwd=APP, creationflags=NO_WINDOW)
            self.destroy()
            return
        if r.get("restart_needed") and self.bot_thread and self.bot_thread.is_alive():
            self.say("Je redemarre le bot avec la nouvelle version.")
            self.fast_restart = True                # bot_loop le relance tout de suite (au lieu de 30 s)
            self.kill_bot()

    def on_close(self):
        self.stop_flag = True
        self.kill_bot()
        self.destroy()


if __name__ == "__main__":
    Hub().mainloop()
