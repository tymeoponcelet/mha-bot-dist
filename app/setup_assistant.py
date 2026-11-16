"""
MHA BOT - Assistant (Windows).  Interface "Plus Ultra" :)

  - bouton "Tout configurer" : installe les modules, télécharge ADB, détecte l'émulateur
  - 4 cartes d'étapes avec voyants verts/rouges
  - quatre modes : Histoire (Peacekeeping), Hero Trial (choix des persos a farmer, avec miniatures), Night Ops (multi-run)
    et Supply (choix du mode food/money/gear/card/crystals + niveau, multi-run)
  - lancement du bot avec compteur de runs / de persos, barre de progression et chrono en direct

Réglages enregistrés dans config.json (à côté de ce fichier).
Aucune dépendance : uniquement tkinter (fourni avec Python).
"""
import os  # noqa: E402
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # memoire : sinon numpy reserve ~370 Mo par processus (16/11/2026 : « Insufficient memory » chez un ami)
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import enchainer as ec          # plan d'enchainement (syntaxe des etapes)
import verif                   # verification de l'installation (lecture seule)
import stats                   # historique des farms (logs/runs.jsonl)
import planificateur as pl     # farms programmes (planning.json + Planificateur de taches Windows)
import profils                 # profils de configuration (profils.json)
import encours                 # farm en cours, partage avec Discord et le planning
import refaire                 # refaire une image de bouton depuis une capture
import sauvegarde              # sauvegardes des reglages (logs/sauvegardes)
import instances              # 2 comptes : instances MuMu
import heroes as hz            # liste / noms des persos (templates_chars/) ; n'a pas besoin d'opencv ici

BASE = Path(__file__).resolve().parent
CHAR_TPL = BASE / "templates_chars"      # lignes des persos dans la liste de choix (une image par perso)
CONFIG = BASE / "config.json"
TEMPLATES = BASE / "templates"
BOT = BASE / "auto_story_mha.py"
PT_DIR = BASE / "platform-tools"
PT_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
REQUIRED = ["main.png", "deploy.png", "pathfinding.png", "continue.png", "close.png"]
HERO_BOT = BASE / "auto_hero_trial.py"
HERO_TPL = BASE / "templates_hero"
HERO_REQUIRED = ["ua.png", "solo.png", "herotrial.png", "plus.png", "attempt.png",
                 "challenge.png", "auto.png", "again.png", "insufficient.png", "exit.png"]
# PNG de templates_hero/ qui ne sont pas des cartes de persos (tout le reste = perso farmable)
# (am.png = ancien choix fixe d'All Might : remplace par templates_chars/, mais on l'exclut toujours de la liste)
HERO_NAV = {Path(n).stem for n in HERO_REQUIRED} | {"auto_on", "continue", "close", "am"}
NIGHT_BOT = BASE / "auto_nightops.py"
NIGHT_TPL = BASE / "templates_nightops"
NIGHT_REQUIRED = ["ua.png", "solo.png", "nightops.png", "challenge.png", "plus.png",
                  "multirun.png", "max.png", "start.png", "controlbattle.png", "continue.png", "challagain.png", "auto.png"]
AA_BOT = BASE / "auto_aa.py"
AA_TPL = BASE / "templates_aa"
AA_BOTS = {"deku": "Deku", "bakugo": "Bakugo", "shoto": "Shoto", "momo": "Momo", "ochaco": "Ochaco", "eijiro": "Eijiro",
           "fumikage": "Fumikage", "denki": "Denki"}
AA_CHAR_NAMES = {"am": "All Might", "endy": "Endeavor", "wheku": "WHM Deku", "whoto": "WHM Shoto"}
AA_REQUIRED = ["coop.png", "aa.png", "jointeam.png", "createteam.png", "insertpermit.png", "permit.png", "bots.png", "add.png",
               "closebots.png", "deploy.png", "cardflip.png", "cardflip2.png", "return.png", "recruit.png", "mypermit.png",
               "resetpermit.png", "confirmreset.png"]
WHM_BOT = BASE / "auto_whm.py"
WHM_TPL = BASE / "templates_whm"
WHM_REQUIRED = ["whmevent.png", "plusieursfois.png", "whmchallenge.png"]   # + banniere et carte de l'evenement choisi
WHM_EVENTS = {"entrance": ("Entrance", "entrance.png", "whmlvl"), "hallway": ("Hallway", "hallway.png", "hallwaylvl"),
              "library": ("Library", "library.png", "librarylvl")}
SUPPLY_BOT = BASE / "auto_supply.py"
SUPPLY_TPL = BASE / "templates_supply"
SUPPLY_MODES = {"food": "Cafeteria", "money": "Bounty", "gear": "Data Collection", "card": "Reinforcement", "crystals": "Recovery"}
SUPPLY_MAX_LEVEL = {"food": 4, "money": 4, "gear": 2, "card": 4, "crystals": 4}
# images communes reprises de templates_nightops/ si elles ne sont pas dans templates_supply/
SUPPLY_COMMON = ["ua.png", "solo.png", "plus.png", "multirun.png", "max.png", "start.png",
                 "continue.png", "challagain.png", "controlbattle.png", "auto.png"]
CHAIN_BOT = BASE / "enchainer.py"
HERO_ORDER = ["ochaco", "iida", "tsuyu", "deku", "denki", "eijiro", "mineta"]
PORTS = ["127.0.0.1:16384", "127.0.0.1:7555", "127.0.0.1:5555", "127.0.0.1:62001"]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def console_python():
    """python.exe meme si l'assistant tourne sous pythonw.exe (farms : console cachee, adb sans fenetre)."""
    return str(Path(sys.executable).with_name("python.exe")) if Path(sys.executable).name.lower() == "pythonw.exe" else sys.executable

# --- palette ---
BG, CARD, BORDER = "#0e1320", "#171f33", "#2a3552"
TEXT, MUTED = "#eef2ff", "#8b97b8"
YELLOW, GREEN, RED, BLUE, GREY = "#ffd400", "#2ee59d", "#ff5470", "#3aa0ff", "#3a4560"
DARK = "#0e1320"

FRIENDLY = {
    "quest": "📜 Quête lancée",
    "deploy": "🚀 Deploy !",
    "pathfinding": "🧭 Pathfinding en cours",
    "continue": "👆 Tap to continue",
    "close": "❌ Fermeture des récompenses",
    "ua": "🏫 Menu UA", "solo": "🧍 Solo", "nightops": "🌙 Night Ops", "challenge": "⚔ Challenge",
    "plus": "➕ Ajout du perso", "am": "🦸 Choix du perso (Deploy)", "multirun": "🔁 Multi-run",
    "level": "🎚 Choix du niveau", "confirm": "✅ Confirmation du niveau", "continuechall": "⏭ Continue Challenge", "modecard": "🃏 Choix du mode", "supply": "📦 Supply",
    "whmevent": "🏛️ Événement", "entrance": "🚪 Entrée de l'événement", "entrance2": "👆 Sélection de l'événement", "card": "🎟 Choix de l'entrée",
    "plusieursfois": "🔁 Consecutive Challenges", "exit": "🚪 Exit Stage",
    "code": "🔋 Code stamina", "max": "⏫ Max", "challagain": "🔁 Challenge Again", "start": "▶ Start", "controlbattle": "🎮 Controlled Battle", "auto": "🤖 Activation AUTO",
}


def hero_chars():
    """Persos disponibles = cartes PNG de templates_hero/ (deposer un PNG = ajouter un perso)."""
    names = [p.stem for p in HERO_TPL.glob("*.png") if p.stem not in HERO_NAV]
    return sorted(names, key=lambda n: (HERO_ORDER.index(n) if n in HERO_ORDER else len(HERO_ORDER), n))


def F(size, bold=False):
    return ("Segoe UI", size, "bold" if bold else "normal")


def blend(hex_color, amount=0.18):
    c = hex_color.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (int(v + (255 - v) * amount) for v in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def load_cfg():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cfg(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


class Btn(tk.Label):
    """Bouton plat coloré avec effet de survol."""

    def __init__(self, parent, text, command, bg=BLUE, fg=DARK, size=10, padx=14, pady=6):
        super().__init__(parent, text=text, bg=bg, fg=fg, font=F(size, True),
                         padx=padx, pady=pady, cursor="hand2")
        self.base, self.command, self.enabled = bg, command, True
        self.bind("<Enter>", lambda e: self.enabled and self.config(bg=blend(self.base)))
        self.bind("<Leave>", lambda e: self.config(bg=self.base))
        self.bind("<Button-1>", lambda e: self.command() if self.enabled else None)

    def style(self, bg, fg=None, text=None, enabled=True):
        self.base, self.enabled = bg, enabled
        self.config(bg=bg, cursor="hand2" if enabled else "arrow")
        if fg:
            self.config(fg=fg)
        if text:
            self.config(text=text)


class StepEditor(tk.Frame):
    """Editeur d'UNE etape de farm avec des menus (tous les modes) : planning et enchainement.
    get() -> etape validee (dict d'enchainer) ou ValueError avec un message clair."""
    NONE_HERO = "(celui déjà placé)"

    def __init__(self, parent, bg=CARD):
        super().__init__(parent, bg=bg)
        self.bg = bg
        self.keys = list(ec.MODES_UI)
        top = tk.Frame(self, bg=bg)
        top.pack(fill="x")
        tk.Label(top, text="Mode", bg=bg, fg=MUTED, font=F(9, True)).pack(side="left")
        self.mode = ttk.Combobox(top, width=24, state="readonly", values=[ec.MODES_UI[k][0] for k in self.keys])
        self.mode.pack(side="left", padx=(6, 0))
        self.mode.bind("<<ComboboxSelected>>", lambda e: self.build())
        self.opts = tk.Frame(self, bg=bg)
        self.opts.pack(fill="x", pady=(6, 0))
        self.heroes = hz.available(CHAR_TPL)
        self.cards = hero_chars()
        self.w = {}
        self.mode.current(0)
        self.build()

    def key(self):
        return self.keys[self.mode.current()]

    def _combo(self, row, label, values, width=10, current=0):
        tk.Label(row, text=label, bg=self.bg, fg=MUTED, font=F(9, True)).pack(side="left", padx=(0, 4))
        c = ttk.Combobox(row, width=width, state="readonly", values=values)
        c.current(current)
        c.pack(side="left", padx=(0, 14))
        return c

    def _spin(self, row, label, value, maxi=500):
        tk.Label(row, text=label, bg=self.bg, fg=MUTED, font=F(9, True)).pack(side="left", padx=(0, 4))
        s = tk.Spinbox(row, from_=1, to=maxi, width=5, font=F(10), justify="center")
        s.delete(0, "end")
        s.insert(0, str(value))
        s.pack(side="left", padx=(0, 14))
        return s

    def build(self):
        for c in self.opts.winfo_children():
            c.destroy()
        self.w = {}
        k = self.key()
        base = ec.MODES_UI[k][1]
        row = tk.Frame(self.opts, bg=self.bg)
        row.pack(fill="x")
        if k == "supply":
            self.w["supply"] = self._combo(row, "Ressource", list(ec.SUPPLY_NAMES.values()), 26, 1)
            self.w["supply"].bind("<<ComboboxSelected>>", lambda e: self._levels())
            self.w["level"] = self._combo(row, "Niveau", ["1", "2", "3", "4"], 4, 3)
            self.w["runs"] = self._spin(row, "Multi-runs", base["runs"])
        elif k in ("nightops", "entrance", "hallway", "library"):
            if k != "nightops":
                self.w["level"] = self._combo(row, "Niveau", ["1", "2", "3"], 4, 2)
            self.w["runs"] = self._spin(row, "Multi-runs", base["runs"])
        elif k == "aa":
            self.w["runs"] = self._spin(row, "Combats", base["runs"])
            bots = ["(aucun)"] + list(ec.AA_BOTS.values())
            self.w["bot1"] = self._combo(row, "Bot 1", bots, 10, 1)
            self.w["bot2"] = self._combo(row, "Bot 2", bots, 10, 2)
            self.w["reset"] = self._combo(row, "Reset du permit au floor", ["jamais"] + [str(i) for i in range(2, 21)], 7, 0)
            # persos reserves pour les hauts floors : pre-coches comme dans l'assistant (mode Agency)
            cfg = load_cfg()
            pre = set(cfg.get("aa_reserved") or [])
            rrow = tk.Frame(self.opts, bg=self.bg)
            rrow.pack(fill="x", pady=(6, 0))
            floors = [str(i) for i in range(2, 31)]
            rf = str(int(cfg.get("aa_reserve_floor", 10) or 10))
            self.w["resfloor"] = self._combo(rrow, "Persos réservés à partir du floor", floors, 4,
                                             floors.index(rf) if rf in floors else floors.index("10"))
            self.w["reserve"] = {}
            row = None
            for i, c in enumerate(ec.aa_chars()):
                if i % 9 == 0:
                    row = tk.Frame(self.opts, bg=self.bg)
                    row.pack(fill="x", padx=(12, 0))
                v = tk.BooleanVar(value=c in pre)
                tk.Checkbutton(row, text=ec.aa_name(c), variable=v, bg=self.bg, fg=TEXT, selectcolor=BG,
                               activebackground=self.bg, activeforeground=TEXT, font=F(9)).pack(side="left")
                self.w["reserve"][c] = v
            # 2e palier : persos reserves pour les floors 13+ (UNIQUEMENT eux) + reset de secours
            pre2 = set(cfg.get("aa_reserved2") or []) - pre
            rrow2 = tk.Frame(self.opts, bg=self.bg)
            rrow2.pack(fill="x", pady=(6, 0))
            rf2 = str(int(cfg.get("aa_reserve2_floor", 13) or 13))
            self.w["resfloor2"] = self._combo(rrow2, "2e palier (13+) à partir du floor", floors, 4,
                                              floors.index(rf2) if rf2 in floors else floors.index("13"))
            fb = int(cfg.get("aa_reset_fallback", 0) or 0)
            fbs = ["aucun"] + [str(i) for i in range(2, 31)]
            self.w["fallback"] = self._combo(rrow2, "Reset de secours (13+ épuisés) au floor", fbs, 6,
                                             fbs.index(str(fb)) if str(fb) in fbs else 0)
            self.w["reserve2"] = {}
            row = None
            for i, c in enumerate(ec.aa_chars()):
                if i % 9 == 0:
                    row = tk.Frame(self.opts, bg=self.bg)
                    row.pack(fill="x", padx=(12, 0))
                v2 = tk.BooleanVar(value=c in pre2)
                tk.Checkbutton(row, text=ec.aa_name(c), variable=v2, bg=self.bg, fg=BLUE, selectcolor=BG,
                               activebackground=self.bg, activeforeground=BLUE, font=F(9)).pack(side="left")
                self.w["reserve2"][c] = v2
                v1 = self.w["reserve"][c]             # un perso dans UNE seule liste : cocher l'une decoche l'autre
                v1.trace_add("write", lambda *_a, a=v1, b=v2: b.set(False) if a.get() else None)
                v2.trace_add("write", lambda *_a, a=v1, b=v2: a.set(False) if b.get() else None)
        if k == "hero":
            tk.Label(row, text="Persos à farmer", bg=self.bg, fg=MUTED, font=F(9, True)).pack(side="left", padx=(0, 6))
            self.w["chars"] = {}
            for i, c in enumerate(self.cards):
                if i and i % 8 == 0:
                    row = tk.Frame(self.opts, bg=self.bg)
                    row.pack(fill="x", padx=(98, 0))
                v = tk.BooleanVar(value=True)
                tk.Checkbutton(row, text=c.capitalize(), variable=v, bg=self.bg, fg=TEXT, selectcolor=BG,
                               activebackground=self.bg, activeforeground=TEXT, font=F(9)).pack(side="left")
                self.w["chars"][c] = v
            row = tk.Frame(self.opts, bg=self.bg)
            row.pack(fill="x", pady=(4, 0))
        if k != "aa":
            names = [self.NONE_HERO] + [hz.pretty(h) for h in self.heroes]
            self.w["hero"] = self._combo(row, "Perso qui combat", names, 16, 0)

    def _levels(self):
        sup = list(ec.SUPPLY_NAMES)[self.w["supply"].current()]
        mx = ec.MAX_LEVEL[sup]
        self.w["level"].config(values=[str(i) for i in range(1, mx + 1)])
        if self.w["level"].current() >= mx or self.w["level"].current() < 0:
            self.w["level"].current(mx - 1)

    def get(self):
        k = self.key()
        st = dict(ec.MODES_UI[k][1])
        try:
            if "runs" in self.w:
                st["runs"] = int(self.w["runs"].get())
            if k == "supply":
                st["supply"] = list(ec.SUPPLY_NAMES)[self.w["supply"].current()]
            if "level" in self.w:
                st["level"] = int(self.w["level"].get())
        except ValueError:
            raise ValueError("Nombre invalide (multi-runs / combats).") from None
        if k == "hero":
            chars = [c for c, v in self.w["chars"].items() if v.get()]
            if not chars:
                raise ValueError("Hero Trial : coche au moins un perso à farmer.")
            st["chars"] = ",".join(chars)
        if k == "aa":
            keys = list(ec.AA_BOTS)
            bots = [keys[c.current() - 1] for c in (self.w["bot1"], self.w["bot2"]) if c.current() > 0]
            if len(set(bots)) < len(bots):
                raise ValueError("Agency : choisis deux bots différents.")
            st["bots"] = ",".join(bots) or None
            st["reset"] = None if self.w["reset"].current() == 0 else int(self.w["reset"].get())
            st["reserve"] = ",".join(c for c, v in self.w["reserve"].items() if v.get()) or "aucun"
            st["resfloor"] = int(self.w["resfloor"].get())
            r2 = ",".join(c for c, v in self.w["reserve2"].items() if v.get())
            st["reserve2"] = r2 or None
            st["resfloor2"] = int(self.w["resfloor2"].get()) if r2 else None
            st["fallback"] = (None if self.w["fallback"].current() == 0 else int(self.w["fallback"].get())) if r2 else None
        elif self.w["hero"].current() > 0:
            st["hero"] = self.heroes[self.w["hero"].current() - 1]
        try:
            return ec.validate(st)
        except SystemExit as e:
            raise ValueError(str(e)) from None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MHA BOT - Plus Ultra !")
        self.configure(bg=BG)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(900, sw - 40)}x{min(1040, sh - 90)}+{max(0, (sw - 900) // 2)}+10")
        self.minsize(min(860, sw - 40), 480)
        self.cfg = load_cfg()
        self.cards = {}
        self.ok = {"deps": False, "adb": False, "files": False, "device": False}
        self.proc = None
        self.t0 = 0.0
        self.total = 1
        self.done = 0
        self.run_vals = {"story": int(self.cfg.get("max_runs", 1) or 1),
                         "night": int(self.cfg.get("nightops_runs", 1) or 1),
                         "supply": int(self.cfg.get("supply_runs", 1) or 1),
                         "whm": int(self.cfg.get("whm_runs", 1) or 1), "aa": int(self.cfg.get("aa_runs", 10) or 10), "chain": 1}
        self.aa_bots = [b for b in (self.cfg.get("aa_bots") or ["deku", "bakugo"]) if b in AA_BOTS][:2]
        self.aa_reset = int(self.cfg.get("aa_reset_floor", 0) or 0)
        self.aa_floor = int(self.cfg.get("aa_floor", 0) or 0)
        self.aa_bot_btns = {}
        self.aa_res_btns = {}
        self.aa_chars = sorted(f.stem.lower() for f in (BASE / "templates_aa" / "chars").glob("*.png")) \
            if (BASE / "templates_aa" / "chars").is_dir() else []
        self.aa_reserved = [n for n in (self.cfg.get("aa_reserved") or []) if n in self.aa_chars]
        self.aa_resfloor = int(self.cfg.get("aa_reserve_floor", 10) or 10)
        self.aa_reserved2 = [n for n in (self.cfg.get("aa_reserved2") or []) if n in self.aa_chars and n not in self.aa_reserved]
        self.aa_resfloor2 = int(self.cfg.get("aa_reserve2_floor", 13) or 13)
        self.aa_fallback = int(self.cfg.get("aa_reset_fallback", 0) or 0)
        self.aa_tier = 1                                 # liste editee par les boutons des persos : 1 = 10+, 2 = 13+
        self.whm_level = int(self.cfg.get("whm_level", 3) or 3)
        self.whm_lvl_btns = {}
        self.whm_event = self.cfg.get("whm_event") if self.cfg.get("whm_event") in WHM_EVENTS else "entrance"
        self.whm_ev_btns = {}
        self.sup_mode = self.cfg.get("supply_mode") if self.cfg.get("supply_mode") in SUPPLY_MODES else "food"
        self.sup_level = int(self.cfg.get("supply_level", 4) or 4)
        self.sup_chips, self.sup_lvl_btns = {}, {}
        self.mode = self.cfg.get("mode", "story") if self.cfg.get("mode") in ("story", "hero", "night", "supply", "whm", "aa", "chain") else "story"
        self.chain_type = self.cfg.get("chain_type") if self.cfg.get("chain_type") in ("supply", "night", "hero") else "supply"
        self.chain_steps = []
        for st in self.cfg.get("chain_steps", []):                 # etapes enregistrees (format de enchainer.py)
            try:
                self.chain_steps.append(ec.validate(dict(st)))
            except (SystemExit, Exception):
                pass
        self.chain_idx, self.chain_n, self.chain_fail, self.chain_recap, self.step_mode = 0, 0, 0, False, None
        self.runs = self.run_vals.get(self.mode, self.run_vals["story"])
        self.chars = hero_chars()
        saved = self.cfg.get("hero_chars")
        self.sel = set(c for c in saved if c in self.chars) if saved else set(self.chars)
        self.chips, self.thumbs = {}, {}
        self.pick_names = hz.available(CHAR_TPL)                 # persos de la liste de choix (templates_chars/)
        saved_pick = self.cfg.get("pick_hero")
        self.pick = saved_pick if saved_pick in self.pick_names else (
            hz.DEFAULT if hz.DEFAULT in self.pick_names else (self.pick_names[0] if self.pick_names else hz.DEFAULT))
        self.pick_chips = {}
        self.hero_done = set()
        self.setup_style()
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.ext = None                                          # farm lance AILLEURS (Discord, planning...) en cours
        self.refresh_all()
        self.after(600, self.run_verif)                          # verification automatique a l'ouverture
        self.after(1000, self.watch_ext)                         # farm en cours lance ailleurs : affiche + Stop
        threading.Thread(target=sauvegarde.daily, daemon=True).start()   # sauvegarde quotidienne des reglages
        self.after(800, self.refresh_instances)                  # 2 comptes : instances MuMu demarrees

    # ================= interface =================
    def setup_style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        for name, color in (("Y", YELLOW), ("G", GREEN)):
            st.configure(f"{name}.Horizontal.TProgressbar", troughcolor="#0a0e18", background=color,
                         bordercolor=BG, lightcolor=color, darkcolor=color, thickness=14)
        st.configure("TCombobox", fieldbackground="#0a0e18", background=CARD, foreground=TEXT,
                     arrowcolor=YELLOW, bordercolor=BORDER, selectbackground="#0a0e18",
                     selectforeground=TEXT)
        st.map("TCombobox", fieldbackground=[("readonly", "#0a0e18")],
               foreground=[("readonly", TEXT)])

    def build_ui(self):
        # --- barre de lancement FIXE en bas (toujours visible, quelle que soit la hauteur de la fenêtre) ---
        self.bottom = tk.Frame(self, bg=CARD, highlightthickness=2, highlightbackground=YELLOW)
        self.bottom.pack(side="bottom", fill="x", padx=16, pady=(4, 10))

        # --- tout le reste dans une zone qui DEFILE (molette / barre de droite) ---
        outer = tk.Frame(self, bg=BG)
        outer.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sbar = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sbar.set)
        sbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=BG)
        win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))
        self.bind_all("<MouseWheel>", self.on_wheel)
        self.bind_all("<Button-4>", self.on_wheel)
        self.bind_all("<Button-5>", self.on_wheel)

        # --- en-tête ---
        head = tk.Frame(self.body, bg=BG)
        head.pack(fill="x", padx=18, pady=(14, 4))
        tk.Label(head, text="MHA BOT", bg=BG, fg=YELLOW, font=("Segoe UI Black", 30)).pack(side="left")
        tk.Label(head, text="  ⚡ PLUS ULTRA !", bg=BG, fg=GREEN, font=F(15, True)).pack(side="left", pady=(12, 0))
        import version
        tk.Label(head, text="  " + version.label(), bg=BG, fg=MUTED, font=F(8)).pack(side="left", pady=(18, 0))
        self.auto_btn = Btn(head, "⚡ Tout configurer automatiquement", self.auto_setup,
                            bg=BLUE, size=10, padx=14, pady=8)
        self.auto_btn.pack(side="right", pady=(6, 0))
        tools = tk.Frame(self.body, bg=BG)                        # outils : sur leur propre ligne (fenetre etroite)
        tools.pack(fill="x", padx=18, pady=(0, 6))
        for txt, fn in (("🗓️ Planning", self.open_planning), ("📊 Stats", self.open_stats), ("👤 Profils", self.open_profiles),
                        ("🖼️ Images", self.open_refaire), ("💾 Sauvegardes", self.open_backups)):
            Btn(tools, txt, fn, bg=GREY, fg=TEXT, size=10, padx=12, pady=5).pack(side="left", padx=(0, 8))

        prog = tk.Frame(self.body, bg=BG)
        prog.pack(fill="x", padx=18, pady=(0, 6))
        self.setup_lbl = tk.Label(prog, text="Configuration : 0/4", bg=BG, fg=MUTED, font=F(9, True))
        self.setup_lbl.pack(anchor="w")
        self.setup_bar = ttk.Progressbar(prog, style="G.Horizontal.TProgressbar", maximum=4)
        self.setup_bar.pack(fill="x", pady=(2, 0))

        # --- cartes d'étapes ---
        c = self.card("deps", 1, "Modules Python", "opencv-python et numpy (1 à 2 minutes, internet requis).")
        Btn(c["btns"], "📦 Installer", lambda: self.run_bg(self.do_install_deps), bg=YELLOW).pack(fill="x")

        c = self.card("adb", 2, "ADB",
                      "L'outil qui permet au bot de cliquer dans l'émulateur. Il s'installe sur ton PC "
                      "(pas dans l'émulateur).")
        Btn(c["btns"], "⬇ Télécharger", lambda: self.run_bg(self.do_download_adb), bg=YELLOW).pack(fill="x")
        Btn(c["btns"], "J'ai déjà adb.exe", self.pick_adb, bg=GREY, fg=TEXT, size=9, pady=3).pack(fill="x", pady=(4, 0))

        c = self.card("files", 3, "Fichiers du bot",
                      "Script + images de boutons du mode choisi (templates/ pour Histoire, "
                      "templates_hero/ pour Hero Trial).")
        Btn(c["btns"], "📂 Ouvrir templates", self.open_templates, bg=YELLOW).pack(fill="x")
        Btn(c["btns"], "🔄 Actualiser", self.refresh_all, bg=GREY, fg=TEXT, size=9, pady=3).pack(fill="x", pady=(4, 0))

        c = self.card("device", 4, "Émulateur",
                      "Lance ton émulateur avec le jeu ouvert, puis clique sur Détecter.")
        r1 = tk.Frame(c["btns"], bg=CARD)
        r1.pack(fill="x")
        Btn(r1, "🔍 Détecter", lambda: self.run_bg(self.do_detect), bg=YELLOW, padx=10).pack(side="left")
        Btn(r1, "📸", self.capture, bg=GREY, fg=TEXT, padx=10).pack(side="left", padx=(4, 0))
        r2 = tk.Frame(c["btns"], bg=CARD)
        r2.pack(fill="x", pady=(4, 0))
        self.combo = ttk.Combobox(r2, width=18, state="readonly")
        self.combo.pack(side="left")
        Btn(r2, "✔", self.use_device, bg=GREEN, padx=8, pady=2).pack(side="left", padx=(4, 0))

        # --- verification de l'installation (lecture seule, ne bloque rien) ---
        vf = tk.Frame(self.body, bg=CARD, highlightthickness=2, highlightbackground=BORDER)
        vf.pack(fill="x", padx=16, pady=4)
        vtop = tk.Frame(vf, bg=CARD)
        vtop.pack(fill="x", padx=12, pady=(8, 2))
        tk.Label(vtop, text="🩺 Vérification", bg=CARD, fg=TEXT, font=F(12, True)).pack(side="left")
        self.verif_when = tk.Label(vtop, text="", bg=CARD, fg=MUTED, font=F(8))
        self.verif_when.pack(side="left", padx=(10, 0))
        Btn(vtop, "🔄 Vérifier", self.run_verif, bg=GREY, fg=TEXT, size=9, padx=10, pady=3).pack(side="right")
        self.verif_fix = Btn(vtop, "", self.use_alt_device, bg=GREEN, size=9, padx=10, pady=3)   # emulateur qui a change de nom
        self.verif_alt = None
        self.verif_lbls = {}
        for key in ("device", "resolution", "game", "foreground", "discord"):
            lb = tk.Label(vf, text="…", bg=CARD, fg=MUTED, font=F(9), anchor="w", justify="left", wraplength=780)
            lb.pack(fill="x", padx=14)
            self.verif_lbls[key] = lb
        tk.Frame(vf, bg=CARD, height=6).pack()

        # --- panneau de lancement ---
        pan = tk.Frame(self.body, bg=CARD, highlightthickness=2, highlightbackground=YELLOW)
        pan.pack(fill="x", padx=16, pady=(10, 4))

        # sélecteur de mode
        self.mode_row = tk.Frame(pan, bg=CARD)
        self.mode_row.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(self.mode_row, text="Mode", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left")
        self.mode_btns = {}
        for key, label in (("story", "📜  Histoire"), ("hero", "🏆  Hero Trial"), ("night", "🌙  Night Ops"),
                           ("supply", "📦  Supply"), ("whm", "🏛️  Événement"), ("aa", "🏢  Agency"), ("chain", "🔗  Enchaîner")):
            b = Btn(self.mode_row, label, lambda k=key: self.set_mode(k), bg=GREY, fg=TEXT, size=10, padx=10, pady=6)
            b.pack(side="left", padx=(8, 0))
            self.mode_btns[key] = b

        # --- options mode Histoire : nombre de runs ---
        self.story_opts = tk.Frame(pan, bg=CARD)
        row = tk.Frame(self.story_opts, bg=CARD)
        row.pack(fill="x", padx=14, pady=(8, 6))
        self.runs_title = tk.Label(row, text="Nombre de runs", bg=CARD, fg=TEXT, font=F(11, True))
        self.runs_title.pack(side="left")
        Btn(row, "−", lambda: self.change_runs(-1), bg=GREY, fg=TEXT, size=12, padx=10, pady=0).pack(side="left", padx=(12, 4))
        self.runs_lbl = tk.Label(row, text=str(self.runs), bg=CARD, fg=YELLOW, font=("Segoe UI Black", 20), width=3)
        self.runs_lbl.pack(side="left")
        Btn(row, "+", lambda: self.change_runs(+1), bg=GREY, fg=TEXT, size=12, padx=10, pady=0).pack(side="left", padx=(4, 12))
        self.runs_desc = tk.Label(row, text="", bg=CARD, fg=MUTED, font=F(9))
        self.runs_desc.pack(side="left")

        # --- options mode Supply : choix du mode + niveau ---
        self.supply_opts = tk.Frame(pan, bg=CARD)
        top = tk.Frame(self.supply_opts, bg=CARD)
        top.pack(fill="x", padx=14, pady=(8, 4))
        tk.Label(top, text="Mode à farmer", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left")
        self.sup_lbl = tk.Label(top, text="", bg=CARD, fg=YELLOW, font=F(10, True))
        self.sup_lbl.pack(side="left", padx=(10, 0))
        grid = tk.Frame(self.supply_opts, bg=CARD)
        grid.pack(fill="x", padx=14, pady=(0, 4))
        for i, name in enumerate(SUPPLY_MODES):
            self.build_supply_chip(grid, name, i)
        lrow = tk.Frame(self.supply_opts, bg=CARD)
        lrow.pack(fill="x", padx=14, pady=(2, 4))
        tk.Label(lrow, text="Niveau", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left")
        for lv, roman in enumerate(["I", "II", "III", "IV"], start=1):
            b = Btn(lrow, roman, lambda n=lv: self.set_sup_level(n), bg=GREY, fg=TEXT, size=10, padx=14, pady=4)
            b.pack(side="left", padx=(8, 0))
            self.sup_lvl_btns[lv] = b
        tk.Label(lrow, text="(Data Collection : niveaux I et II seulement)", bg=CARD, fg=MUTED, font=F(8)).pack(side="left", padx=(12, 0))

        # --- options mode Événement (WHM) : choix de l'entrée ---
        self.whm_opts = tk.Frame(pan, bg=CARD)
        wtop = tk.Frame(self.whm_opts, bg=CARD)
        wtop.pack(fill="x", padx=14, pady=(8, 4))
        tk.Label(wtop, text="Événements WHM (Entrance / Hallway / Library)", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left")
        self.whm_lbl = tk.Label(wtop, text="", bg=CARD, fg=YELLOW, font=F(10, True))
        self.whm_lbl.pack(side="left", padx=(10, 0))
        erow = tk.Frame(self.whm_opts, bg=CARD)
        erow.pack(fill="x", padx=14, pady=(0, 4))
        tk.Label(erow, text="Lequel", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left")
        for ev, (nm, _b, _c) in WHM_EVENTS.items():
            b = Btn(erow, nm, lambda e=ev: self.set_whm_event(e), bg=GREY, fg=TEXT, size=10, padx=12, pady=4)
            b.pack(side="left", padx=(8, 0))
            self.whm_ev_btns[ev] = b
        wrow = tk.Frame(self.whm_opts, bg=CARD)
        wrow.pack(fill="x", padx=14, pady=(0, 4))
        tk.Label(wrow, text="Niveau", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left")
        for lv in (1, 2, 3):
            b = Btn(wrow, f"Niveau {lv}", lambda n=lv: self.set_whm_level(n), bg=GREY, fg=TEXT, size=10, padx=12, pady=4)
            b.pack(side="left", padx=(8, 0))
            self.whm_lvl_btns[lv] = b
        tk.Label(self.whm_opts, text="Le bot coche « Consecutive Challenges » (le maximum de runs d'un coup), active AUTO, "
                 "puis enchaîne avec « Continue Challenge ». Images : templates_whm/.",
                 bg=CARD, fg=MUTED, font=F(8), anchor="w", justify="left", wraplength=800).pack(fill="x", padx=14, pady=(0, 6))

        # --- options mode Agency Assessment : bots, reset du permit, floor actuel ---
        self.aa_opts = tk.Frame(pan, bg=CARD)
        atop = tk.Frame(self.aa_opts, bg=CARD)
        atop.pack(fill="x", padx=14, pady=(8, 4))
        tk.Label(atop, text="Agency Assessment", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left")
        self.aa_lbl = tk.Label(atop, text="", bg=CARD, fg=YELLOW, font=F(10, True))
        self.aa_lbl.pack(side="left", padx=(10, 0))
        brow = tk.Frame(self.aa_opts, bg=CARD)
        brow.pack(fill="x", padx=14, pady=(0, 4))
        tk.Label(brow, text="Bots (2 max)", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left")
        for b, nm in AA_BOTS.items():
            bt = Btn(brow, nm, lambda n=b: self.toggle_aa_bot(n), bg=GREY, fg=TEXT, size=9, padx=8, pady=4)
            bt.pack(side="left", padx=(6, 0))
            self.aa_bot_btns[b] = bt
        rrow2 = tk.Frame(self.aa_opts, bg=CARD)
        rrow2.pack(fill="x", padx=14, pady=(0, 4))
        tk.Label(rrow2, text="Reset du permit à partir du floor", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left")
        Btn(rrow2, "−", lambda: self.step_aa("aa_reset", -1), bg=GREY, fg=TEXT, size=11, padx=10, pady=2).pack(side="left", padx=(8, 0))
        self.aa_reset_lbl = tk.Label(rrow2, text="", bg=CARD, fg=YELLOW, font=F(12, True), width=7)
        self.aa_reset_lbl.pack(side="left")
        Btn(rrow2, "+", lambda: self.step_aa("aa_reset", 1), bg=GREY, fg=TEXT, size=11, padx=10, pady=2).pack(side="left")
        tk.Label(rrow2, text="Floor actuel", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left", padx=(24, 0))
        Btn(rrow2, "−", lambda: self.step_aa("aa_floor", -1), bg=GREY, fg=TEXT, size=11, padx=10, pady=2).pack(side="left", padx=(8, 0))
        self.aa_floor_lbl = tk.Label(rrow2, text="", bg=CARD, fg=YELLOW, font=F(12, True), width=7)
        self.aa_floor_lbl.pack(side="left")
        Btn(rrow2, "+", lambda: self.step_aa("aa_floor", 1), bg=GREY, fg=TEXT, size=11, padx=10, pady=2).pack(side="left")
        rrow3 = tk.Frame(self.aa_opts, bg=CARD)
        rrow3.pack(fill="x", padx=14, pady=(0, 4))
        tk.Label(rrow3, text="Persos réservés à partir du floor", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left")
        Btn(rrow3, "−", lambda: self.step_aa("aa_resfloor", -1), bg=GREY, fg=TEXT, size=11, padx=10, pady=2).pack(side="left", padx=(8, 0))
        self.aa_resfloor_lbl = tk.Label(rrow3, text="", bg=CARD, fg=YELLOW, font=F(12, True), width=4)
        self.aa_resfloor_lbl.pack(side="left")
        Btn(rrow3, "+", lambda: self.step_aa("aa_resfloor", 1), bg=GREY, fg=TEXT, size=11, padx=10, pady=2).pack(side="left")
        tk.Label(rrow3, text="2e palier (13+) à partir du floor", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left", padx=(24, 0))
        Btn(rrow3, "−", lambda: self.step_aa("aa_resfloor2", -1), bg=GREY, fg=TEXT, size=11, padx=10, pady=2).pack(side="left", padx=(8, 0))
        self.aa_resfloor2_lbl = tk.Label(rrow3, text="", bg=CARD, fg=BLUE, font=F(12, True), width=4)
        self.aa_resfloor2_lbl.pack(side="left")
        Btn(rrow3, "+", lambda: self.step_aa("aa_resfloor2", 1), bg=GREY, fg=TEXT, size=11, padx=10, pady=2).pack(side="left")
        rrow4 = tk.Frame(self.aa_opts, bg=CARD)
        rrow4.pack(fill="x", padx=14, pady=(0, 4))
        tk.Label(rrow4, text="Liste éditée", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left")
        self.aa_tier_btns = {}
        for t in (1, 2):
            bt = Btn(rrow4, "", lambda t=t: self.set_aa_tier(t), bg=GREY, fg=TEXT, size=9, padx=8, pady=3)
            bt.pack(side="left", padx=(6, 0))
            self.aa_tier_btns[t] = bt
        tk.Label(rrow4, text="Reset de secours (13+ épuisés) au floor", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left", padx=(24, 0))
        Btn(rrow4, "−", lambda: self.step_aa("aa_fallback", -1), bg=GREY, fg=TEXT, size=11, padx=10, pady=2).pack(side="left", padx=(8, 0))
        self.aa_fallback_lbl = tk.Label(rrow4, text="", bg=CARD, fg=BLUE, font=F(12, True), width=7)
        self.aa_fallback_lbl.pack(side="left")
        Btn(rrow4, "+", lambda: self.step_aa("aa_fallback", 1), bg=GREY, fg=TEXT, size=11, padx=10, pady=2).pack(side="left")
        self.aa_res_lbl = tk.Label(self.aa_opts, text="", bg=CARD, fg=YELLOW, font=F(9, True), anchor="w")
        self.aa_res_lbl.pack(fill="x", padx=14, pady=(0, 3))
        crow = None
        for i, n in enumerate(self.aa_chars):
            if i % 12 == 0:
                crow = tk.Frame(self.aa_opts, bg=CARD)
                crow.pack(fill="x", padx=14, pady=(0, 3))
            bt = Btn(crow, AA_CHAR_NAMES.get(n, n.capitalize()), lambda n=n: self.toggle_aa_res(n), bg=GREY, fg=TEXT, size=8, padx=6, pady=3)
            bt.pack(side="left", padx=(0, 4))
            self.aa_res_btns[n] = bt
        if not self.aa_chars:
            tk.Label(rrow3, text="aucune icône dans templates_aa/chars/ (une image par perso : nom.png)", bg=CARD, fg=MUTED,
                     font=F(8)).pack(side="left", padx=(10, 0))
        tk.Label(self.aa_opts, text="Le bot insère le permit, lit le floor, ajoute les 2 bots, lance Deploy, met AUTO, retourne les cartes puis "
                 "Return. « Reset à partir du floor N » : si le permit est au floor N ou plus haut, il le remet au floor 1 avant de choisir le perso (jamais = pas de reset). "
                 "« Floor actuel » : à donner seulement si le bot n'arrive pas à le lire (auto = il le lit). "
                 "Persos réservés : jaunes = floors 10+ (jusqu'au 2e palier), bleus = floors 13+ (UNIQUEMENT eux à partir du 2e palier) ; "
                 "« Liste éditée » choisit la couleur donnée en cliquant un perso. Reset de secours : si plus aucun perso bleu n'a de "
                 "stamina, le bot reset et ne monte plus au-delà de ce floor (aucun = il s'arrête et t'alerte). Images : templates_aa/.",
                 bg=CARD, fg=MUTED, font=F(8), anchor="w", justify="left", wraplength=800).pack(fill="x", padx=14, pady=(0, 6))

        # --- options mode Hero Trial : choix des persos ---
        self.hero_opts = tk.Frame(pan, bg=CARD)
        top = tk.Frame(self.hero_opts, bg=CARD)
        top.pack(fill="x", padx=14, pady=(8, 4))
        tk.Label(top, text="Persos à farmer", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left")
        self.sel_lbl = tk.Label(top, text="", bg=CARD, fg=YELLOW, font=F(10, True))
        self.sel_lbl.pack(side="left", padx=(10, 0))
        Btn(top, "Aucun", lambda: self.select_all(False), bg=GREY, fg=TEXT, size=9, padx=8, pady=2).pack(side="right")
        Btn(top, "Tous", lambda: self.select_all(True), bg=GREY, fg=TEXT, size=9, padx=8, pady=2).pack(side="right", padx=(0, 4))
        grid = tk.Frame(self.hero_opts, bg=CARD)
        grid.pack(fill="x", padx=14, pady=(0, 4))
        for i, name in enumerate(self.chars):
            self.build_chip(grid, name, i)
        if not self.chars:
            tk.Label(grid, text="Aucune carte trouvée dans templates_hero/", bg=CARD, fg=RED, font=F(9)).pack()
        tk.Label(self.hero_opts, text="Clique sur une carte pour l'activer / la désactiver. 2 essais par perso et par jour. "
                 "Pour ajouter un perso : dépose sa carte (PNG) dans templates_hero/.",
                 bg=CARD, fg=MUTED, font=F(8), anchor="w", justify="left", wraplength=800).pack(fill="x", padx=14, pady=(0, 4))

        # --- choix du personnage qui combat (Hero Trial / Night Ops / Supply) ---
        self.pick_opts = tk.Frame(pan, bg=CARD)
        top = tk.Frame(self.pick_opts, bg=CARD)
        top.pack(fill="x", padx=14, pady=(8, 2))
        tk.Label(top, text="Personnage qui combat", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left")
        self.pick_lbl = tk.Label(top, text="", bg=CARD, fg=YELLOW, font=F(10, True))
        self.pick_lbl.pack(side="left", padx=(10, 0))
        grid = tk.Frame(self.pick_opts, bg=CARD)
        grid.pack(fill="x", padx=14, pady=(0, 2))
        for i, name in enumerate(self.pick_names):
            self.build_pick_chip(grid, name, i)
        if not self.pick_names:
            tk.Label(grid, text="Aucune image dans templates_chars/", bg=CARD, fg=RED, font=F(9)).pack()
        tk.Label(self.pick_opts, text="Choisi dans la liste quand le bot ajoute un perso (+). Absent de l'écran ? Le bot fait "
                 "défiler la liste vers le bas puis vers le haut. Ajouter un perso : dépose sa ligne (PNG) dans templates_chars/.",
                 bg=CARD, fg=MUTED, font=F(8), anchor="w", justify="left", wraplength=800).pack(fill="x", padx=14, pady=(0, 4))

        # --- mode Enchaîner : choix du type d'étape (en haut) + liste des étapes (en bas) ---
        self.chain_opts = tk.Frame(pan, bg=CARD)
        top = tk.Frame(self.chain_opts, bg=CARD)
        top.pack(fill="x", padx=14, pady=(8, 2))
        tk.Label(top, text="Étape à ajouter", bg=CARD, fg=TEXT, font=F(11, True)).pack(side="left")
        self.chain_btns = {}
        self.chain_editor = StepEditor(self.chain_opts)          # tous les modes : Supply ... Agency (menus)
        self.chain_editor.pack(fill="x", padx=14, pady=(4, 4))
        tk.Label(self.chain_opts, text="Règle l'étape ci-dessus (mode, niveau, lancements, perso), puis clique sur « Ajouter ». "
                 "Les étapes s'exécutent dans l'ordre ; le bot revient au menu principal entre deux étapes.",
                 bg=CARD, fg=MUTED, font=F(8), anchor="w", justify="left", wraplength=800).pack(fill="x", padx=14, pady=(0, 2))

        self.chain_list = tk.Frame(pan, bg=CARD)
        arow = tk.Frame(self.chain_list, bg=CARD)
        arow.pack(fill="x", padx=14, pady=(6, 4))
        Btn(arow, "➕  Ajouter cette étape", self.add_step, bg=GREEN, size=11, padx=16, pady=6).pack(side="left")
        Btn(arow, "🗑 Tout vider", self.clear_steps, bg=GREY, fg=TEXT, size=9, padx=10, pady=4).pack(side="right")
        self.stop_err = tk.BooleanVar(value=bool(self.cfg.get("chain_stop_on_error", False)))
        tk.Checkbutton(arow, text="S'arrêter à la 1re erreur", variable=self.stop_err, bg=CARD, fg=TEXT, selectcolor=BG,
                       activebackground=CARD, activeforeground=TEXT, font=F(9),
                       command=self.save_chain).pack(side="right", padx=(0, 14))
        tk.Label(self.chain_list, text="Plan (exécuté dans l'ordre) :", bg=CARD, fg=TEXT, font=F(11, True),
                 anchor="w").pack(fill="x", padx=14)
        self.steps_frame = tk.Frame(self.chain_list, bg=CARD)
        self.steps_frame.pack(fill="x", padx=14, pady=(2, 8))

        # --- recharge de stamina par code (Night Ops / Supply / Enchaîner) ---
        self.refill_opts = tk.Frame(pan, bg=CARD)
        self.refill_var = tk.BooleanVar(value=bool(self.cfg.get("stamina_refill", False)))
        rrow = tk.Frame(self.refill_opts, bg=CARD)
        rrow.pack(fill="x", padx=14, pady=(6, 0))
        tk.Checkbutton(rrow, text="🔋  Recharger la stamina avec le code quand il n'y en a plus", variable=self.refill_var,
                       bg=CARD, fg=TEXT, selectcolor=BG, activebackground=CARD, activeforeground=TEXT, font=F(11, True),
                       command=self.save_refill).pack(side="left")
        tk.Label(self.refill_opts, text="Quand « Insufficient Stamina » apparaît (ou si tu lances sans stamina), le bot utilise le code cadeau "
                 "15 fois (+1000 chacune, 15000 max) puis reprend. Désactivé = le bot s'arrête simplement. "
                 "Images : templates_stamina/.",
                 bg=CARD, fg=MUTED, font=F(8), anchor="w", justify="left", wraplength=800).pack(fill="x", padx=14, pady=(0, 6))

        # --- chien de garde : maintenance / jeu bloque -> relance du jeu ---
        self.wd_var = tk.BooleanVar(value=bool(self.cfg.get("watchdog", False)))
        wrow = tk.Frame(self.refill_opts, bg=CARD)
        wrow.pack(fill="x", padx=14, pady=(2, 0))
        tk.Checkbutton(wrow, text="🛡️  Chien de garde : maintenance du serveur / jeu bloqué → relancer le jeu", variable=self.wd_var,
                       bg=CARD, fg=TEXT, selectcolor=BG, activebackground=CARD, activeforeground=TEXT, font=F(11, True),
                       command=self.save_refill).pack(side="left")
        tk.Label(self.refill_opts, text="Si le jeu revient à l'écran d'accueil (maintenance : icône orange), le bot attend qu'elle redevienne verte. "
                 "Si rien ne progresse pendant 5 min (pas de Challenge Again), il ferme le jeu, le relance et reprend. "
                 "Images : templates_watchdog/.",
                 bg=CARD, fg=MUTED, font=F(8), anchor="w", justify="left", wraplength=800).pack(fill="x", padx=14, pady=(0, 6))

        row = tk.Frame(self.bottom, bg=CARD)
        row.pack(fill="x", padx=14, pady=(8, 6))
        self.launch_row = row
        self.launch_btn = Btn(row, "▶  LANCER  ·  PLUS ULTRA !", self.launch, bg=GREY, fg=TEXT,
                              size=14, padx=20, pady=10)
        self.launch_btn.pack(side="left", fill="x", expand=True)
        self.stop_btn = Btn(row, "⏹ Stop", self.stop, bg=GREY, fg=TEXT, size=12, padx=16, pady=10)
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.stop_btn.style(GREY, TEXT, enabled=False)

        info = tk.Frame(self.bottom, bg=CARD)
        info.pack(fill="x", padx=14, pady=(0, 8))
        self.action_lbl = tk.Label(info, text="Prêt quand toutes les étapes sont vertes.", bg=CARD,
                                   fg=MUTED, font=F(10, True), anchor="w")
        self.action_lbl.pack(fill="x")
        self.run_bar = ttk.Progressbar(info, style="Y.Horizontal.TProgressbar", maximum=1)
        self.run_bar.pack(fill="x", pady=(4, 2))
        self.stats_lbl = tk.Label(info, text="Run 0/0   ·   ⏱ 00:00", bg=CARD, fg=MUTED, font=F(9), anchor="w")
        self.stats_lbl.pack(fill="x")
        # --- 2 comptes : instance du farm (visible seulement si 2 instances MuMu sont demarrees) ---
        self.inst_row = tk.Frame(info, bg=CARD)
        tk.Label(self.inst_row, text="📱 Instance", bg=CARD, fg=TEXT, font=F(10, True)).pack(side="left")
        self.inst_combo = ttk.Combobox(self.inst_row, width=40, state="readonly")
        self.inst_combo.pack(side="left", padx=(8, 0))
        tk.Label(self.inst_row, text="(2 comptes : jamais le même script sur les deux)", bg=CARD, fg=MUTED,
                 font=F(8)).pack(side="left", padx=(8, 0))
        self.others_row = tk.Frame(info, bg=CARD)
        self.others_lbl = tk.Label(self.others_row, text="", bg=CARD, fg=YELLOW, font=F(9, True), anchor="w", justify="left")
        self.others_lbl.pack(side="left", fill="x", expand=True)
        self.others_stop = Btn(self.others_row, "⏹ Stop", self.stop_others, bg=RED, fg="#ffffff", size=9, padx=10, pady=2)
        self.others_stop.pack(side="right")
        self.inst_items, self.proc_device, self.others = [], None, []
        self.run_mode, self.run_chain = self.mode, list(self.chain_steps)

        # --- journal ---
        self.logbox = tk.Text(self.body, height=9, bg="#0a0e18", fg="#b8c4e6", font=("Consolas", 9),
                              relief="flat", wrap="word", state="disabled", padx=8, pady=6)
        self.logbox.pack(fill="x", padx=16, pady=(4, 14))
        self.apply_mode()

    def on_wheel(self, e):
        """Molette : fait defiler la page (sauf dans le journal, qui defile tout seul)."""
        if e.widget is getattr(self, "logbox", None):
            return
        if getattr(e, "num", 0) == 4:
            d = -1
        elif getattr(e, "num", 0) == 5:
            d = 1
        else:
            d = -1 if e.delta > 0 else 1
        self.canvas.yview_scroll(d, "units")

    def card(self, key, num, title, desc):
        f = tk.Frame(self.body, bg=CARD, highlightthickness=2, highlightbackground=BORDER)
        f.pack(fill="x", padx=16, pady=4)
        badge = tk.Label(f, text=str(num), bg=GREY, fg=TEXT, font=F(16, True), width=3)
        badge.pack(side="left", fill="y")
        mid = tk.Frame(f, bg=CARD)
        mid.pack(side="left", fill="both", expand=True, padx=12, pady=8)
        tk.Label(mid, text=title, bg=CARD, fg=TEXT, font=F(12, True), anchor="w").pack(fill="x")
        tk.Label(mid, text=desc, bg=CARD, fg=MUTED, font=F(9), anchor="w", justify="left",
                 wraplength=440).pack(fill="x")
        status = tk.Label(mid, text="", bg=CARD, font=F(9, True), anchor="w", justify="left", wraplength=440)
        status.pack(fill="x", pady=(3, 0))
        btns = tk.Frame(f, bg=CARD)
        btns.pack(side="right", padx=12, pady=8)
        self.cards[key] = {"frame": f, "badge": badge, "status": status, "num": str(num), "btns": btns}
        return self.cards[key]

    # ================= mode / persos =================
    def build_chip(self, parent, name, i):
        f = tk.Frame(parent, bg=CARD, highlightthickness=3, highlightbackground=GREY, cursor="hand2")
        f.grid(row=i // 8, column=i % 8, padx=4, pady=4)
        img = None
        try:
            img = tk.PhotoImage(file=str(HERO_TPL / f"{name}.png"))
            k = max(1, round(img.height() / 120))
            img = img.subsample(k, k)
        except Exception:
            pass
        self.thumbs[name] = img
        lbl = tk.Label(f, image=img, bg=CARD) if img else tk.Label(f, text="?", bg=CARD, fg=MUTED, width=8, height=6)
        lbl.pack()
        txt = tk.Label(f, text=name.capitalize(), bg=GREY, fg=TEXT, font=F(9, True), pady=2)
        txt.pack(fill="x")
        for w in (f, lbl, txt):
            w.bind("<Button-1>", lambda e, n=name: self.toggle_char(n))
        self.chips[name] = (f, txt)
        self.paint_chip(name)

    def paint_chip(self, name):
        f, txt = self.chips[name]
        on = name in self.sel
        f.config(highlightbackground=GREEN if on else GREY)
        txt.config(bg=GREEN if on else GREY, fg=DARK if on else TEXT, text=("✔ " if on else "") + name.capitalize())

    def toggle_char(self, name):
        if self.locked():
            return
        self.sel.symmetric_difference_update({name})
        self.paint_chip(name)
        self.save_hero_sel()
        self.sel_lbl.config(text=self.sel_text())
        self.refresh_all()

    def select_all(self, on):
        if self.locked():
            return
        self.sel = set(self.chars) if on else set()
        for n in self.chars:
            self.paint_chip(n)
        self.save_hero_sel()
        self.sel_lbl.config(text=self.sel_text())
        self.refresh_all()

    def sel_text(self):
        return f"{len(self.sel)}/{len(self.chars)} sélectionné(s)"

    def chosen(self):
        """Persos cochés, dans l'ordre d'affichage."""
        return [c for c in self.chars if c in self.sel]

    def save_refill(self):
        self.cfg["stamina_refill"] = bool(self.refill_var.get())
        self.cfg["watchdog"] = bool(self.wd_var.get())
        save_cfg(self.cfg)

    def refill_flag(self):
        return "--refill" if self.refill_var.get() else "--no-refill"

    def wd_flag(self):
        return "--watchdog" if self.wd_var.get() else "--no-watchdog"

    def save_hero_sel(self):
        self.cfg["hero_chars"] = self.chosen()
        save_cfg(self.cfg)

    def build_pick_chip(self, parent, name, i):
        """Vignette d'un perso de la liste de choix : son portrait (28 % gauche de la ligne) + son nom."""
        f = tk.Frame(parent, bg=CARD, highlightthickness=3, highlightbackground=GREY, cursor="hand2")
        f.grid(row=i // 8, column=i % 8, padx=3, pady=3)
        img = None
        try:
            full = tk.PhotoImage(file=str(CHAR_TPL / f"{name}.png"))
            w, h = full.width(), full.height()
            crop = tk.PhotoImage(width=int(w * 0.28), height=h)
            crop.tk.call(str(crop), "copy", str(full), "-from", 0, 0, int(w * 0.28), h)
            k = max(1, round(h / 56))
            img = crop.subsample(k, k)
            self.thumbs["pick_" + name] = (full, crop, img)        # garde les images en vie
        except Exception:
            pass
        lbl = tk.Label(f, image=img, bg=CARD) if img else tk.Label(f, text="?", bg=CARD, fg=MUTED, width=6, height=3)
        lbl.pack()
        txt = tk.Label(f, text=hz.pretty(name), bg=GREY, fg=TEXT, font=F(8, True), pady=1)
        txt.pack(fill="x")
        for wdg in (f, lbl, txt):
            wdg.bind("<Button-1>", lambda e, n=name: self.set_pick(n))
        self.pick_chips[name] = (f, txt)

    def paint_pick(self):
        for name, (f, txt) in self.pick_chips.items():
            on = name == self.pick
            f.config(highlightbackground=GREEN if on else GREY)
            txt.config(bg=GREEN if on else GREY, fg=DARK if on else TEXT, text=("✔ " if on else "") + hz.pretty(name))
        self.pick_lbl.config(text=hz.pretty(self.pick))

    def set_pick(self, name):
        if self.locked():
            return
        self.pick = name
        self.paint_pick()
        self.cfg["pick_hero"] = name
        save_cfg(self.cfg)
        self.refresh_all()

    def build_supply_chip(self, parent, name, i):
        f = tk.Frame(parent, bg=CARD, highlightthickness=3, highlightbackground=GREY, cursor="hand2")
        f.grid(row=0, column=i, padx=4, pady=4)
        img = None
        try:
            img = tk.PhotoImage(file=str(SUPPLY_TPL / f"{name}.png"))
            k = max(1, round(img.height() / 120))
            img = img.subsample(k, k)
        except Exception:
            pass
        self.thumbs["sup_" + name] = img
        lbl = tk.Label(f, image=img, bg=CARD) if img else tk.Label(f, text="?", bg=CARD, fg=MUTED, width=8, height=6)
        lbl.pack()
        txt = tk.Label(f, text=SUPPLY_MODES[name], bg=GREY, fg=TEXT, font=F(9, True), pady=2)
        txt.pack(fill="x")
        for w in (f, lbl, txt):
            w.bind("<Button-1>", lambda e, n=name: self.set_sup_mode(n))
        self.sup_chips[name] = (f, txt)

    def paint_supply(self):
        for name, (f, txt) in self.sup_chips.items():
            on = name == self.sup_mode
            f.config(highlightbackground=GREEN if on else GREY)
            txt.config(bg=GREEN if on else GREY, fg=DARK if on else TEXT,
                       text=("✔ " if on else "") + SUPPLY_MODES[name])
        top = SUPPLY_MAX_LEVEL[self.sup_mode]
        self.sup_level = min(max(1, self.sup_level), top)
        for lv, b in self.sup_lvl_btns.items():
            on = lv == self.sup_level
            b.style(YELLOW if on else (GREY if lv <= top else DARK), DARK if on else (TEXT if lv <= top else MUTED))
        self.sup_lbl.config(text=f"{SUPPLY_MODES[self.sup_mode]} · niveau {['I', 'II', 'III', 'IV'][self.sup_level - 1]}")

    def set_sup_mode(self, name):
        if self.locked():
            return
        self.sup_mode = name
        self.paint_supply()
        self.save_supply()
        self.refresh_all()

    def paint_aa(self):
        for b, bt in self.aa_bot_btns.items():
            on = b in self.aa_bots
            bt.style(YELLOW if on else GREY, DARK if on else TEXT)
        self.aa_reset_lbl.config(text=str(self.aa_reset) if self.aa_reset else "jamais")
        self.aa_floor_lbl.config(text=str(self.aa_floor) if self.aa_floor else "auto")
        self.aa_resfloor_lbl.config(text=str(self.aa_resfloor))
        nm = lambda lst: ", ".join(AA_CHAR_NAMES.get(n, n.capitalize()) for n in lst) or "aucun"   # noqa: E731
        self.aa_res_lbl.config(text=f"Floors {self.aa_resfloor}+ : {nm(self.aa_reserved)}     |     "
                                    f"Floors {self.aa_resfloor2}+ : {nm(self.aa_reserved2)}")
        for n, bt in self.aa_res_btns.items():
            col = YELLOW if n in self.aa_reserved else BLUE if n in self.aa_reserved2 else None
            bt.style(col or GREY, DARK if col else TEXT)
        self.aa_resfloor2_lbl.config(text=str(self.aa_resfloor2))
        self.aa_fallback_lbl.config(text=str(self.aa_fallback) if self.aa_fallback else "aucun")
        for t, bt in self.aa_tier_btns.items():
            col = YELLOW if t == 1 else BLUE
            bt.config(text=f"Réservés {self.aa_resfloor if t == 1 else self.aa_resfloor2}+")
            bt.style(col if t == self.aa_tier else GREY, DARK if t == self.aa_tier else TEXT)
        self.aa_lbl.config(text=" + ".join(AA_BOTS[b] for b in self.aa_bots) or "sans bot")

    def save_aa(self):
        self.cfg["aa_bots"], self.cfg["aa_reset_floor"] = list(self.aa_bots), self.aa_reset
        self.aa_resfloor = max(1, self.aa_resfloor)
        self.cfg["aa_reserved"], self.cfg["aa_reserve_floor"] = list(self.aa_reserved), self.aa_resfloor
        self.aa_resfloor2 = max(self.aa_resfloor + 1, self.aa_resfloor2)
        self.cfg["aa_reserved2"], self.cfg["aa_reserve2_floor"] = list(self.aa_reserved2), self.aa_resfloor2
        self.cfg["aa_reset_fallback"] = self.aa_fallback
        if self.aa_floor:
            self.cfg["aa_floor"] = self.aa_floor
        else:
            self.cfg.pop("aa_floor", None)
        save_cfg(self.cfg)
        self.paint_aa()

    def toggle_aa_bot(self, b):
        if self.locked():
            return
        if b in self.aa_bots:
            self.aa_bots.remove(b)
        elif len(self.aa_bots) < 2:
            self.aa_bots.append(b)
        else:
            self.aa_bots = [self.aa_bots[1], b]          # 2 maximum : le plus ancien cede sa place
        self.save_aa()

    def toggle_aa_res(self, n):
        """Clic sur un perso : il entre dans la liste editee (10+ ou 13+), ou en sort s'il y etait deja."""
        if self.locked():
            return
        mine, other = (self.aa_reserved, self.aa_reserved2) if self.aa_tier == 1 else (self.aa_reserved2, self.aa_reserved)
        if n in mine:
            mine.remove(n)
        else:
            if n in other:
                other.remove(n)                          # un perso est dans UNE seule liste
            mine.append(n)
        self.save_aa()

    def set_aa_tier(self, t):
        self.aa_tier = t
        self.paint_aa()

    def step_aa(self, what, d):
        if self.locked():
            return
        setattr(self, what, max(0, min(99, getattr(self, what) + d)))
        self.save_aa()

    def paint_whm(self):
        for lv, b in self.whm_lvl_btns.items():
            b.style(YELLOW if lv == self.whm_level else GREY, DARK if lv == self.whm_level else TEXT)
        for ev, b in self.whm_ev_btns.items():
            b.style(YELLOW if ev == self.whm_event else GREY, DARK if ev == self.whm_event else TEXT)
        self.whm_lbl.config(text=f"{WHM_EVENTS[self.whm_event][0]} {self.whm_level}")

    def set_whm_event(self, ev):
        if self.locked():
            return
        self.whm_event = ev
        self.cfg["whm_event"] = ev
        save_cfg(self.cfg)
        self.paint_whm()
        self.refresh_all()

    def set_whm_level(self, lv):
        if self.locked():
            return
        self.whm_level = lv
        self.cfg["whm_level"] = lv
        save_cfg(self.cfg)
        self.paint_whm()
        self.refresh_all()

    def set_sup_level(self, lv):
        if self.locked() or lv > SUPPLY_MAX_LEVEL[self.sup_mode]:
            return
        self.sup_level = lv
        self.paint_supply()
        self.save_supply()

    def save_supply(self):
        self.cfg["supply_mode"], self.cfg["supply_level"] = self.sup_mode, self.sup_level
        save_cfg(self.cfg)

    # ================= enchainement =================
    def step_text(self, st):
        icon = ec.MODES_UI[ec.ui_key(st)][0].split()[0]
        return f"{icon} {ec.describe(st)}"

    def step_token(self, st):
        return ec.token(st)

    def save_chain(self):
        self.cfg["chain_steps"] = [{k: v for k, v in st.items() if v is not None} for st in self.chain_steps]
        self.cfg["chain_type"] = self.chain_type
        self.cfg["chain_stop_on_error"] = bool(self.stop_err.get())
        save_cfg(self.cfg)

    def set_chain_type(self, t):
        if self.locked() or t == self.chain_type:
            return
        self.run_vals["chain"] = self.runs
        self.chain_type = t
        self.save_chain()
        self.apply_mode()

    def add_step(self):
        if self.locked():
            return
        try:
            st = self.chain_editor.get()
        except ValueError as e:
            messagebox.showwarning("Étape incomplète", str(e))
            return
        self.chain_steps.append(st)
        self.save_chain()
        self.paint_steps()
        self.refresh_all()

    def move_step(self, i, d):
        j = i + d
        if self.locked() or not 0 <= j < len(self.chain_steps):
            return
        self.chain_steps[i], self.chain_steps[j] = self.chain_steps[j], self.chain_steps[i]
        self.save_chain()
        self.paint_steps()

    def del_step(self, i):
        if self.locked():
            return
        del self.chain_steps[i]
        self.save_chain()
        self.paint_steps()
        self.refresh_all()

    def clear_steps(self):
        if self.locked() or not self.chain_steps:
            return
        self.chain_steps.clear()
        self.save_chain()
        self.paint_steps()
        self.refresh_all()

    def paint_steps(self, current=None):
        """Affiche le plan ; current = index (0-based) de l'etape en cours pendant l'execution."""
        for w in self.steps_frame.winfo_children():
            w.destroy()
        if not self.chain_steps:
            tk.Label(self.steps_frame, text="(aucune étape : règle une étape ci-dessus puis clique sur « Ajouter »)",
                     bg=CARD, fg=MUTED, font=F(9)).pack(anchor="w", pady=4)
            return
        for i, st in enumerate(self.chain_steps):
            on = current == i
            row = tk.Frame(self.steps_frame, bg=blend(CARD, 0.08) if on else BG, highlightthickness=2,
                           highlightbackground=YELLOW if on else BORDER)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=f"{i + 1}", bg=YELLOW if on else GREY, fg=DARK if on else TEXT, font=F(11, True), width=3).pack(side="left", fill="y")
            tk.Label(row, text=self.step_text(st), bg=row["bg"], fg=TEXT, font=F(10, True), anchor="w").pack(side="left", padx=10, pady=6)
            for txt, fn in (("✕", lambda i=i: self.del_step(i)), ("▼", lambda i=i: self.move_step(i, 1)), ("▲", lambda i=i: self.move_step(i, -1))):
                Btn(row, txt, fn, bg=RED if txt == "✕" else GREY, fg="#ffffff" if txt == "✕" else TEXT, size=9, padx=8, pady=2).pack(side="right", padx=(0, 4), pady=4)

    def step_info(self, st):
        """(script, dossier de templates, images requises, supply ?) d'une etape."""
        if st["mode"] == "hero":
            return HERO_BOT, HERO_TPL, HERO_REQUIRED + [f"{c}.png" for c in st["chars"].split(",") if c], False
        if st["mode"] == "nightops":
            return NIGHT_BOT, NIGHT_TPL, NIGHT_REQUIRED, False
        if st["mode"] == "whm":
            _n, banner, card = WHM_EVENTS[st.get("event", "entrance")]
            return WHM_BOT, WHM_TPL, WHM_REQUIRED + [banner, f"{card}{st['level']}.png"], False
        if st["mode"] == "aa":
            return AA_BOT, AA_TPL, AA_REQUIRED + [f"{b}.png" for b in str(st.get("bots") or "").split(",") if b], False
        m = st["supply"]
        return SUPPLY_BOT, SUPPLY_TPL, ["supply.png", f"{m}.png", f"lvl{m}.png", "confirm.png", "continuechall.png"] + SUPPLY_COMMON, True

    def missing_files(self):
        """-> (script, script_ok, images manquantes, nombre d'images requises) du mode courant."""
        if self.mode == "chain":
            miss, seen = [], set()
            bot_ok = CHAIN_BOT.exists()
            for st in self.chain_steps:
                script, tdir, req, sup = self.step_info(st)
                bot_ok = bot_ok and script.exists()
                for n in req:
                    if (tdir.name, n) in seen:
                        continue
                    seen.add((tdir.name, n))
                    if not ((tdir / n).exists() or (sup and (NIGHT_TPL / n).exists())):
                        miss.append(f"{tdir.name}/{n}")
                if st["mode"] != "aa" and not (CHAR_TPL / f"{st.get('hero') or self.pick}.png").exists():
                    miss.append(f"templates_chars/{st.get('hero') or self.pick}.png")
            if not self.chain_steps:
                miss.append("(aucune étape dans le plan)")
            return CHAIN_BOT, bot_ok, sorted(set(miss)), len(seen)
        script, tdir, req = self.script_info()
        missing = [n for n in req if not self.have(tdir, n)]
        if self.mode == "hero" and not self.chosen():
            missing = missing + ["(aucun perso sélectionné)"]
        if self.mode != "story" and not (CHAR_TPL / f"{self.pick}.png").exists():
            missing = missing + [f"templates_chars/{self.pick}.png"]
        return script, script.exists(), missing, len(req)

    def kill_proc(self):
        """Arrete le script ET ses sous-processus (l'enchaineur lance les bots dans des processus enfants)."""
        if not self.proc:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
        else:
            self.proc.terminate()

    def have(self, tdir, name):
        """Image presente dans le dossier du mode (Supply : ou dans templates_nightops/ pour les images communes)."""
        return (tdir / name).exists() or (self.mode == "supply" and (NIGHT_TPL / name).exists())

    def set_mode(self, mode):
        if self.locked() or mode == self.mode:
            return
        if self.mode in self.run_vals:
            self.run_vals[self.mode] = self.runs
        self.mode = mode
        self.cfg["mode"] = mode
        save_cfg(self.cfg)
        self.apply_mode()
        self.refresh_all()

    def apply_mode(self):
        chain = self.mode == "chain"
        hero = self.mode == "hero"
        if not hero:
            self.runs = self.run_vals[self.mode]
            self.runs_lbl.config(text=str(self.runs))
            if chain:
                self.runs_title.config(text="Lancements (Start)")
                self.runs_desc.config(text="de cette étape (1 lancement = 1 Multi-run au max)")
            elif self.mode == "aa":
                self.runs_title.config(text="Combats")
                self.runs_desc.config(text="nombre de combats (1 combat = 1 floor), puis le bot s'arrête")
            elif self.mode == "whm":
                self.runs_title.config(text="Multi-runs")
                self.runs_desc.config(text="1 multi-run = case cochée + Start = le maximum de runs possible")
            elif self.mode in ("night", "supply"):
                self.runs_title.config(text="Lancements (Start)")
                self.runs_desc.config(text="1 lancement = 1 Multi-run au max (le bot s'arrête à la fin du dernier combat)")
            else:
                self.runs_title.config(text="Nombre de runs")
                self.runs_desc.config(text="1 run = 4 combats = 20 de stamina")
        for key, b in self.mode_btns.items():
            b.style(YELLOW if key == self.mode else GREY, DARK if key == self.mode else TEXT)
        for f in (self.story_opts, self.hero_opts, self.supply_opts, self.whm_opts, self.aa_opts, self.chain_opts, self.chain_list):
            f.pack_forget()
        if chain:
            self.chain_opts.pack(fill="x", after=self.mode_row)
        elif self.mode == "supply":
            self.paint_supply()
            self.supply_opts.pack(fill="x", after=self.mode_row)
            self.story_opts.pack(fill="x", after=self.supply_opts)
        elif self.mode == "whm":
            self.paint_whm()
            self.whm_opts.pack(fill="x", after=self.mode_row)
            self.story_opts.pack(fill="x", after=self.whm_opts)
        elif self.mode == "aa":
            self.paint_aa()
            self.aa_opts.pack(fill="x", after=self.mode_row)
            self.story_opts.pack(fill="x", after=self.aa_opts)
        else:
            (self.hero_opts if hero else self.story_opts).pack(fill="x", after=self.mode_row)
        self.pick_opts.pack_forget()
        self.refill_opts.pack_forget()
        if self.mode not in ("story", "aa"):     # Histoire : equipe automatique ; Agency : persos geres par la stamina
            self.paint_pick()
            self.pick_opts.pack(fill="x")
        if self.mode in ("hero", "night", "supply", "whm", "aa", "chain"):
            self.refill_opts.pack(fill="x")
        if chain:
            self.paint_steps()
            self.chain_list.pack(fill="x")
        self.sel_lbl.config(text=self.sel_text())
        self.stats_lbl.config(text="Persos 0/0   ·   ⏱ 00:00" if self.mode == "hero" else "Run 0/0   ·   ⏱ 00:00")
        self.run_bar.config(value=0)

    def script_info(self):
        """(script, dossier de templates, images requises) du mode courant."""
        if self.mode == "chain":
            return CHAIN_BOT, BASE, []
        if self.mode == "hero":
            return HERO_BOT, HERO_TPL, HERO_REQUIRED + [f"{c}.png" for c in self.chosen()]
        if self.mode == "night":
            return NIGHT_BOT, NIGHT_TPL, NIGHT_REQUIRED
        if self.mode == "aa":
            return AA_BOT, AA_TPL, AA_REQUIRED + [f"{b}.png" for b in self.aa_bots]
        if self.mode == "whm":
            _n, banner, card = WHM_EVENTS[self.whm_event]
            return WHM_BOT, WHM_TPL, WHM_REQUIRED + [banner, f"{card}{self.whm_level}.png"]
        if self.mode == "supply":
            m = self.sup_mode
            return SUPPLY_BOT, SUPPLY_TPL, ["supply.png", f"{m}.png", f"lvl{m}.png", "confirm.png", "continuechall.png"] + SUPPLY_COMMON
        return BOT, TEMPLATES, REQUIRED

    # ================= helpers UI =================
    def log(self, msg, color=None):
        self.after(0, self._log, msg, color)

    def _log(self, msg, color=None):
        self.logbox.config(state="normal")
        tag = f"c{color}" if color else None
        if color:
            self.logbox.tag_config(tag, foreground=color)
        self.logbox.insert("end", msg + "\n", tag)
        self.logbox.see("end")
        self.logbox.config(state="disabled")

    def set_status(self, key, ok, text):
        c = self.cards[key]
        c["status"].config(text=("✔ " if ok else "✘ ") + text, fg=GREEN if ok else RED)
        c["badge"].config(text="✔" if ok else c["num"], bg=GREEN if ok else GREY, fg=DARK if ok else TEXT)
        c["frame"].config(highlightbackground=GREEN if ok else BORDER)

    def run_bg(self, fn):
        def work():
            try:
                fn()
            finally:
                self.after(0, self.refresh_all)
        threading.Thread(target=work, daemon=True).start()

    def change_runs(self, d):
        self.runs = max(1, min(500, self.runs + d))
        self.runs_lbl.config(text=str(self.runs))

    # ================= état =================
    def adb_path(self):
        local = PT_DIR / "adb.exe"
        if local.exists():
            return str(local)
        p = self.cfg.get("adb")
        return p if p and Path(p).exists() else None

    def deps_ok(self):
        r = subprocess.run([sys.executable, "-c", "import cv2, numpy"],
                           capture_output=True, creationflags=NO_WINDOW)
        return r.returncode == 0

    def refresh_all(self):
        def work():
            deps = self.deps_ok()
            adb = self.adb_path()
            script, bot_ok, missing, nreq = self.missing_files()
            self.after(0, lambda: self.apply_state(deps, adb, missing, bot_ok, script, nreq))
        threading.Thread(target=work, daemon=True).start()

    def apply_state(self, deps, adb, missing, bot_ok, script, nreq):
        self.set_status("deps", deps, "modules installés" if deps else "modules manquants")
        self.set_status("adb", bool(adb), adb if adb else "ADB introuvable")
        files_ok = bot_ok and not missing
        if files_ok:
            self.set_status("files", True, f"script et {nreq} images présents")
        else:
            txt = ("" if bot_ok else f"{script.name} introuvable. ") + \
                  (("images manquantes : " + ", ".join(missing)) if missing else "")
            self.set_status("files", False, txt)
        dev = self.cfg.get("device")
        self.set_status("device", bool(dev), f"appareil choisi : {dev}" if dev else "aucun appareil choisi")
        self.ok = {"deps": deps, "adb": bool(adb), "files": files_ok, "device": bool(dev)}
        n = sum(self.ok.values())
        self.setup_bar.config(value=n)
        self.setup_lbl.config(text=f"Configuration : {n}/4" + ("   ·   tout est prêt ! 🎉" if n == 4 else ""),
                              fg=GREEN if n == 4 else MUTED)
        if not self.proc and not self.ext:
            if n == 4:
                self.launch_btn.style(YELLOW, DARK)
                self.action_lbl.config(
                    text=("Tout est vert. Reste sur le menu principal et lance !" if self.mode in ("hero", "night", "supply", "chain")
                          else "Tout est vert. Mets ton perso en ville et lance !"), fg=GREEN)
            else:
                self.launch_btn.style(GREY, TEXT)
                self.action_lbl.config(text="Prêt quand toutes les étapes sont vertes.", fg=MUTED)

    def open_stats(self):
        """Fenetre des statistiques : aujourd'hui / 7 jours / tout, detail par mode (7 j), dernieres sessions."""
        win = tk.Toplevel(self)
        win.title("MHA Bot - statistiques")
        win.configure(bg=BG)
        win.geometry("880x640")
        tk.Label(win, text="📊 Statistiques des farms", bg=BG, fg=YELLOW, font=F(16, True)).pack(anchor="w", padx=16, pady=(12, 6))
        tiles = tk.Frame(win, bg=BG)
        tiles.pack(fill="x", padx=12)
        for days, title in ((1, "Dernières 24 h"), (7, "7 derniers jours"), (0, "Depuis le début")):
            t = stats.summary(days)["total"]
            f = tk.Frame(tiles, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
            f.pack(side="left", fill="both", expand=True, padx=4)
            tk.Label(f, text=title, bg=CARD, fg=MUTED, font=F(9, True)).pack(anchor="w", padx=10, pady=(8, 0))
            tk.Label(f, text=f"{t['runs']} runs", bg=CARD, fg=TEXT, font=("Segoe UI Black", 20)).pack(anchor="w", padx=10)
            tk.Label(f, text=f"{t['sessions']} session(s) · {stats.hms(t['duree'])} de farm\n{t['reussite']} % finies · "
                     f"{t['recharges']} recharge(s) · {t['relances']} relance(s)", bg=CARD, fg=MUTED, font=F(9),
                     justify="left").pack(anchor="w", padx=10, pady=(0, 8))
        tk.Label(win, text="Par mode (7 derniers jours)", bg=BG, fg=TEXT, font=F(11, True)).pack(anchor="w", padx=16, pady=(12, 2))
        tab = tk.Frame(win, bg=CARD)
        tab.pack(fill="x", padx=16)
        modes = stats.summary(7)["modes"]
        for c, h in enumerate(("Mode", "Sessions", "Runs", "Temps", "Finies", "Recharges", "Relances", "Reprises")):
            tk.Label(tab, text=h, bg=CARD, fg=MUTED, font=F(9, True)).grid(row=0, column=c, sticky="w", padx=8, pady=(6, 2))
        for r, (m, a) in enumerate(modes.items(), start=1):
            vals = (m, a["sessions"], a["runs"], stats.hms(a["duree"]), f"{a['reussite']} %", a["recharges"], a["relances"], a["reprises"])
            for c, v in enumerate(vals):
                tk.Label(tab, text=str(v), bg=CARD, fg=TEXT, font=F(9)).grid(row=r, column=c, sticky="w", padx=8, pady=1)
        if not modes:
            tk.Label(tab, text="Aucun farm ces 7 derniers jours.", bg=CARD, fg=MUTED, font=F(9)).grid(row=1, column=0, columnspan=8,
                                                                                                    sticky="w", padx=8, pady=4)
        tk.Label(win, text="Dernières sessions", bg=BG, fg=TEXT, font=F(11, True)).pack(anchor="w", padx=16, pady=(12, 2))
        hist = tk.Frame(win, bg=CARD)
        hist.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        last = stats.summary(0)["dernieres"]
        for s in last:
            tk.Label(hist, text=stats.describe(s), bg=CARD, fg=TEXT, font=F(9), anchor="w").pack(fill="x", padx=8)
        if not last:
            tk.Label(hist, text="L'historique se remplit tout seul à chaque farm (logs/runs.jsonl).", bg=CARD, fg=MUTED,
                     font=F(9)).pack(anchor="w", padx=8, pady=4)
        return win

    def open_refaire(self):
        """🖼️ Images : refaire une image de bouton a partir d'une capture (rectangle a la souris), puis verification."""
        win = tk.Toplevel(self)
        win.title("MHA Bot - refaire une image")
        win.configure(bg=BG)
        win.geometry("1220x720")
        tk.Label(win, text="🖼️ Refaire une image de bouton", bg=BG, fg=YELLOW, font=F(16, True)).pack(anchor="w", padx=16, pady=(12, 0))
        tk.Label(win, text="1) choisis une capture  2) choisis l'image à remplacer  3) trace un rectangle autour du bouton  "
                 "4) Enregistrer et vérifier. L'ancienne image est gardée (bouton « Remettre l'ancienne »).",
                 bg=BG, fg=MUTED, font=F(9), wraplength=1180, justify="left").pack(anchor="w", padx=16, pady=(0, 8))
        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=16)
        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="y")
        caps = refaire.captures()
        lb = tk.Listbox(left, width=46, height=26, bg=CARD, fg=TEXT, selectbackground=BLUE, font=F(9), activestyle="none")
        for _p, label, _s in caps:
            lb.insert("end", label[:70])
        lb.pack(fill="y", expand=True)
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))
        top = tk.Frame(right, bg=BG)
        top.pack(fill="x")
        tk.Label(top, text="Image à remplacer", bg=BG, fg=MUTED, font=F(9, True)).pack(side="left")
        target = ttk.Combobox(top, width=44, values=refaire.templates())
        target.pack(side="left", padx=(6, 10))
        preview = tk.Label(top, bg=BG)
        preview.pack(side="left")
        canvas = tk.Canvas(right, width=800, height=450, bg="#000000", highlightthickness=0, cursor="crosshair")
        canvas.pack(anchor="w", pady=(6, 6))
        status = tk.Label(right, text="", bg=BG, fg=MUTED, font=F(10, True), anchor="w", justify="left", wraplength=800)
        bot = tk.Frame(right, bg=BG)
        bot.pack(fill="x")
        st = {"cap": None, "img": None, "rect": None, "box": None, "start": None, "backup": None, "prev": None}

        def show_preview(*_):
            p = BASE / target.get()
            try:
                im = tk.PhotoImage(file=str(p))
                while im.width() > 220 or im.height() > 70:
                    im = im.subsample(2)
                st["prev"] = im
                preview.config(image=im, text="")
            except Exception:                                # noqa: BLE001
                preview.config(image="", text="(nouvelle image)", fg=MUTED)
        target.bind("<<ComboboxSelected>>", show_preview)

        def pick(_e=None):
            sel = lb.curselection()
            if not sel:
                return
            path, _label, sug = caps[sel[0]]
            st["cap"], st["box"] = path, None
            img = tk.PhotoImage(file=str(path))
            st["scale"] = 2 if img.width() > 800 else 1
            st["img"] = img.subsample(st["scale"]) if st["scale"] > 1 else img
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=st["img"])
            if sug:
                target.set(sug)
                show_preview()
            status.config(text="Trace un rectangle autour du bouton (clic gauche maintenu).", fg=MUTED)
        lb.bind("<<ListboxSelect>>", pick)

        def press(e):
            st["start"] = (e.x, e.y)
            if st["rect"]:
                canvas.delete(st["rect"])
            st["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline=YELLOW, width=2)

        def drag(e):
            if st["start"]:
                canvas.coords(st["rect"], *st["start"], e.x, e.y)

        def release(e):
            if not st["start"]:
                return
            x0, y0 = st["start"]
            k = st.get("scale", 1)
            st["box"] = (min(x0, e.x) * k, min(y0, e.y) * k, max(x0, e.x) * k, max(y0, e.y) * k)
            st["start"] = None
            b = st["box"]
            status.config(text=f"Rectangle : {int(b[2] - b[0])} x {int(b[3] - b[1])} px. Clique sur « Enregistrer et vérifier ».",
                          fg=MUTED)
        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", release)

        def save():
            if not st["cap"] or not st["box"] or not target.get():
                status.config(text="❌ Il faut une capture, l'image à remplacer et un rectangle.", fg=RED)
                return
            try:
                st["backup"] = refaire.save_crop(st["cap"], st["box"], target.get())
            except ValueError as e:
                status.config(text=f"❌ {e}", fg=RED)
                return
            show_preview()
            status.config(text="✔ Image enregistrée. Vérification en cours (mêmes réglages que le bot)…", fg=YELLOW)
            cap, tgt = st["cap"], target.get()

            def work():
                try:
                    r = refaire.check_subprocess(tgt, cap)
                    if not r["ok"]:
                        msg, col = (f"⚠️ Pas reconnue sur cette capture ({r['score']} < seuil {r['seuil']}) : retrace le "
                                    "rectangle plus serré autour du bouton, ou « Remettre l'ancienne »."), RED
                    else:
                        msg, col = f"✅ Reconnue : {r['score']} (seuil {r['seuil']}).", GREEN
                        if r.get("ailleurs_fichier") and r["ailleurs_max"] >= r["seuil"]:
                            msg += (f"\n   Trouvée aussi sur « {r['ailleurs_fichier']} » ({r['ailleurs_max']}) : normal si le "
                                    "bouton y est vraiment, sinon c'est un faux positif → découpe plus précise.")
                        elif r.get("ailleurs_fichier"):
                            msg += f"  Ailleurs : {r['ailleurs_max']} au plus (pas de confusion)."
                except Exception as e:                       # noqa: BLE001
                    msg, col = f"⚠️ Vérification impossible : {e}", RED
                self.after(0, lambda: status.config(text=msg, fg=col))
            threading.Thread(target=work, daemon=True).start()

        def undo():
            b = st.get("backup")
            if not b or not Path(b).exists():
                status.config(text="Rien à remettre (pas d'ancienne image sauvegardée dans cette fenêtre).", fg=MUTED)
                return
            shutil.copy2(b, BASE / target.get())
            st["backup"] = None
            show_preview()
            status.config(text="↩ Ancienne image remise.", fg=MUTED)
        Btn(bot, "💾 Enregistrer et vérifier", save, bg=YELLOW, padx=14, pady=5).pack(side="left")
        Btn(bot, "↩ Remettre l'ancienne", undo, bg=GREY, fg=TEXT, padx=12, pady=5).pack(side="left", padx=(8, 0))
        status.pack(fill="x", pady=(6, 0))
        if caps:
            lb.selection_set(0)
            pick()
        win.refaire_ui = {"list": lb, "caps": caps, "target": target, "canvas": canvas, "status": status, "state": st,
                          "press": press, "drag": drag, "release": release, "save": save, "undo": undo, "pick": pick}
        return win

    def open_backups(self):
        """💾 Sauvegardes : liste, sauvegarder maintenant, restaurer (l'assistant se relance)."""
        win = tk.Toplevel(self)
        win.title("MHA Bot - sauvegardes")
        win.configure(bg=BG)
        win.geometry("720x460")
        tk.Label(win, text="💾 Sauvegardes de tes réglages", bg=BG, fg=YELLOW, font=F(16, True)).pack(anchor="w", padx=16, pady=(12, 0))
        tk.Label(win, text="Réglages, planning, profils. Une par jour, avant chaque mise à jour et avant chaque restauration "
                 "(les 10 dernières). Le token Discord n'est jamais copié.", bg=BG, fg=MUTED, font=F(9), wraplength=680,
                 justify="left").pack(anchor="w", padx=16, pady=(0, 8))
        lst = tk.Frame(win, bg=CARD)
        lst.pack(fill="x", padx=16)
        status = tk.Label(win, text="", bg=BG, fg=MUTED, font=F(9), anchor="w", justify="left", wraplength=680)

        def refresh():
            for w in lst.winfo_children():
                w.destroy()
            items = sauvegarde.listing()
            for d, when, why, files in items:
                row = tk.Frame(lst, bg=CARD)
                row.pack(fill="x", padx=8, pady=2)
                tk.Label(row, text=f"{when}   {why:<20}   {', '.join(files)}", bg=CARD, fg=TEXT, font=F(9), anchor="w").pack(side="left")
                Btn(row, "Restaurer", lambda d=d, when=when: restore(d, when), bg=YELLOW, size=9, padx=10, pady=1).pack(side="right")
            if not items:
                tk.Label(lst, text="Aucune sauvegarde pour l'instant.", bg=CARD, fg=MUTED, font=F(9)).pack(anchor="w", padx=8, pady=6)

        def now():
            d = sauvegarde.backup("manuelle")
            status.config(text=f"✅ sauvegardé ({d.name})" if d else "Rien à sauvegarder.", fg=GREEN if d else MUTED)
            refresh()

        def restore(d, when):
            if self.proc:
                status.config(text="❌ Un farm tourne : arrête-le avant de restaurer.", fg=RED)
                return
            try:
                files = sauvegarde.restore(d)
            except (ValueError, OSError) as e:
                status.config(text=f"❌ {e}", fg=RED)
                return
            status.config(text=f"✅ Restauré ({', '.join(files)}) depuis la sauvegarde du {when} : l'assistant se relance…", fg=GREEN)
            self.after(800, self.restart)
        Btn(win, "💾 Sauvegarder maintenant", now, bg=GREY, fg=TEXT, padx=12, pady=4).pack(anchor="w", padx=16, pady=(10, 0))
        status.pack(fill="x", padx=16, pady=(6, 0))
        refresh()
        win.backups_ui = {"now": now, "restore": restore, "status": status, "list": lst}
        return win

    def open_profiles(self):
        """Profils : enregistrer les reglages actuels sous un nom, en recharger un (l'assistant se relance), supprimer."""
        win = tk.Toplevel(self)
        win.title("MHA Bot - profils")
        win.configure(bg=BG)
        win.geometry("760x480")
        tk.Label(win, text="👤 Profils", bg=BG, fg=YELLOW, font=F(16, True)).pack(anchor="w", padx=16, pady=(12, 0))
        tk.Label(win, text="Un profil garde tes réglages de farm (mode, persos, niveaux, runs, recharge, garde-chien). "
                 "Jamais ceux du PC (ADB, émulateur).", bg=BG, fg=MUTED, font=F(9), wraplength=720,
                 justify="left").pack(anchor="w", padx=16, pady=(0, 8))
        lst = tk.Frame(win, bg=CARD)
        lst.pack(fill="x", padx=16)
        status = tk.Label(win, text="", bg=BG, fg=MUTED, font=F(9), anchor="w", justify="left", wraplength=720)

        def refresh():
            for w in lst.winfo_children():
                w.destroy()
            data = profils.load()
            for n, p in data.items():
                row = tk.Frame(lst, bg=CARD)
                row.pack(fill="x", padx=8, pady=2)
                tk.Label(row, text=n, bg=CARD, fg=TEXT, font=F(10, True), width=18, anchor="w").pack(side="left")
                tk.Label(row, text=profils.summary(p), bg=CARD, fg=MUTED, font=F(9), anchor="w").pack(side="left")
                Btn(row, "🗑", lambda n=n: delete(n), bg=GREY, fg=TEXT, size=9, padx=8, pady=1).pack(side="right")
                Btn(row, "Charger", lambda n=n: use(n), bg=YELLOW, size=9, padx=10, pady=1).pack(side="right", padx=(0, 4))
            if not data:
                tk.Label(lst, text="Aucun profil pour l'instant.", bg=CARD, fg=MUTED, font=F(9)).pack(anchor="w", padx=8, pady=6)

        def delete(n):
            profils.delete(n)
            status.config(text=f"profil « {n} » supprimé", fg=MUTED)
            refresh()

        def use(n):
            if self.proc:
                status.config(text="❌ Un farm tourne : arrête-le avant de changer de profil.", fg=RED)
                return
            self.cfg = profils.apply(n, self.cfg)
            save_cfg(self.cfg)
            status.config(text=f"✅ profil « {n} » chargé : l'assistant se relance…", fg=GREEN)
            self.after(700, self.restart)

        form = tk.Frame(win, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        form.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(form, text="Enregistrer les réglages actuels sous le nom", bg=CARD, fg=TEXT, font=F(10, True)).pack(side="left", padx=10, pady=10)
        name = tk.Entry(form, width=24, font=F(11))
        name.pack(side="left", padx=(0, 8))

        def save():
            try:
                n = profils.save(name.get(), self.cfg)
                status.config(text=f"✅ profil « {n} » enregistré ({profils.summary(profils.load()[n])})", fg=GREEN)
                name.delete(0, "end")
            except ValueError as e:
                status.config(text=f"❌ {e}", fg=RED)
            refresh()
        Btn(form, "💾 Enregistrer", save, bg=YELLOW, padx=12, pady=4).pack(side="left")
        status.pack(fill="x", padx=16, pady=(4, 0))
        refresh()
        win.profils_ui = {"name": name, "save": save, "use": use, "delete": delete, "list": lst, "status": status}
        return win

    def restart(self):
        """Relance l'assistant (apres chargement d'un profil : tous les ecrans relisent config.json)."""
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe")
        subprocess.Popen([str(pyw if pyw.exists() else exe), str(BASE / "setup_assistant.py")], cwd=str(BASE))
        self.destroy()

    def open_planning(self):
        """Fenetre des farms programmes : liste (prochaine execution, suppression) + ajout d'un creneau."""
        win = tk.Toplevel(self)
        win.title("MHA Bot - planning")
        win.configure(bg=BG)
        win.geometry("900x700")
        tk.Label(win, text="🗓️ Farms programmés", bg=BG, fg=YELLOW, font=F(16, True)).pack(anchor="w", padx=16, pady=(12, 0))
        tk.Label(win, text="Le PC peut être en veille (il se réveille tout seul), pas éteint. Si un farm tourne déjà à l'heure "
                 "prévue, le créneau est sauté. Notification sur Discord à la fin.", bg=BG, fg=MUTED, font=F(9),
                 wraplength=820, justify="left").pack(anchor="w", padx=16, pady=(0, 8))
        lst = tk.Frame(win, bg=CARD)
        lst.pack(fill="x", padx=16)
        status = tk.Label(win, text="", bg=BG, fg=MUTED, font=F(9), anchor="w", justify="left", wraplength=820)

        gen = [0]                                            # seul le dernier rafraichissement s'affiche

        def refresh():
            gen[0] += 1
            me = gen[0]
            for w in lst.winfo_children():
                w.destroy()
            tk.Label(lst, text="… lecture du Planificateur Windows", bg=CARD, fg=MUTED, font=F(9)).pack(anchor="w", padx=8, pady=6)

            def work():
                nxt = pl.next_runs()
                items = pl.load()                            # relu APRES : jamais une liste perimee
                self.after(0, lambda: show(items, nxt) if me == gen[0] else None)
            threading.Thread(target=work, daemon=True).start()

        def show(items, nxt):
            for w in lst.winfo_children():
                w.destroy()
            for it in items:
                row = tk.Frame(lst, bg=CARD)
                row.pack(fill="x", padx=8, pady=2)
                tk.Label(row, text=pl.describe(it) + f"   ·   prochaine : {nxt.get(it['id'], '⚠️ tâche absente')}", bg=CARD,
                         fg=TEXT, font=F(9), anchor="w").pack(side="left")
                Btn(row, "🗑", lambda i=it["id"]: delete(i), bg=GREY, fg=TEXT, size=9, padx=8, pady=1).pack(side="right")
            if not items:
                tk.Label(lst, text="Aucun farm programmé.", bg=CARD, fg=MUTED, font=F(9)).pack(anchor="w", padx=8, pady=6)

        def delete(i):
            def work():
                pl.remove(i)
                self.after(0, refresh)
            status.config(text=f"suppression du créneau #{i}…", fg=MUTED)
            threading.Thread(target=work, daemon=True).start()

        form = tk.Frame(win, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        form.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(form, text="Nouveau créneau  ·  1. Ce qu'il faut farmer", bg=CARD, fg=TEXT, font=F(11, True)).pack(
            anchor="w", padx=10, pady=(8, 4))
        ed = StepEditor(form)
        ed.pack(fill="x", padx=10)
        steps = []
        srow = tk.Frame(form, bg=CARD)
        srow.pack(fill="x", padx=10, pady=(6, 0))
        box = tk.Frame(form, bg=BG)
        box.pack(fill="x", padx=10, pady=(6, 0))

        def paint():
            for c in box.winfo_children():
                c.destroy()
            for i, st in enumerate(steps):
                r = tk.Frame(box, bg=BG)
                r.pack(fill="x", pady=1)
                tk.Label(r, text=f"{i + 1}.  {ec.describe(st)}", bg=BG, fg=TEXT, font=F(9), anchor="w").pack(side="left", padx=6)
                Btn(r, "🗑", lambda i=i: (steps.pop(i), paint()), bg=GREY, fg=TEXT, size=8, padx=6, pady=0).pack(side="right")
                Btn(r, "↓", lambda i=i: move(i, 1), bg=GREY, fg=TEXT, size=8, padx=6, pady=0).pack(side="right", padx=(0, 2))
                Btn(r, "↑", lambda i=i: move(i, -1), bg=GREY, fg=TEXT, size=8, padx=6, pady=0).pack(side="right", padx=(0, 2))
            if not steps:
                tk.Label(box, text="Aucune étape : règle le mode ci-dessus puis « Ajouter cette étape » "
                         "(plusieurs étapes = elles s'enchaînent).", bg=BG, fg=MUTED, font=F(9)).pack(anchor="w", padx=6, pady=3)

        def move(i, d):
            j = i + d
            if 0 <= j < len(steps):
                steps[i], steps[j] = steps[j], steps[i]
                paint()

        def add_step():
            try:
                steps.append(ed.get())
                status.config(text="", fg=MUTED)
            except ValueError as e:
                status.config(text=f"❌ {e}", fg=RED)
            paint()

        def from_chain():
            steps[:] = [dict(st) for st in self.chain_steps]
            status.config(text=f"{len(steps)} étape(s) reprise(s) de l'enchaînement de l'assistant" if steps else
                          "L'enchaînement de l'assistant est vide (mode Enchaîner).", fg=MUTED)
            paint()
        Btn(srow, "➕ Ajouter cette étape", add_step, bg=YELLOW, padx=12, pady=3).pack(side="left")
        Btn(srow, "📋 Reprendre l'enchaînement de l'assistant", from_chain, bg=GREY, fg=TEXT, size=9, padx=10,
            pady=3).pack(side="left", padx=(8, 0))
        paint()

        tk.Label(form, text="2. Quand", bg=CARD, fg=TEXT, font=F(11, True)).pack(anchor="w", padx=10, pady=(12, 4))
        wrow = tk.Frame(form, bg=CARD)
        wrow.pack(fill="x", padx=10, pady=(0, 10))
        tk.Label(wrow, text="Heure", bg=CARD, fg=MUTED, font=F(9, True)).pack(side="left", padx=(0, 4))
        heure = tk.Entry(wrow, width=7, font=F(11), justify="center")
        heure.insert(0, "02:00")
        heure.pack(side="left", padx=(0, 14))
        jours = ttk.Combobox(wrow, width=22, state="readonly", values=list(pl.JOURS.values()))
        jours.current(0)
        jours.pack(side="left", padx=(0, 14))

        def add():
            if not steps:
                status.config(text="❌ Ajoute au moins une étape (1. Ce qu'il faut farmer).", fg=RED)
                return
            code = list(pl.JOURS)[jours.current()]
            seq = " ".join(ec.token(st) for st in steps)
            status.config(text="ajout dans le Planificateur Windows…", fg=MUTED)

            def work():
                try:
                    it, _m = pl.add(heure.get(), code, seq, refill=refill_v.get(), watchdog=wd_v.get())
                    msg, col = f"✅ créneau #{it['id']} programmé : {pl.describe(it)}", GREEN
                    self.after(0, lambda: (steps.clear(), paint()))
                except ValueError as e:
                    msg, col = f"❌ {e}", RED
                self.after(0, lambda: (status.config(text=msg, fg=col), refresh()))
            threading.Thread(target=work, daemon=True).start()
        Btn(wrow, "🗓️ Programmer", add, bg=YELLOW, padx=14, pady=4).pack(side="left")
        orow = tk.Frame(form, bg=CARD)                           # options du creneau (au depart : reglage general)
        orow.pack(fill="x", padx=10, pady=(0, 10))
        refill_v = tk.BooleanVar(value=bool(self.cfg.get("stamina_refill", False)))
        wd_v = tk.BooleanVar(value=bool(self.cfg.get("watchdog", False)))
        for txt, var in (("🔋 Recharge de stamina (code cadeau)", refill_v), ("🛡️ Chien de garde (maintenance / blocage)", wd_v)):
            tk.Checkbutton(orow, text=txt, variable=var, bg=CARD, fg=TEXT, selectcolor=BG, activebackground=CARD,
                           activeforeground=TEXT, font=F(10, True)).pack(side="left", padx=(0, 18))
        status.pack(fill="x", padx=16, pady=(4, 0))
        refresh()
        win.planning_ui = {"editor": ed, "steps": steps, "add_step": add_step, "from_chain": from_chain, "heure": heure,
                           "refill": refill_v, "watchdog": wd_v,
                           "jours": jours, "add": add, "status": status, "list": lst, "box": box}
        return win

    def run_verif(self):
        """Verifications de verif.py en arriere-plan, puis affichage (✅ / ⚠️ / ❌ + conseil)."""
        for lb in self.verif_lbls.values():
            lb.config(text="… vérification", fg=MUTED)

        def work():
            alt = None
            try:
                res = verif.run_all(self.adb_path(), self.cfg.get("device"))
                if res and res[0][1] == "warn":
                    alt = verif.alt_device(self.adb_path(), self.cfg.get("device"))
            except Exception as e:                       # noqa: BLE001
                res = [("device", "bad", f"vérification impossible ({e})", "")]
            self.after(0, lambda: self.show_verif(res, alt))
        threading.Thread(target=work, daemon=True).start()

    def use_alt_device(self):
        """Bouton « ✔ Utiliser <appareil> » : l'emulateur a change de nom, on enregistre celui qui repond."""
        if not self.verif_alt:
            return
        self.cfg["device"] = self.verif_alt
        save_cfg(self.cfg)
        self.log(f"✔ Émulateur enregistré : {self.verif_alt}", GREEN)
        self.verif_alt = None
        self.verif_fix.pack_forget()
        self.refresh_all()
        self.run_verif()

    def show_verif(self, res, alt=None):
        self.verif_alt = alt
        if alt:
            self.verif_fix.config(text=f"✔ Utiliser {alt}")
            self.verif_fix.pack(side="right", padx=(0, 8))
        else:
            self.verif_fix.pack_forget()
        icon = {"ok": ("✅", GREEN), "warn": ("⚠️", YELLOW), "bad": ("❌", RED)}
        for key, st, txt, tip in res:
            if key in self.verif_lbls:
                i, col = icon[st]
                self.verif_lbls[key].config(text=f"{i}  {txt}" + (f"   →  {tip}" if tip else ""), fg=col if st != "ok" else TEXT)
        self.verif_when.config(text="vérifié à " + time.strftime("%H:%M:%S"))
        bad = [txt for _k, st, txt, _t in res if st == "bad"]
        if bad:
            self.log("🩺 À corriger : " + " ; ".join(bad), RED)

    def problems(self):
        names = {"deps": "modules Python (étape 1)", "adb": "ADB (étape 2)",
                 "files": "fichiers du bot (étape 3)", "device": "émulateur (étape 4)"}
        return [names[k] for k, v in self.ok.items() if not v]

    # ================= actions =================
    def adb(self, *args, binary=False, timeout=20):
        adb = self.adb_path()
        if not adb:
            return None
        try:
            return subprocess.run([adb, *args], capture_output=True, text=not binary,
                                  timeout=timeout, creationflags=NO_WINDOW)
        except Exception as e:
            self.log(f"Erreur ADB : {e}", RED)
            return None

    def do_install_deps(self):
        if self.deps_ok():
            self.log("📦 Modules déjà installés.", GREEN)
            return
        self.log("📦 Installation de opencv-python et numpy... (patience)")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "opencv-python", "numpy"],
                           capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode == 0:
            self.log("📦 Modules installés !", GREEN)
        else:
            self.log("📦 Échec :\n" + r.stderr[-500:], RED)

    def do_download_adb(self):
        if os.name != "nt":
            self.log("Le téléchargement automatique ne fonctionne que sous Windows.", RED)
            return
        if (PT_DIR / "adb.exe").exists():
            self.log("🔧 ADB déjà installé.", GREEN)
            return
        try:
            self.log("🔧 Téléchargement d'ADB (quelques Mo)...")
            zpath = BASE / "platform-tools.zip"
            urllib.request.urlretrieve(PT_URL, zpath)
            with zipfile.ZipFile(zpath) as z:
                z.extractall(BASE)
            zpath.unlink()
            self.cfg["adb"] = str(PT_DIR / "adb.exe")
            save_cfg(self.cfg)
            self.log("🔧 ADB installé !", GREEN)
        except Exception as e:
            self.log(f"🔧 Erreur de téléchargement : {e}", RED)

    def pick_adb(self):
        p = filedialog.askopenfilename(title="Choisis adb.exe", filetypes=[("adb", "adb.exe")])
        if p:
            self.cfg["adb"] = p
            save_cfg(self.cfg)
            self.refresh_all()

    def open_templates(self):
        _s, tdir, _r = self.script_info()
        tdir = BASE if self.mode == "chain" else tdir
        tdir.mkdir(exist_ok=True)
        if os.name == "nt":
            os.startfile(tdir)
        else:
            self.log(f"Dossier : {tdir}")

    def list_devices(self):
        r = self.adb("devices")
        if not r:
            return []
        out = []
        for line in r.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) == 2 and parts[1] == "device":
                out.append(parts[0])
        return out

    def do_detect(self):
        if not self.adb_path():
            self.log("🔍 Installe d'abord ADB (étape 2).", RED)
            return
        self.log("🔍 Recherche de l'émulateur...")
        self.adb("start-server")
        devs = self.list_devices()
        if not devs:
            for p in PORTS:
                self.adb("connect", p, timeout=6)
            devs = self.list_devices()
        if devs:
            self.log("🔍 Trouvé : " + ", ".join(devs), GREEN)
            self.after(0, lambda: self.set_devices(devs))
        else:
            self.log("🔍 Aucun appareil. L'émulateur est-il lancé avec le jeu ouvert ? "
                     "Sinon active le débogage ADB dans ses réglages.", RED)

    def set_devices(self, devs):
        self.combo["values"] = devs
        self.combo.set(devs[0])
        if len(devs) == 1:
            self.use_device()

    def use_device(self):
        dev = self.combo.get()
        if dev:
            self.cfg["device"] = dev
            save_cfg(self.cfg)
            self.refresh_all()

    def capture(self):
        dev = self.cfg.get("device")
        if not dev:
            messagebox.showwarning("Capture", "Choisis d'abord un appareil (étape 4).")
            return

        def work():
            r = self.adb("-s", dev, "exec-out", "screencap", "-p", binary=True, timeout=30)
            data = r.stdout if r else b""
            if not data.startswith(b"\x89PNG"):
                self.log("📸 Capture impossible.", RED)
                return
            w, h = struct.unpack(">II", data[16:24])
            (BASE / "capture.png").write_bytes(data)
            self.log(f"📸 capture.png enregistrée ({w}x{h}). Les images de templates/ doivent "
                     f"venir de cette même résolution.", GREEN)
        threading.Thread(target=work, daemon=True).start()

    def auto_setup(self):
        def work():
            self.log("⚡ Configuration automatique...", YELLOW)
            if not self.deps_ok():
                self.do_install_deps()
            if not self.adb_path():
                self.do_download_adb()
            if self.adb_path() and not self.cfg.get("device"):
                self.do_detect()
            _s, _ok, missing, _n = self.missing_files()
            if missing:
                self.log("⚠ Fichiers manquants : " + ", ".join(missing), RED)
            self.log("⚡ Terminé.", YELLOW)
        self.run_bg(work)

    # ================= bot =================
    def launch(self):
        device = self.chosen_device()
        detached = False
        if self.proc:                                    # un farm de l'assistant tourne deja
            if not device or device == self.proc_device:
                return
            detached = True                              # 2 comptes : 2e farm sur l'autre instance, suivi par le registre
        if device:
            clash = encours.conflict(self.script_name(), device, self.chain_tokens())
            if clash and clash[1] != "instance occupée":
                messagebox.showwarning("Même script sur l'autre instance", f"{clash[1]} :\n{encours.describe(clash[0])}\n\n"
                                       "Deux comptes peuvent farmer en même temps, mais pas le même script.")
                return
            ext = clash[0] if clash else None
        else:
            ext = encours.read()
        if ext:
            if not messagebox.askyesno("Un farm tourne déjà", encours.describe(ext) +
                                       "\n\nL'arrêter et lancer celui-ci à la place ?"):
                return
            encours.stop(None, ext)
            self.log(f"⏹ Farm arrêté pour lancer celui-ci : {ext.get('label', '?')}", RED)
            self.watch_ext(once=True)
        probs = self.problems()
        if probs:
            messagebox.showwarning("Pas encore prêt", "À corriger avant de lancer :\n- " + "\n- ".join(probs))
            return
        hero = self.mode == "hero"
        chain = self.mode == "chain"
        if not hero:
            self.run_vals[self.mode] = self.runs
        self.cfg["max_runs"] = self.run_vals["story"]
        self.cfg["whm_runs"] = self.run_vals["whm"]
        self.cfg["aa_runs"] = self.run_vals["aa"]
        self.cfg["whm_level"] = self.whm_level
        self.cfg["whm_event"] = self.whm_event
        self.cfg["nightops_runs"] = self.run_vals["night"]
        self.cfg["supply_runs"] = self.run_vals["supply"]
        self.cfg["supply_mode"], self.cfg["supply_level"] = self.sup_mode, self.sup_level
        self.cfg["mode"] = self.mode
        self.cfg["hero_chars"] = self.chosen()
        self.cfg["pick_hero"] = self.pick
        self.cfg["stamina_refill"] = bool(self.refill_var.get())
        self.cfg["watchdog"] = bool(self.wd_var.get())
        save_cfg(self.cfg)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", MHA_SOURCE="assistant",
                   **({"MHA_DEVICE": device} if device else {}))
        script = self.script_info()[0]          # meme source que la verification des images : un seul endroit a tenir a jour
        cmd = [console_python(), "-u", str(script)]      # jamais pythonw : ses adb ouvriraient une fenetre chacun
        if chain:
            self.save_chain()
            cmd += [self.step_token(st) for st in self.chain_steps]
            if self.stop_err.get():
                cmd.append("--stop-on-error")
            cmd += [self.refill_flag(), self.wd_flag()]
        elif hero:
            cmd += ["--chars", ",".join(self.chosen())]
        elif self.mode == "supply":
            cmd += ["--mode", self.sup_mode, "--level", str(self.sup_level)]
        elif self.mode == "whm":
            cmd += ["--event", self.whm_event, "--level", str(self.whm_level)]
        elif self.mode == "aa":
            cmd += ["--bots", ",".join(self.aa_bots), "--reset-floor", str(self.aa_reset)]
            cmd += ["--reserve", ",".join(self.aa_reserved) or "aucun", "--reserve-floor", str(self.aa_resfloor)]
            cmd += ["--reserve2", ",".join(self.aa_reserved2) or "aucun", "--reserve2-floor", str(self.aa_resfloor2),
                    "--reset-fallback", str(self.aa_fallback)]
            if self.aa_floor:
                cmd += ["--floor", str(self.aa_floor)]
            cmd += [self.wd_flag()]
        if self.mode not in ("story", "chain", "aa"):
            cmd += ["--hero", self.pick]
        if self.mode in ("hero", "night", "supply", "whm"):
            cmd += [self.refill_flag(), self.wd_flag()]
        if detached:                                     # 2e farm (autre instance) : suivi dans la ligne « Autre farm »
            subprocess.Popen(cmd, cwd=BASE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, creationflags=NO_WINDOW)
            self.log(f"▶ Lancé sur {instances.label(device, self.inst_items)} (suivi dans la ligne « Autre farm »).", YELLOW)
            self.after(1500, lambda: self.watch_ext(once=True))
            return
        self.proc_device = device
        self.run_mode, self.run_chain = self.mode, list(self.chain_steps)   # suivi : independant de l'ecran (2 comptes)
        self.proc = subprocess.Popen(cmd, cwd=BASE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                     errors="replace", env=env, creationflags=NO_WINDOW)
        self.hero_done = set()
        self.total = len(self.chosen()) if hero else (1 if chain else self.runs)
        self.chain_idx, self.chain_n, self.chain_fail, self.chain_recap, self.step_mode = 0, len(self.chain_steps), 0, False, None
        self.done, self.t0 = 0, time.time()
        self.run_bar.config(maximum=max(1, self.total), value=0)
        self.launch_btn.style(GREY, TEXT, text="⏳  Le bot tourne...", enabled=False)
        self.stop_btn.style(RED, "#ffffff", enabled=True)
        self.action_lbl.config(text="🚀 Démarrage...", fg=YELLOW)
        self.update_stats()
        if chain:
            self.log(f"▶ Enchaînement de {len(self.chain_steps)} étape(s). Plus Ultra !", YELLOW)
            for i, st in enumerate(self.chain_steps, 1):
                self.log(f"   {i}. {self.step_text(st)}")
        elif self.mode == "supply":
            self.log(f"▶ Supply {SUPPLY_MODES[self.sup_mode]} niveau {self.sup_level} : {self.total} lancement(s). Plus Ultra !", YELLOW)
        elif self.mode == "night":
            self.log(f"▶ Night Ops : {self.total} lancement(s). Plus Ultra !", YELLOW)
        elif self.mode == "whm":
            self.log(f"▶ Événement, {WHM_EVENTS[self.whm_event][0]} {self.whm_level} : {self.total} multi-run(s). Plus Ultra !", YELLOW)
        elif self.mode == "aa":
            self.log(f"▶ Agency Assessment : {self.total} combat(s), bots {' + '.join(AA_BOTS[b] for b in self.aa_bots) or 'aucun'}, "
                     f"reset {'au floor ' + str(self.aa_reset) if self.aa_reset else 'jamais'}. Plus Ultra !", YELLOW)
        elif hero:
            self.log(f"▶ Hero Trial : {', '.join(self.chosen())}. Plus Ultra !", YELLOW)
        else:
            self.log(f"▶ Lancement : {self.total} run(s). Plus Ultra !", YELLOW)
        if self.mode not in ("story", "chain", "aa"):
            self.log(f"🦸 Perso qui combat : {hz.pretty(self.pick)}", YELLOW)
        if self.mode in ("hero", "night", "supply", "whm", "chain"):
            self.log("🛡️ Chien de garde : " + ("ACTIVÉ (relance le jeu si maintenance / bloqué 5 min)" if self.wd_var.get() else "désactivé"), YELLOW)
            self.log("🔋 Recharge de stamina : " + ("ACTIVÉE (code cadeau x15 quand il n'y en a plus)" if self.refill_var.get() else "désactivée"), YELLOW)
        threading.Thread(target=self.read_proc, args=(self.proc,), daemon=True).start()
        self.tick()

    def read_proc(self, proc):
        for line in proc.stdout:
            self.after(0, self.on_line, line.rstrip())
        code = proc.wait()
        self.after(0, self.on_end, code)

    def on_line(self, line):
        if not line:
            return
        self.log(line)
        if "WATCHDOG" in line or "watchdog : serveur en MAINTENANCE" in line:
            self.action_lbl.config(text="🛡️ Maintenance / relance du jeu..." if "MAINTENANCE" in line or "WATCHDOG" in line else "", fg=YELLOW)
        if "RECHARGE DE STAMINA" in line or "recharge terminee" in line:
            self.action_lbl.config(text="🔋 Recharge de stamina (code cadeau)..." if "RECHARGE DE" in line else "🔋 Stamina rechargée", fg=YELLOW)
        if "defilement" in line:                 # le bot cherche le perso voulu en faisant defiler la liste
            self.action_lbl.config(text="↕ Recherche du perso : défilement de la liste", fg=YELLOW)
        if self.run_mode == "chain":
            if self.on_chain_line(line):
                return
        if self.run_mode == "hero" or (self.run_mode == "chain" and self.step_mode == "hero"):
            self.on_hero_line(line)
            return
        m = re.search(r"\] (\w+) \(.*\| (?:multi-)?run (\d+)/(\d+)", line)
        if m:
            name, done, total = m.group(1), int(m.group(2)), int(m.group(3))
            self.done, self.total = done, total
            self.action_lbl.config(text=FRIENDLY.get(name, name), fg=YELLOW)
            self.run_bar.config(maximum=total, value=done)
            self.update_stats()

    def on_chain_line(self, line):
        """Lignes de enchainer.py. True si la ligne est traitee (inutile de la passer au parseur du mode)."""
        m = re.match(r"=+ Etape (\d+)/(\d+) : (.*?) =+", line)
        if m:
            self.chain_idx, self.chain_n = int(m.group(1)), int(m.group(2))
            st = self.run_chain[self.chain_idx - 1] if self.chain_idx <= len(self.run_chain) else None
            self.step_mode = st["mode"] if st else None
            self.hero_done = set()
            self.done = 0
            self.total = len(st["chars"].split(",")) if st and st["mode"] == "hero" else (st["runs"] if st else 1)
            self.run_bar.config(maximum=max(1, self.total), value=0)
            self.paint_steps(self.chain_idx - 1)
            self.action_lbl.config(text=f"🔗 Étape {self.chain_idx}/{self.chain_n} : {self.step_text(st) if st else m.group(3)}", fg=YELLOW)
            self.update_stats()
            return True
        if line.startswith("===== Recapitulatif"):
            self.chain_recap = True
            return True
        if self.chain_recap and re.match(r"\s+(OK|ECHEC)\b", line):
            if "ECHEC" in line:
                self.chain_fail += 1
            return True
        if line.startswith("(!) Etape"):
            self.action_lbl.config(text="⚠ " + line[4:90], fg=RED)
            return True
        return False

    def on_hero_line(self, line):
        m = re.search(r"\] perso : (\w+)", line)
        if m:
            self.action_lbl.config(text=f"🎯 Perso en cours : {m.group(1).capitalize()}", fg=YELLOW)
        m = re.search(r"\] combat (\d+)/(\d+) \((\w+)\)", line)
        if m:
            self.action_lbl.config(text=f"⚔ {m.group(3).capitalize()} : combat {m.group(1)}", fg=YELLOW)
        m = re.search(r"\] (\w+) : 0/2", line) or re.search(r"\] (\w+) termine -> Exit Stage", line)
        if m and m.group(1) in self.chars:
            self.hero_done.add(m.group(1))
            self.done = len(self.hero_done)
            self.run_bar.config(value=self.done)
            self.log(f"✔ {m.group(1).capitalize()} terminé", GREEN)
        self.update_stats()

    def update_stats(self):
        el = int(time.time() - self.t0) if self.t0 else 0
        hero = self.run_mode == "hero" or (self.run_mode == "chain" and self.step_mode == "hero")
        label = "Persos" if hero else "Run"
        pre = f"Étape {self.chain_idx}/{self.chain_n}   ·   " if self.run_mode == "chain" and self.chain_idx else ""
        self.stats_lbl.config(text=f"{pre}{label} {self.done}/{self.total}   ·   ⏱ {el // 60:02d}:{el % 60:02d}")

    def tick(self):
        if self.proc:
            self.update_stats()
            self.after(1000, self.tick)

    def on_end(self, code):
        finished = (self.chain_recap and self.chain_fail == 0) if self.run_mode == "chain" else self.done >= self.total
        self.proc = None
        self.stop_btn.style(GREY, TEXT, enabled=False)
        if self.run_mode == "chain":
            self.paint_steps()
            if finished:
                self.action_lbl.config(text=f"🎉 Enchaînement terminé : {self.chain_n} étape(s) réussie(s) !", fg=GREEN)
                self.log("🎉 Terminé !", GREEN)
            elif self.chain_recap:
                self.action_lbl.config(text=f"⚠ Enchaînement terminé avec {self.chain_fail} étape(s) en échec (voir le journal).", fg=RED)
            else:
                self.action_lbl.config(text="⏹ Enchaînement arrêté.", fg=RED)
        elif finished:
            self.action_lbl.config(
                text=(f"🎉 Mission accomplie : {self.done} perso(s) terminé(s) !" if self.run_mode == "hero"
                      else f"🎉 Mission accomplie : {self.done} run(s) terminé(s) !"), fg=GREEN)
            self.log("🎉 Terminé !", GREEN)
        else:
            self.action_lbl.config(text="⏹ Arrêté (écran inattendu, carte introuvable ou bouton Stop).", fg=RED)
        self.update_stats()
        self.refresh_all()
        self.launch_btn.style(YELLOW, DARK, text="▶  LANCER  ·  PLUS ULTRA !")

    def stop(self):
        if self.proc:
            self.kill_proc()
            self.log("⏹ Arrêt demandé...", RED)
        elif self.ext:
            ext = self.ext

            def work():
                encours.stop()
                self.after(0, lambda: (self.log(f"⏹ Farm arrêté : {ext.get('label', '?')}", RED), self.watch_ext(once=True)))
            self.stop_btn.style(GREY, TEXT, enabled=False)
            threading.Thread(target=work, daemon=True).start()

    def watch_ext(self, once=False):
        """Toutes les 2 s : un farm lance depuis Discord / le planning / a la main tourne-t-il ? -> barre du bas + Stop."""
        try:
            recs = encours.read_all()
            mine = self.proc.pid if self.proc else None
            self.others = [r for r in recs if r.get("pid") != mine]
            multi = len(self.inst_items) >= 2
            if self.proc or (multi and self.others):     # 2 comptes : les autres farms sur leur propre ligne
                rec = None
                self.show_others()
                if not self.proc and multi:              # on peut lancer sur l'autre instance
                    self.launch_btn.style(YELLOW, DARK, text="▶  LANCER  ·  PLUS ULTRA !")
                    self.stop_btn.style(GREY, TEXT, enabled=False)
                elif self.proc and multi:
                    self.launch_btn.style(BLUE, DARK, text="▶  Lancer sur l'autre instance", enabled=True)
            else:
                self.show_others()
                rec = self.others[0] if self.others else None
            if rec:
                self.ext = rec
                total = rec.get("total") or 1
                self.launch_btn.style(GREY, TEXT, text="⏳  Un farm tourne déjà", enabled=False)
                self.stop_btn.style(RED, "#ffffff", enabled=True)
                src = encours.SOURCES.get(rec.get("source"), "?")
                self.action_lbl.config(text=f"⏳ Farm lancé depuis {src} : {rec.get('label', '?')}", fg=YELLOW)
                self.run_bar.config(maximum=max(1, total), value=rec.get("done") or 0)
                self.stats_lbl.config(text=encours.describe(rec) + (f"   ·   {rec['last'][-70:]}" if rec.get("last") else ""))
            elif self.ext and not self.proc:
                ext, self.ext = self.ext, None
                self.stop_btn.style(GREY, TEXT, enabled=False)
                self.launch_btn.style(GREY, TEXT, text="▶  LANCER  ·  PLUS ULTRA !")
                self.action_lbl.config(text=f"✔ Le farm lancé depuis {encours.SOURCES.get(ext.get('source'), '?')} "
                                            f"est terminé ou arrêté : {ext.get('label', '?')}", fg=MUTED)
                self.run_bar.config(value=0)
                self.log(f"✔ Farm lancé depuis {encours.SOURCES.get(ext.get('source'), '?')} terminé ou arrêté : "
                         f"{ext.get('label', '?')}", MUTED)
                self.refresh_all()
        except Exception:                                    # noqa: BLE001 - la surveillance ne doit jamais casser l'assistant
            pass
        if not once:
            self.after(2000, self.watch_ext)

    # ---------------- 2 comptes ----------------
    def refresh_instances(self):
        def work():
            try:
                items = instances.listing()
            except Exception:                                # noqa: BLE001
                items = []
            self.after(0, lambda: self.show_instances(items))
        threading.Thread(target=work, daemon=True).start()

    def show_instances(self, items):
        self.inst_items = items
        if len(items) >= 2:
            cur = self.inst_combo.get()
            vals = [instances.label(i["serial"], items) for i in items]
            self.inst_combo.config(values=vals)
            self.inst_combo.current(vals.index(cur) if cur in vals else 0)
            self.inst_row.pack(fill="x", pady=(4, 0), before=self.stats_lbl)
        else:
            self.inst_row.pack_forget()

    def locked(self):
        """Reglages verrouilles pendant un farm de l'assistant ; 2 comptes : non (on prepare le farm de l'autre instance)."""
        return bool(self.proc) and len(self.inst_items) < 2

    def chosen_device(self):
        """Instance choisie (2 comptes) ou None (une seule instance : comportement habituel)."""
        if len(self.inst_items) < 2 or self.inst_combo.current() < 0:
            return None
        return self.inst_items[self.inst_combo.current()]["serial"]

    def script_name(self):
        return {"story": "histoire", "hero": "herotrial", "night": "nightops", "supply": "supply", "whm": "whm", "aa": "aa",
                "chain": "enchainement"}.get(self.mode, self.mode)

    def chain_tokens(self):
        return [self.step_token(st) for st in self.chain_steps] if self.mode == "chain" else []

    def show_others(self):
        if self.others:
            self.others_lbl.config(text="📱 Autre farm : " + "   |   ".join(encours.describe(r) for r in self.others))
            self.others_row.pack(fill="x", pady=(4, 0))
        else:
            self.others_row.pack_forget()

    def stop_others(self):
        others = list(self.others)

        def work():
            for r in others:
                encours.stop(None, r)
            self.after(0, lambda: (self.log("⏹ Autre farm arrêté : " + ", ".join(r.get("label", "?") for r in others), RED),
                                   self.watch_ext(once=True)))
        threading.Thread(target=work, daemon=True).start()

    def on_close(self):
        if self.proc:
            self.kill_proc()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
