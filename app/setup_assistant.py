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
import json
import os
import re
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
        self.refresh_all()

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
        self.auto_btn = Btn(head, "⚡ Tout configurer automatiquement", self.auto_setup,
                            bg=BLUE, size=10, padx=14, pady=8)
        self.auto_btn.pack(side="right", pady=(6, 0))

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
        self.aa_res_lbl = tk.Label(rrow3, text="", bg=CARD, fg=YELLOW, font=F(9, True), anchor="w")
        self.aa_res_lbl.pack(side="left", padx=(12, 0))
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
                 "« Floor actuel » : à donner seulement si le bot n'arrive pas à le lire (auto = il le lit). Images : templates_aa/.",
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
        for key, label in (("supply", "📦 Supply"), ("night", "🌙 Night Ops"), ("hero", "🏆 Hero Trial")):
            b = Btn(top, label, lambda k=key: self.set_chain_type(k), bg=GREY, fg=TEXT, size=10, padx=12, pady=4)
            b.pack(side="left", padx=(8, 0))
            self.chain_btns[key] = b
        tk.Label(self.chain_opts, text="Règle l'étape ci-dessous (mode, niveau, lancements, perso), puis clique sur « Ajouter ». "
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
        if self.proc:
            return
        self.sel.symmetric_difference_update({name})
        self.paint_chip(name)
        self.save_hero_sel()
        self.sel_lbl.config(text=self.sel_text())
        self.refresh_all()

    def select_all(self, on):
        if self.proc:
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
        if self.proc:
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
        if self.proc:
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
        self.aa_res_lbl.config(text=(", ".join(AA_CHAR_NAMES.get(n, n.capitalize()) for n in self.aa_reserved) or "aucun perso réservé"))
        for n, bt in self.aa_res_btns.items():
            on = n in self.aa_reserved
            bt.style(YELLOW if on else GREY, DARK if on else TEXT)
        self.aa_lbl.config(text=" + ".join(AA_BOTS[b] for b in self.aa_bots) or "sans bot")

    def save_aa(self):
        self.cfg["aa_bots"], self.cfg["aa_reset_floor"] = list(self.aa_bots), self.aa_reset
        self.aa_resfloor = max(1, self.aa_resfloor)
        self.cfg["aa_reserved"], self.cfg["aa_reserve_floor"] = list(self.aa_reserved), self.aa_resfloor
        if self.aa_floor:
            self.cfg["aa_floor"] = self.aa_floor
        else:
            self.cfg.pop("aa_floor", None)
        save_cfg(self.cfg)
        self.paint_aa()

    def toggle_aa_bot(self, b):
        if self.proc:
            return
        if b in self.aa_bots:
            self.aa_bots.remove(b)
        elif len(self.aa_bots) < 2:
            self.aa_bots.append(b)
        else:
            self.aa_bots = [self.aa_bots[1], b]          # 2 maximum : le plus ancien cede sa place
        self.save_aa()

    def toggle_aa_res(self, n):
        if self.proc:
            return
        if n in self.aa_reserved:
            self.aa_reserved.remove(n)
        else:
            self.aa_reserved.append(n)
        self.save_aa()

    def step_aa(self, what, d):
        if self.proc:
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
        if self.proc:
            return
        self.whm_event = ev
        self.cfg["whm_event"] = ev
        save_cfg(self.cfg)
        self.paint_whm()
        self.refresh_all()

    def set_whm_level(self, lv):
        if self.proc:
            return
        self.whm_level = lv
        self.cfg["whm_level"] = lv
        save_cfg(self.cfg)
        self.paint_whm()
        self.refresh_all()

    def set_sup_level(self, lv):
        if self.proc or lv > SUPPLY_MAX_LEVEL[self.sup_mode]:
            return
        self.sup_level = lv
        self.paint_supply()
        self.save_supply()

    def save_supply(self):
        self.cfg["supply_mode"], self.cfg["supply_level"] = self.sup_mode, self.sup_level
        save_cfg(self.cfg)

    # ================= enchainement =================
    def step_text(self, st):
        who = f"  ·  🦸 {hz.pretty(st['hero'])}" if st.get("hero") else ""
        if st["mode"] == "supply":
            return f"📦 Supply {SUPPLY_MODES[st['supply']]} niv. {st['level']}  ×{st['runs']}{who}"
        if st["mode"] == "nightops":
            return f"🌙 Night Ops  ×{st['runs']}{who}"
        return f"🏆 Hero Trial : {', '.join(c.capitalize() for c in st['chars'].split(','))}{who}"

    def step_token(self, st):
        who = f":{st['hero']}" if st.get("hero") else ""
        if st["mode"] == "supply":
            return f"supply:{st['supply']}:{st['level']}:{st['runs']}{who}"
        if st["mode"] == "nightops":
            return f"nightops:{st['runs']}{who}"
        return f"hero:{st['chars']}{who}"

    def save_chain(self):
        self.cfg["chain_steps"] = [{k: v for k, v in st.items() if v is not None} for st in self.chain_steps]
        self.cfg["chain_type"] = self.chain_type
        self.cfg["chain_stop_on_error"] = bool(self.stop_err.get())
        save_cfg(self.cfg)

    def set_chain_type(self, t):
        if self.proc or t == self.chain_type:
            return
        self.run_vals["chain"] = self.runs
        self.chain_type = t
        self.save_chain()
        self.apply_mode()

    def add_step(self):
        if self.proc:
            return
        t = self.chain_type
        if t == "supply":
            st = dict(mode="supply", supply=self.sup_mode, level=self.sup_level, runs=self.runs, hero=self.pick)
        elif t == "night":
            st = dict(mode="nightops", runs=self.runs, hero=self.pick)
        else:
            if not self.chosen():
                messagebox.showwarning("Hero Trial", "Coche au moins un perso à farmer.")
                return
            st = dict(mode="hero", chars=",".join(self.chosen()), hero=self.pick)
        self.chain_steps.append(st)
        self.save_chain()
        self.paint_steps()
        self.refresh_all()

    def move_step(self, i, d):
        j = i + d
        if self.proc or not 0 <= j < len(self.chain_steps):
            return
        self.chain_steps[i], self.chain_steps[j] = self.chain_steps[j], self.chain_steps[i]
        self.save_chain()
        self.paint_steps()

    def del_step(self, i):
        if self.proc:
            return
        del self.chain_steps[i]
        self.save_chain()
        self.paint_steps()
        self.refresh_all()

    def clear_steps(self):
        if self.proc or not self.chain_steps:
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
                if not (CHAR_TPL / f"{st.get('hero') or self.pick}.png").exists():
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
        if self.proc or mode == self.mode:
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
        hero = self.mode == "hero" or (chain and self.chain_type == "hero")
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
            for key, b in self.chain_btns.items():
                b.style(YELLOW if key == self.chain_type else GREY, DARK if key == self.chain_type else TEXT)
            self.chain_opts.pack(fill="x", after=self.mode_row)
            if self.chain_type == "supply":
                self.paint_supply()
                self.supply_opts.pack(fill="x", after=self.chain_opts)
                self.story_opts.pack(fill="x", after=self.supply_opts)
            elif self.chain_type == "night":
                self.story_opts.pack(fill="x", after=self.chain_opts)
            else:
                self.hero_opts.pack(fill="x", after=self.chain_opts)
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
        if not self.proc:
            if n == 4:
                self.launch_btn.style(YELLOW, DARK)
                self.action_lbl.config(
                    text=("Tout est vert. Reste sur le menu principal et lance !" if self.mode in ("hero", "night", "supply", "chain")
                          else "Tout est vert. Mets ton perso en ville et lance !"), fg=GREEN)
            else:
                self.launch_btn.style(GREY, TEXT)
                self.action_lbl.config(text="Prêt quand toutes les étapes sont vertes.", fg=MUTED)

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
        if self.proc:
            return
        probs = self.problems()
        if probs:
            messagebox.showwarning("Pas encore prêt", "À corriger avant de lancer :\n- " + "\n- ".join(probs))
            return
        hero = self.mode == "hero"
        chain = self.mode == "chain"
        if not hero and not (chain and self.chain_type == "hero"):
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
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        script = self.script_info()[0]          # meme source que la verification des images : un seul endroit a tenir a jour
        cmd = [sys.executable, "-u", str(script)]
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
            if self.aa_floor:
                cmd += ["--floor", str(self.aa_floor)]
            cmd += [self.wd_flag()]
        if self.mode not in ("story", "chain", "aa"):
            cmd += ["--hero", self.pick]
        if self.mode in ("hero", "night", "supply", "whm"):
            cmd += [self.refill_flag(), self.wd_flag()]
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
        if self.mode == "chain":
            if self.on_chain_line(line):
                return
        if self.mode == "hero" or (self.mode == "chain" and self.step_mode == "hero"):
            self.on_hero_line(line)
            return
        m = re.search(r"\] (\w+) \(.*\| run (\d+)/(\d+)", line)
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
            st = self.chain_steps[self.chain_idx - 1] if self.chain_idx <= len(self.chain_steps) else None
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
        hero = self.mode == "hero" or (self.mode == "chain" and self.step_mode == "hero")
        label = "Persos" if hero else "Run"
        pre = f"Étape {self.chain_idx}/{self.chain_n}   ·   " if self.mode == "chain" and self.chain_idx else ""
        self.stats_lbl.config(text=f"{pre}{label} {self.done}/{self.total}   ·   ⏱ {el // 60:02d}:{el % 60:02d}")

    def tick(self):
        if self.proc:
            self.update_stats()
            self.after(1000, self.tick)

    def on_end(self, code):
        finished = (self.chain_recap and self.chain_fail == 0) if self.mode == "chain" else self.done >= self.total
        self.proc = None
        self.stop_btn.style(GREY, TEXT, enabled=False)
        if self.mode == "chain":
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
                text=(f"🎉 Mission accomplie : {self.done} perso(s) terminé(s) !" if self.mode == "hero"
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

    def on_close(self):
        if self.proc:
            self.kill_proc()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
