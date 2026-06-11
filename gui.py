import os
import random
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import Image, ImageTk, ImageGrab, ImageOps, ImageFilter

from astar import aStar, isSolvable, move as apply_move, runMoves, GOAL_ROW, GOAL_COL
import Move

try:
    from psearch import Recorder, Run
    HAS_PSEARCH = True
except Exception:
    HAS_PSEARCH = False

RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")

try:
    import pytesseract
    pytesseract.get_tesseract_version()
    HAS_OCR = True
except Exception:
    HAS_OCR = False

END_STATE = list(range(1, 16)) + [0]
MOVE = Move.Move

# Catppuccin Mocha — minimal palette
BG = "#11111b"
PANEL = "#181825"
SURFACE = "#1e1e2e"
TEXT = "#cdd6f4"
MUTED = "#6c7086"
SUBTLE = "#9399b2"
PRIMARY = "#89b4fa"
SUCCESS = "#a6e3a1"
WARN = "#f9e2af"
DANGER = "#f38ba8"
TILE = "#cdd6f4"
TILE_TEXT = "#1e1e2e"
TILE_GOAL = "#a6e3a1"
TILE_GOAL_TEXT = "#1e1e2e"
TILE_BLANK = "#181825"

TILE_SIZE = 76
TILE_PAD = 5
RADIUS = 14
SLIDE_MS = 90          # one tile slide duration
FRAME_MS = 16          # ~60 fps
INTER_MOVE_MS = 10     # tiny gap between moves

# Heatmap: tile color by Manhattan distance from its goal
HEATMAP = [
    "#a6e3a1",  # 0 - green (home)
    "#94e2d5",  # 1 - teal
    "#f9e2af",  # 2 - yellow
    "#fab387",  # 3 - peach
    "#eba0ac",  # 4 - maroon
    "#f38ba8",  # 5+ red
]

# Path-spine: dot color by move direction
SPINE_COLORS = {
    "UP":    "#89dceb",  # sky
    "DOWN":  "#cba6f7",  # mauve
    "LEFT":  "#f9e2af",  # yellow
    "RIGHT": "#a6e3a1",  # green
}

MINI_TILE = 26
MINI_PAD = 2
SPINE_DOT = 5
SPINE_GAP = 1
SPINE_WIDTH = 220

# Path window cards
CARD_TILE = 22
CARD_PAD = 2
CARD_W = CARD_TILE * 4 + CARD_PAD * 5 + 16
CARD_H = CARD_W + 60


class PuzzleGUI:
    def __init__(self, root):
        self.root = root
        root.title("15-Puzzle")
        root.configure(bg=BG)
        root.resizable(True, True)
        root.minsize(880, 760)
        root.geometry("980x820")

        self.board = list(END_STATE)
        self.solution_moves = []
        self.move_count = 0
        self.animating = False
        self.playing = False
        self._edit_widget = None
        self._fix_queue = []
        self._solve_t0 = 0.0
        self._last_threshold = None
        self._solver_snapshot = None
        self._polling_solver = False
        self._solve_start_state = None
        self._path_window = None
        self._tree_window = None
        self.search_record = []
        self._total_nodes = 0
        self.search_stats = {}
        self._record_mode = "sampled"  # off / sampled / full
        self._run = None               # currently loaded psearch.Run
        self._selected_nid = 0
        self._tree_canvas = None
        self._tree_node_items = {}     # canvas item id -> nid
        self._tree_positions = {}      # nid -> (x, y)
        self._detail_widgets = {}      # holders for live-updating detail pane

        try:
            os.makedirs(RUNS_DIR, exist_ok=True)
        except Exception:
            pass

        self._build_ui()
        self._refresh()

    # ---------- UI construction ----------

    def _build_ui(self):
        # Centered container so content stays put when the window grows.
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        container = tk.Frame(self.root, bg=BG)
        container.grid(row=0, column=0)
        self._content = container

        header = tk.Frame(container, bg=BG)
        header.grid(row=0, column=0, columnspan=2, sticky="ew",
                    padx=24, pady=(16, 6))
        tk.Label(
            header, text="15-Puzzle",
            font=("Helvetica", 22, "bold"), bg=BG, fg=TEXT,
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Paste a screenshot, play with arrow keys, "
                 "or Solve for an optimal A* solution.",
            font=("Helvetica", 11), bg=BG, fg=MUTED,
        ).pack(anchor="w", pady=(2, 0))

        board_wrap = tk.Frame(container, bg=BG)
        board_wrap.grid(row=1, column=0, sticky="n", padx=(24, 12), pady=4)

        size = TILE_SIZE * 4 + TILE_PAD * 5
        self.canvas = tk.Canvas(
            board_wrap, width=size, height=size,
            bg=PANEL, highlightthickness=0,
        )
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._canvas_click)

        side = tk.Frame(container, bg=BG)
        side.grid(row=1, column=1, sticky="n", padx=(12, 24), pady=4)

        self._section(side, "INPUT")
        row = tk.Frame(side, bg=BG); row.pack(fill="x")
        self._btn(row, "Paste", self.paste_image, PRIMARY, side="left")
        self._btn(row, "Shuffle", self.randomize, PRIMARY, ghost=True, side="left")
        self._btn(row, "Reset", self.reset, PRIMARY, ghost=True, side="left")

        row2 = tk.Frame(side, bg=BG); row2.pack(fill="x", pady=(4, 0))
        self._btn(row2, "Load Run", self.load_run, PRIMARY, ghost=True, side="left")
        self._save_run_btn = self._btn(row2, "Save Run", self.save_run, SUCCESS,
                                       ghost=True, side="left")

        self._section(side, "SOLVE", pad_top=12)
        row = tk.Frame(side, bg=BG); row.pack(fill="x")
        self._btn(row, "Solve", self.solve, SUCCESS, side="left")
        self._btn(row, "Animate", self.animate, WARN, ghost=True, side="left")
        self._btn(row, "Auto-play", self.auto_play, DANGER, ghost=True, side="left")

        # Speedrun controls: which preset, how long the countdown, and a button
        # that rerolls scrambles until it finds a shallow (low-move) one.
        speed_row = tk.Frame(side, bg=BG); speed_row.pack(fill="x", pady=(6, 0))
        tk.Label(speed_row, text="speed", font=("Menlo", 9),
                 bg=BG, fg=MUTED).pack(side="left", padx=(0, 6))
        self._speed_var = tk.StringVar(value="Fast")
        for label in self.SPEED_PRESETS:
            tk.Radiobutton(
                speed_row, text=label, variable=self._speed_var, value=label,
                font=("Menlo", 9), bg=BG, fg=SUBTLE,
                activebackground=BG, activeforeground=TEXT,
                selectcolor=PANEL, highlightthickness=0,
                borderwidth=0, indicatoron=True,
            ).pack(side="left", padx=1)

        cd_row = tk.Frame(side, bg=BG); cd_row.pack(fill="x", pady=(2, 0))
        tk.Label(cd_row, text="countdown", font=("Menlo", 9),
                 bg=BG, fg=MUTED).pack(side="left", padx=(0, 6))
        self._countdown_var = tk.IntVar(value=3)
        for v in (0, 1, 3, 5):
            tk.Radiobutton(
                cd_row, text=f"{v}s", variable=self._countdown_var, value=v,
                font=("Menlo", 9), bg=BG, fg=SUBTLE,
                activebackground=BG, activeforeground=TEXT,
                selectcolor=PANEL, highlightthickness=0,
                borderwidth=0, indicatoron=True,
            ).pack(side="left", padx=1)

        easy_row = tk.Frame(side, bg=BG); easy_row.pack(fill="x", pady=(6, 0))
        self._btn(easy_row, "Reroll ≤", self.shuffle_until_easy,
                  SUCCESS, ghost=True, side="left")
        self._easy_target_var = tk.IntVar(value=30)
        tk.Spinbox(easy_row, from_=15, to=60, increment=1, width=4,
                   textvariable=self._easy_target_var,
                   font=("Menlo", 11, "bold"),
                   bg=PANEL, fg=TEXT, buttonbackground=PANEL,
                   relief="flat", highlightthickness=0,
                   ).pack(side="left", padx=(4, 0))
        tk.Label(easy_row, text="moves", font=("Menlo", 9),
                 bg=BG, fg=MUTED).pack(side="left", padx=(4, 0))

        # Record-mode picker (sampled is plenty for the visualizer; "full" warns)
        mode_row = tk.Frame(side, bg=BG); mode_row.pack(fill="x", pady=(6, 0))
        tk.Label(mode_row, text="record", font=("Menlo", 9),
                 bg=BG, fg=MUTED).pack(side="left", padx=(0, 6))
        self._record_mode_var = tk.StringVar(value=self._record_mode)
        for label in ("off", "sampled", "full"):
            tk.Radiobutton(
                mode_row, text=label, variable=self._record_mode_var, value=label,
                command=self._on_record_mode_change,
                font=("Menlo", 9), bg=BG, fg=SUBTLE,
                activebackground=BG, activeforeground=TEXT,
                selectcolor=PANEL, highlightthickness=0,
                borderwidth=0, indicatoron=True,
            ).pack(side="left", padx=2)

        # Bottom slot: either the image preview OR the solver activity panel
        self.bottom_slot = tk.Frame(side, bg=BG)
        self.bottom_slot.pack(fill="x", pady=(14, 0))

        self.preview = tk.Label(
            self.bottom_slot, bg=PANEL, fg=MUTED,
            width=22, height=8, padx=6, pady=6,
            font=("Helvetica", 10), justify="center",
            text="No image\n\n"
                 + ("OCR ready" if HAS_OCR else "tesseract not found"),
        )
        self.preview.pack(fill="x")

        self.solver_panel = self._build_solver_panel(self.bottom_slot)

        footer = tk.Frame(container, bg=BG)
        footer.grid(row=2, column=0, columnspan=2, sticky="ew",
                    padx=24, pady=(8, 4))
        self.status = tk.Label(
            footer, text="Ready",
            font=("Helvetica", 11, "bold"),
            bg=BG, fg=MUTED, anchor="w",
        )
        self.status.pack(side="left")
        counter_wrap = tk.Frame(footer, bg=BG)
        counter_wrap.pack(side="right")
        self.counter = tk.Label(
            counter_wrap, text="",
            font=("Helvetica", 10), bg=BG, fg=SUBTLE,
        )
        self.counter.pack(side="left")
        self.tree_link = tk.Label(
            counter_wrap, text="",
            font=("Helvetica", 10), bg=BG, fg=PRIMARY,
            cursor="hand2",
        )
        self.tree_link.pack(side="left", padx=(8, 0))
        self.path_link = tk.Label(
            counter_wrap, text="",
            font=("Helvetica", 10), bg=BG, fg=PRIMARY,
            cursor="hand2",
        )
        self.path_link.pack(side="left", padx=(8, 0))
        self.path_link.bind("<Button-1>", lambda e: self._open_path_window())
        self.tree_link.bind("<Button-1>", lambda e: self._open_tree_window())

        hint_row = tk.Frame(container, bg=BG)
        hint_row.grid(row=3, column=0, columnspan=2, sticky="ew",
                      padx=24, pady=(0, 14))
        tk.Label(
            hint_row,
            text="←↑↓→ play  ·  click a tile to edit  ·  ↵ solve  ·  ⌘V paste",
            font=("Helvetica", 10), bg=BG, fg=MUTED,
        ).pack(anchor="w")

        self.root.bind("<Key>", self._on_key)
        self.root.bind("<Command-v>", lambda e: self.paste_image())
        self.root.bind("<Control-v>", lambda e: self.paste_image())

    def _section(self, parent, text, pad_top=0):
        tk.Label(
            parent, text=text,
            font=("Helvetica", 9, "bold"),
            bg=BG, fg=MUTED,
        ).pack(anchor="w", pady=(pad_top, 5))

    @staticmethod
    def _hex_to_rgb(c):
        return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)

    @staticmethod
    def _rgb_to_hex(r, g, b):
        return f"#{r:02x}{g:02x}{b:02x}"

    def _tint(self, color, ratio=0.18):
        """Blend `color` into the SURFACE tone — a subtle, accent-flavoured fill."""
        sr, sg, sb = self._hex_to_rgb(SURFACE)
        cr, cg, cb = self._hex_to_rgb(color)
        return self._rgb_to_hex(
            int(sr + (cr - sr) * ratio),
            int(sg + (cg - sg) * ratio),
            int(sb + (cb - sb) * ratio),
        )

    def _lighten(self, color, amount=0.14):
        r, g, b = self._hex_to_rgb(color)
        return self._rgb_to_hex(
            min(255, int(r + (255 - r) * amount)),
            min(255, int(g + (255 - g) * amount)),
            min(255, int(b + (255 - b) * amount)),
        )

    def _btn(self, parent, text, cmd, color=PRIMARY, ghost=False, side=None):
        """Custom button (Frame + Label) so colors render reliably on macOS Tk."""
        if ghost:
            bg = self._tint(color, 0.18)
            fg = color
            hover_bg = self._tint(color, 0.34)
            press_bg = self._tint(color, 0.46)
        else:
            bg = color
            fg = BG
            hover_bg = self._lighten(color, 0.12)
            press_bg = color  # full saturation on press

        wrap = tk.Frame(parent, bg=bg, cursor="hand2",
                        highlightthickness=0, bd=0)
        label = tk.Label(
            wrap, text=text,
            font=("Helvetica", 11, "bold"),
            bg=bg, fg=fg, cursor="hand2",
            padx=16, pady=10,
        )
        label.pack(fill="both", expand=True)

        state = {"hovered": False}

        def set_bg(c):
            wrap.config(bg=c)
            label.config(bg=c)

        def on_enter(_e):
            state["hovered"] = True
            set_bg(hover_bg)

        def on_leave(_e):
            state["hovered"] = False
            set_bg(bg)

        def on_press(_e):
            set_bg(press_bg)

        def on_release(_e):
            set_bg(hover_bg if state["hovered"] else bg)
            cmd()

        for w in (wrap, label):
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)
            w.bind("<ButtonPress-1>", on_press)
            w.bind("<ButtonRelease-1>", on_release)

        if side:
            wrap.pack(side=side, expand=True, fill="x", padx=3)
        else:
            wrap.pack(fill="x", pady=3)
        return wrap

    # ---------- search-tree visualizer ----------

    def _render_search_aggregates(self, parent, stats, compact=False):
        """compact=True stacks the panels vertically into a sidebar."""
        import math
        wrap = tk.Frame(parent, bg=BG)
        if compact:
            wrap.pack(fill="both", expand=True, padx=0, pady=0)
        else:
            wrap.pack(fill="x", padx=20, pady=(0, 12))
        tk.Label(wrap, text="ALL NODES — search profile",
                 font=("Menlo", 9, "bold"), bg=BG, fg=MUTED).pack(anchor="w", pady=(0, 6))

        total = stats.get('total_nodes', 0)
        iters = stats.get('iterations', [])
        depths = stats.get('nodes_per_depth', [])

        # Container — vertical stack in compact mode, side-by-side otherwise.
        charts = tk.Frame(wrap, bg=BG)
        charts.pack(fill="both", expand=True)

        # ----- iteration bars -----
        iter_frame = tk.Frame(charts, bg=PANEL, padx=10, pady=10)
        if compact:
            iter_frame.pack(fill="x", pady=(0, 8))
        else:
            iter_frame.pack(side="left", fill="both", expand=True, padx=(0, 6))
        tk.Label(iter_frame,
                 text=f"by iteration · {len(iters)} thresholds · {total:,} nodes",
                 font=("Menlo", 9), bg=PANEL, fg=MUTED).pack(anchor="w")
        bar_h = 12 if compact else 14
        BAR_W = 180 if compact else 280
        max_iter = max((n for _, n in iters), default=1)
        for thr, n in iters:
            row = tk.Frame(iter_frame, bg=PANEL); row.pack(fill="x", pady=1)
            tk.Label(row, text=f"f≤{thr}", font=("Menlo", 9),
                     bg=PANEL, fg=SUBTLE, width=5, anchor="w").pack(side="left")
            c = tk.Canvas(row, width=BAR_W, height=bar_h,
                          bg=PANEL, highlightthickness=0)
            c.pack(side="left", padx=(2, 4))
            w = int(BAR_W * n / max_iter) if max_iter else 0
            color = SUCCESS if (thr, n) == iters[-1] else PRIMARY
            c.create_rectangle(0, 2, w, bar_h - 2, fill=color, outline="")
            tk.Label(row, text=f"{n:,}", font=("Menlo", 9),
                     bg=PANEL, fg=TEXT).pack(side="left")

        # ----- depth bars -----
        depth_frame = tk.Frame(charts, bg=PANEL, padx=10, pady=10)
        if compact:
            depth_frame.pack(fill="x", pady=(0, 8))
        else:
            depth_frame.pack(side="left", fill="both", expand=True, padx=(6, 0))
        tk.Label(depth_frame,
                 text=f"by depth · reached depth {max(0, len(depths) - 1)} · log",
                 font=("Menlo", 9), bg=PANEL, fg=MUTED).pack(anchor="w")
        hist_h = 100 if compact else 120
        bar_w = 4 if compact else 10
        gap = 1 if compact else 2
        if depths:
            max_d = max(depths) or 1
            log_max = math.log10(max_d + 1)
            hist_w = len(depths) * (bar_w + gap)
            chart = tk.Canvas(depth_frame, width=hist_w, height=hist_h + 16,
                              bg=PANEL, highlightthickness=0)
            chart.pack(anchor="w", pady=(6, 0))
            for d, n in enumerate(depths):
                h = int(hist_h * math.log10(n + 1) / log_max) if n else 0
                x0 = d * (bar_w + gap)
                color = HEATMAP[min(d // 4, len(HEATMAP) - 1)]
                chart.create_rectangle(x0, hist_h - h, x0 + bar_w, hist_h,
                                       fill=color, outline="")
                step = 5 if compact else 5
                if d % step == 0:
                    chart.create_text(x0 + bar_w // 2, hist_h + 6,
                                      text=str(d),
                                      font=("Menlo", 8), fill=SUBTLE)

        # ----- depth × f-value heatmap -----
        joint = stats.get('joint_depth_f') or {}
        if joint:
            heat_frame = tk.Frame(charts if compact else wrap,
                                  bg=PANEL, padx=10, pady=10)
            if compact:
                heat_frame.pack(fill="x")
            else:
                heat_frame.pack(fill="x", pady=(8, 0))
            tk.Label(heat_frame,
                     text="depth × f · log brightness",
                     font=("Menlo", 9), bg=PANEL, fg=MUTED).pack(anchor="w")
            max_d = max(d for d, _f in joint)
            min_f = min(f for _d, f in joint)
            max_f = max(f for _d, f in joint)
            n_d = max_d + 1
            n_f = max(1, max_f - min_f + 1)
            cell = 4 if compact else 9
            heat_w = n_d * cell + 36
            heat_h = n_f * cell + 26
            hc = tk.Canvas(heat_frame, width=heat_w, height=heat_h,
                           bg=PANEL, highlightthickness=0)
            hc.pack(anchor="w", pady=(6, 0))
            log_max = math.log10(max(joint.values()) + 1)
            for (d, f), cnt in joint.items():
                t = math.log10(cnt + 1) / log_max if log_max else 0
                r = int(24 + (243 - 24) * t)
                gg = int(24 + (139 - 24) * t)
                bb = int(37 + (168 - 37) * t)
                color = f"#{r:02x}{gg:02x}{bb:02x}"
                x0 = 28 + d * cell
                y0 = heat_h - 18 - (f - min_f + 1) * cell
                hc.create_rectangle(x0, y0, x0 + cell, y0 + cell,
                                    fill=color, outline="")
            d_step = max(1, n_d // (8 if compact else 12))
            for d in range(0, n_d, d_step):
                hc.create_text(28 + d * cell + cell // 2, heat_h - 8,
                               text=str(d), font=("Menlo", 7 if compact else 8),
                               fill=SUBTLE)
            f_step = max(1, n_f // (6 if compact else 8))
            for fv in range(min_f, max_f + 1, f_step):
                hc.create_text(20, heat_h - 18 - (fv - min_f + 1) * cell + cell // 2,
                               text=str(fv), font=("Menlo", 7 if compact else 8),
                               fill=SUBTLE, anchor="e")

    # ----- Tree source adapter (in-memory record OR mmap'd Run) -----

    def _tree_source_kind(self):
        if self._run is not None:
            return "run"
        if self.search_record:
            return "memory"
        return None

    def _tree_total_recorded(self):
        if self._run is not None:
            return self._run.total_recorded
        return len(self.search_record)

    def _tree_total_explored(self):
        if self._run is not None:
            return self._run.total_explored
        return self._total_nodes or len(self.search_record)

    def _tree_node(self, nid):
        # Negative IDs = virtual nodes on the always-drawn optimal-path spine.
        if nid < 0:
            i = -nid - 1
            states = getattr(self, "_full_path_states", [])
            moves = getattr(self, "_full_path_moves", [])
            if i < 0 or i >= len(states):
                return None
            from astar import manhattan, linear_conflict
            h = manhattan(states[i]) + linear_conflict(states[i])
            move = moves[i - 1].name if i > 0 and i - 1 < len(moves) else "ROOT"
            parent_id = -i if i > 0 else 0xFFFFFFFF
            return {
                "parent_id": parent_id,
                "move": move,
                "depth": i,
                "f_value": i + h,
                "iteration": 0,
                "on_path": True,
            }
        if self._run is not None:
            try:
                return self._run.node(nid)
            except Exception:
                return None
        if 0 <= nid < len(self.search_record):
            parent, move_name, depth, f = self.search_record[nid]
            return {
                "parent_id": parent if parent >= 0 else 0xFFFFFFFF,
                "move": move_name if move_name is not None else "ROOT",
                "depth": depth, "f_value": f, "iteration": 0,
                "on_path": False,
            }
        return None

    def _tree_children(self, nid):
        if self._run is not None:
            return self._run.children(nid)
        cache = getattr(self, "_mem_children_cache", None)
        if cache is None:
            cache = {}
            for cid, (parent, *_rest) in enumerate(self.search_record):
                if parent >= 0:
                    cache.setdefault(parent, []).append(cid)
            self._mem_children_cache = cache
        return cache.get(nid, [])

    def _tree_solution_ids(self):
        if self._run is not None:
            return list(self._run.solution_path_ids)
        cache = getattr(self, "_mem_solution_ids", None)
        if cache is not None:
            return cache
        sol = []
        if self.search_record:
            sol = [0]
            cur = 0
            for m in self.solution_moves:
                nxt = None
                for c in self._tree_children(cur):
                    if self.search_record[c][1] == m.name:
                        nxt = c; break
                if nxt is None:
                    break
                sol.append(nxt); cur = nxt
        self._mem_solution_ids = sol
        return sol

    def _tree_reconstruct_board(self, nid):
        if nid < 0:
            i = -nid - 1
            states = getattr(self, "_full_path_states", [])
            if 0 <= i < len(states):
                return list(states[i])
            return list(self._solve_start_state or range(16))
        if self._run is not None:
            try:
                return self._run.reconstruct_board(nid)
            except Exception:
                return list(self._solve_start_state or range(16))
        moves = []
        cur = nid
        while cur > 0 and cur < len(self.search_record):
            p, mn, *_ = self.search_record[cur]
            if mn:
                moves.append(mn)
            cur = p
            if cur < 0:
                break
        moves.reverse()
        board = list(self._solve_start_state or list(range(16)))
        name_to_move = {m.name: m for m in (MOVE.UP, MOVE.DOWN, MOVE.LEFT, MOVE.RIGHT)}
        for mn in moves:
            mv = name_to_move.get(mn)
            if mv is None:
                continue
            zi = board.index(0)
            board = apply_move(board, zi, mv)
        return board

    def _tree_path_names(self, nid):
        if nid < 0:
            i = -nid - 1
            moves = getattr(self, "_full_path_moves", [])
            return [m.name for m in moves[:max(0, i)]]
        if self._run is not None:
            try:
                return list(self._run.path_to_root(nid))
            except Exception:
                return []
        path = []
        cur = nid
        while cur > 0 and cur < len(self.search_record):
            p, mn, *_ = self.search_record[cur]
            if mn:
                path.append(mn)
            cur = p
            if cur < 0:
                break
        path.reverse()
        return path

    # ----- Tree window -----

    def _open_tree_window(self):
        if self._tree_source_kind() is None:
            return
        if self._tree_window is not None and self._tree_window.winfo_exists():
            self._tree_window.lift(); self._tree_window.focus_force(); return

        self._mem_children_cache = None
        self._mem_solution_ids = None
        self._tree_node_items = {}
        self._tree_positions = {}
        self._detail_widgets = {}

        total_rec = self._tree_total_recorded()
        if total_rec == 0:
            return

        DRAW_LIMIT = 8000
        sol_list = self._tree_solution_ids()
        if total_rec <= DRAW_LIMIT:
            draw_ids = set(range(total_rec))
        else:
            draw_ids = set(sol_list)
            if self._run is not None and self._run.sample_ids:
                budget = max(1, DRAW_LIMIT - len(draw_ids) - 1)
                step = max(1, len(self._run.sample_ids) // budget)
                draw_ids.update(self._run.sample_ids[::step])
            for nid in list(draw_ids):
                cur = nid; hops = 0
                while cur != 0 and hops < 200:
                    n = self._tree_node(cur)
                    if n is None: break
                    pid = n["parent_id"]
                    if pid >= 0xFFFFFFFE:
                        draw_ids.add(0); break
                    if pid in draw_ids: break
                    draw_ids.add(pid); cur = pid; hops += 1
            draw_ids.add(0)

        children = {}
        for nid in draw_ids:
            n = self._tree_node(nid)
            if n is None: continue
            p = n["parent_id"]
            if p == 0xFFFFFFFF: continue
            if p in draw_ids:
                children.setdefault(p, []).append(nid)
        for kids in children.values():
            kids.sort()

        sol_set = set(sol_list)
        sol_edges = set(zip(sol_list, sol_list[1:]))

        # ----- Layout -----
        # The recorded tree often has thousands of leaves (way wider than a
        # window) and a shallow depth, while the optimal solution path can be
        # 60+ deep. Lay out at "natural" units first, then post-scale x to
        # fit a target tree-pane width and pick Y_STEP so the full
        # max(recorded_depth, spine_depth) fits inside the available canvas
        # height. Result: tree fills the canvas, spine stays on screen.
        positions = {}
        UNIT_W = 1.0
        UNIT_Y = 1.0
        depth_cache = {}

        def depth_of(nid):
            if nid not in depth_cache:
                n = self._tree_node(nid)
                depth_cache[nid] = n["depth"] if n else 0
            return depth_cache[nid]

        def layout(nid, x_start):
            kids = children.get(nid, [])
            if not kids:
                positions[nid] = (x_start + UNIT_W / 2, depth_of(nid) * UNIT_Y)
                return UNIT_W
            width = 0
            for k in kids:
                width += layout(k, x_start + width)
            first_x = positions[kids[0]][0]
            last_x = positions[kids[-1]][0]
            positions[nid] = ((first_x + last_x) / 2, depth_of(nid) * UNIT_Y)
            return width

        raw_w = layout(0, 0) if 0 in draw_ids else 1.0
        AXIS_X = 60

        max_depth = max((depth_of(i) for i in draw_ids), default=0)

        # Decide pane dimensions. Adapt to screen size so the spine stays in
        # the viewport on small displays and the tree stretches on big ones.
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
        except Exception:
            sw, sh = 1500, 900
        # center column ≈ screen × 0.94 - sidebar(260) - detail(300) - paddings(60)
        center_w = max(420, int(sw * 0.94) - 620)
        tree_pane_w = max(360, int(center_w * 0.72))
        tree_pane_h = max(540, int(sh * 0.92) - 240)
        spine_depth = len(self.solution_moves) if self.solution_moves else 0
        display_max_depth = max(max_depth, spine_depth)

        # Y step: pick so the full depth range fills the pane vertically,
        # but with a sensible floor so things stay readable.
        Y_STEP = max(14, min(40, tree_pane_h // max(1, display_max_depth + 2)))

        # X scale: compress wide trees, preserve narrow ones.
        sx = (tree_pane_w / raw_w) if raw_w > 0 else 1.0
        for nid in positions:
            x, y_units = positions[nid]
            positions[nid] = (AXIS_X + x * sx, y_units * Y_STEP + 50)
        total_w = tree_pane_w if raw_w > 0 else AXIS_X
        self._tree_positions = positions

        f_vals = [self._tree_node(nid)["f_value"]
                  for nid in draw_ids if self._tree_node(nid) is not None]
        max_f = max(f_vals, default=1)
        min_f = min(f_vals, default=0)
        f_span = max(1, max_f - min_f)

        def f_color(f):
            t = (f - min_f) / f_span
            if t < 0.5:
                r = int(166 + (249 - 166) * (t * 2))
                g = int(227 + (226 - 227) * (t * 2))
                b = int(161 + (175 - 161) * (t * 2))
            else:
                r = int(249 + (243 - 249) * ((t - 0.5) * 2))
                g = int(226 + (139 - 226) * ((t - 0.5) * 2))
                b = int(175 + (168 - 175) * ((t - 0.5) * 2))
            return f"#{r:02x}{g:02x}{b:02x}"

        win = tk.Toplevel(self.root)
        self._tree_window = win
        win.title("A* Search Tree")
        win.configure(bg=BG)

        # Fill most of the screen.
        try:
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            ww = int(sw * 0.94)
            wh = int(sh * 0.92)
            wx = (sw - ww) // 2
            wy = (sh - wh) // 2
            win.geometry(f"{ww}x{wh}+{wx}+{wy}")
        except Exception:
            win.geometry("1500x900")

        # ----- Top strip: title + stats + key hints (slim) -----
        topbar = tk.Frame(win, bg=BG)
        topbar.pack(fill="x", padx=18, pady=(10, 4))
        title_row = tk.Frame(topbar, bg=BG); title_row.pack(fill="x")
        tk.Label(title_row, text="A* Search Tree",
                 font=("Helvetica", 18, "bold"),
                 bg=BG, fg=TEXT).pack(side="left")
        total_expl = self._tree_total_explored()
        sub_text = (f"showing {len(draw_ids):,} of {total_rec:,} recorded "
                    f"·  explored ~{total_expl:,}  ·  depth {max_depth}  "
                    f"·  solution {max(0, len(sol_list) - 1)}/{len(self.solution_moves)} steps")
        tk.Label(title_row, text=sub_text, font=("Helvetica", 11),
                 bg=BG, fg=MUTED).pack(side="left", padx=(14, 0))

        keys_row = tk.Frame(topbar, bg=BG); keys_row.pack(fill="x", pady=(2, 0))
        tk.Label(keys_row,
                 text="● solution",
                 font=("Menlo", 10, "bold"), bg=BG, fg=PRIMARY).pack(side="left", padx=(0, 12))
        tk.Label(keys_row, text="◎ selected",
                 font=("Menlo", 10, "bold"), bg=BG, fg=WARN).pack(side="left", padx=(0, 12))
        tk.Label(keys_row, text="● explored (color = f-value)",
                 font=("Menlo", 10), bg=BG, fg=MUTED).pack(side="left", padx=(0, 16))
        tk.Label(keys_row,
                 text="j/k dfs · J/K depth · f/F f · [/] iter · s sol · "
                      "↑↓ parent/child · ←→ sibling · g/G root/leaf · "
                      "⌘+wheel zoom · click selects · Enter loads · Esc closes",
                 font=("Menlo", 9), bg=BG, fg=SUBTLE).pack(side="left")

        # ----- Body grid: sidebar | tree | detail -----
        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=(4, 0))
        body.columnconfigure(0, weight=0, minsize=260)
        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=0, minsize=300)
        body.rowconfigure(0, weight=1)

        # Sidebar (collapsible) — aggregates stacked vertically.
        sidebar = tk.Frame(body, bg=BG, width=260)
        sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        sidebar.grid_propagate(False)
        side_inner = tk.Frame(sidebar, bg=BG)
        side_inner.pack(fill="both", expand=True)
        if self.search_stats:
            self._render_search_aggregates(side_inner, self.search_stats, compact=True)

        # Center — the tree canvas, dominant.
        tree_wrap = tk.Frame(body, bg=PANEL)
        tree_wrap.grid(row=0, column=1, sticky="nsew")
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)
        canvas = tk.Canvas(tree_wrap, bg=PANEL, highlightthickness=0)
        hbar = tk.Scrollbar(tree_wrap, orient="horizontal", command=canvas.xview)
        vbar = tk.Scrollbar(tree_wrap, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        self._tree_canvas = canvas

        # Depth axis labels along the left edge.
        axis_extent_x = AXIS_X + total_w + 200  # past the spine area
        depth_label_step = 1 if display_max_depth <= 25 else 2 if display_max_depth <= 50 else 5
        for d in range(0, display_max_depth + 1):
            y = d * Y_STEP + 50
            if d % depth_label_step == 0:
                canvas.create_text(36, y, text=str(d),
                                   font=("Menlo", 9), fill=SUBTLE, anchor="e")
            if d % max(2, depth_label_step) == 0:
                canvas.create_line(AXIS_X - 6, y, axis_extent_x, y,
                                   fill="#1e1e2e", width=1)
        canvas.create_text(36, 22, text="depth",
                           font=("Menlo", 9, "bold"), fill=MUTED, anchor="e")
        # Mark where recorded tree ends visually (shallow vs deep zones).
        if max_depth < display_max_depth:
            y_end = max_depth * Y_STEP + 50
            canvas.create_line(AXIS_X - 6, y_end + Y_STEP // 2,
                               AXIS_X + total_w, y_end + Y_STEP // 2,
                               fill="#45475a", width=1, dash=(3, 3))
            canvas.create_text(AXIS_X + total_w // 2, y_end + Y_STEP // 2 - 8,
                               text=f"↑ recorded depths 0–{max_depth}   ·   "
                                    f"deeper nodes only on optimal spine ↓",
                               font=("Menlo", 9), fill=MUTED)

        # Edges (solution drawn after so they sit on top).
        normal_edges = []
        for cid in draw_ids:
            if cid not in positions: continue
            n = self._tree_node(cid)
            if n is None: continue
            p = n["parent_id"]
            if p == 0xFFFFFFFF or p not in positions: continue
            x1, y1 = positions[p]; x2, y2 = positions[cid]
            if (p, cid) in sol_edges:
                continue
            normal_edges.append((x1, y1, x2, y2))
        for x1, y1, x2, y2 in normal_edges:
            canvas.create_line(x1, y1, x2, y2, fill="#313244", width=1)
        for p, c in sol_edges:
            if p in positions and c in positions:
                x1, y1 = positions[p]; x2, y2 = positions[c]
                canvas.create_line(x1, y1, x2, y2, fill=PRIMARY, width=3)

        # Nodes — larger for legibility + clickability.
        self._tree_node_items.clear()
        for nid in draw_ids:
            pos = positions.get(nid)
            if pos is None: continue
            x, y = pos
            n = self._tree_node(nid)
            if n is None: continue
            is_sol = nid in sol_set
            if is_sol:
                r = 7
                item = canvas.create_oval(x - r, y - r, x + r, y + r,
                                          fill=PRIMARY, outline=TEXT, width=1)
            else:
                r = 5
                item = canvas.create_oval(x - r, y - r, x + r, y + r,
                                          fill=f_color(n["f_value"]), outline="")
            self._tree_node_items[item] = nid

        self._tree_sel_ring = canvas.create_oval(-20, -20, -20, -20,
                                                 outline=WARN, width=3)

        # Hover tooltip.
        tooltip_bg = canvas.create_rectangle(0, 0, 0, 0,
                                             fill=BG, outline=PRIMARY, width=1,
                                             state="hidden")
        tooltip_txt = canvas.create_text(0, 0, text="", anchor="nw",
                                         font=("Menlo", 9), fill=TEXT,
                                         state="hidden")

        def show_tooltip(nid, sx, sy):
            n = self._tree_node(nid)
            if n is None:
                return
            move = n["move"]
            depth = n["depth"]; f = n["f_value"]; h = max(0, f - depth)
            label = (f"node {nid:,}  ·  {move}\n"
                     f"depth {depth}  g={depth}  h={h}  f={f}  iter {n['iteration']}"
                     + ("\n★ on solution path" if nid in sol_set else ""))
            canvas.itemconfigure(tooltip_txt, text=label, state="normal")
            x0, y0, x1, y1 = canvas.bbox(tooltip_txt)
            canvas.coords(tooltip_bg, x0 - 6, y0 - 4, x1 + 6, y1 + 4)
            canvas.itemconfigure(tooltip_bg, state="normal")
            canvas.tag_raise(tooltip_bg); canvas.tag_raise(tooltip_txt)
            canvas.coords(tooltip_txt, sx + 14, sy + 14)
            x0, y0, x1, y1 = canvas.bbox(tooltip_txt)
            canvas.coords(tooltip_bg, x0 - 6, y0 - 4, x1 + 6, y1 + 4)

        def hide_tooltip():
            canvas.itemconfigure(tooltip_bg, state="hidden")
            canvas.itemconfigure(tooltip_txt, state="hidden")

        def nearest(e):
            cx = canvas.canvasx(e.x); cy = canvas.canvasy(e.y)
            best = None; best_d = 30 * 30
            for nid, (px, py) in positions.items():
                d = (px - cx) ** 2 + (py - cy) ** 2
                if d < best_d:
                    best_d = d; best = nid
            return best, cx, cy

        def on_move(e):
            nid, cx, cy = nearest(e)
            if nid is None:
                hide_tooltip(); return
            show_tooltip(nid, cx, cy)

        def on_click(e):
            nid, _, _ = nearest(e)
            if nid is not None:
                self._select_node(nid)
                canvas.focus_set()

        canvas.bind("<Motion>", on_move)
        canvas.bind("<Leave>", lambda _e: hide_tooltip())
        canvas.bind("<Button-1>", on_click)

        # Scroll + zoom.
        def on_wheel(e):
            canvas.yview_scroll(int(-e.delta), "units")
        def on_shift_wheel(e):
            canvas.xview_scroll(int(-e.delta), "units")

        # Initial scrollregion accounts for both tree and spine.
        init_w = AXIS_X + total_w + 180
        init_h = (display_max_depth + 3) * Y_STEP + 50
        zoom_state = {"scale": 1.0, "scrollregion": (0, 0, init_w, init_h)}

        def apply_zoom(factor, anchor=None):
            new = max(0.25, min(8.0, zoom_state["scale"] * factor))
            actual = new / zoom_state["scale"]
            if abs(actual - 1.0) < 1e-3:
                return
            zoom_state["scale"] = new
            if anchor is None:
                anchor = (canvas.canvasx(canvas.winfo_width() // 2),
                          canvas.canvasy(canvas.winfo_height() // 2))
            canvas.scale("all", anchor[0], anchor[1], actual, actual)
            sx0, sy0, sx1, sy1 = zoom_state["scrollregion"]
            sx1 = (sx1 - sx0) * new + sx0
            sy1 = (sy1 - sy0) * new + sy0
            zoom_state["scrollregion"] = (sx0, sy0, sx1, sy1)
            canvas.configure(scrollregion=(sx0, sy0, sx1, sy1))
            # Update cached positions so selection ring + scrolling work post-zoom.
            for nid in self._tree_positions:
                px, py = self._tree_positions[nid]
                self._tree_positions[nid] = (
                    (px - anchor[0]) * actual + anchor[0],
                    (py - anchor[1]) * actual + anchor[1],
                )
            pos = self._tree_positions.get(self._selected_nid)
            if pos is not None:
                x, y = pos; r = 9 * new
                canvas.coords(self._tree_sel_ring,
                              x - r, y - r, x + r, y + r)

        def on_ctrl_wheel(e):
            factor = 1.2 if e.delta > 0 else (1 / 1.2)
            anchor = (canvas.canvasx(e.x), canvas.canvasy(e.y))
            apply_zoom(factor, anchor)

        canvas.configure(scrollregion=zoom_state["scrollregion"])
        win.bind("<MouseWheel>", on_wheel)
        win.bind("<Shift-MouseWheel>", on_shift_wheel)
        win.bind("<Command-MouseWheel>", on_ctrl_wheel)
        win.bind("<Control-MouseWheel>", on_ctrl_wheel)
        win.bind("<Command-equal>", lambda _e: apply_zoom(1.25))
        win.bind("<Command-minus>", lambda _e: apply_zoom(1 / 1.25))
        win.bind("<Command-0>",
                 lambda _e: apply_zoom(1.0 / zoom_state["scale"]))

        # ----- Full optimal solution spine -----
        # We always have solution_moves saved, so render the FULL path even when
        # the recorder skipped most of it (sampled mode). Uses negative virtual
        # IDs (-1 = start, -2 = step 1, ...) so it integrates with selection.
        self._full_path_positions = {}
        self._full_path_states = []
        self._full_path_moves = list(self.solution_moves)
        spine_max_y = (max_depth + 1) * Y_STEP + 50
        if self.solution_moves and self._solve_start_state:
            sol_states = [list(self._solve_start_state)]
            name_to_move = {m.name: m for m in (MOVE.UP, MOVE.DOWN, MOVE.LEFT, MOVE.RIGHT)}
            for m in self.solution_moves:
                zi = sol_states[-1].index(0)
                sol_states.append(apply_move(sol_states[-1], zi, m))
            self._full_path_states = sol_states

            spine_x = AXIS_X + total_w + 60
            # Header
            canvas.create_text(spine_x, 20, text="OPTIMAL",
                               font=("Menlo", 9, "bold"), fill=PRIMARY)
            canvas.create_text(spine_x, 34, text=f"{len(self.solution_moves)} moves",
                               font=("Menlo", 9), fill=SUBTLE)
            # Faint separator between tree and spine
            canvas.create_line(spine_x - 40, 50,
                               spine_x - 40, max_depth * Y_STEP + 80,
                               fill="#1e1e2e", width=1)

            # Glow line (thick faint behind, thin bright on top)
            for i in range(len(sol_states) - 1):
                y1 = i * Y_STEP + 50
                y2 = (i + 1) * Y_STEP + 50
                canvas.create_line(spine_x, y1, spine_x, y2,
                                   fill="#4a5180", width=7)
            for i in range(len(sol_states) - 1):
                y1 = i * Y_STEP + 50
                y2 = (i + 1) * Y_STEP + 50
                canvas.create_line(spine_x, y1, spine_x, y2,
                                   fill=PRIMARY, width=3)

            arrows = {"UP": "↑", "DOWN": "↓", "LEFT": "←", "RIGHT": "→"}
            for i, _state in enumerate(sol_states):
                y = i * Y_STEP + 50
                if i == 0:
                    r = 9; fill = PRIMARY; outline = TEXT
                elif i == len(sol_states) - 1:
                    r = 9; fill = SUCCESS; outline = TEXT
                else:
                    r = 6; fill = PRIMARY; outline = TEXT
                canvas.create_oval(spine_x - r, y - r, spine_x + r, y + r,
                                   fill=fill, outline=outline, width=1)
                self._full_path_positions[-(i + 1)] = (spine_x, y)
                if i > 0 and i - 1 < len(self.solution_moves):
                    a = arrows.get(self.solution_moves[i - 1].name, "?")
                    canvas.create_text(spine_x + 16, y, text=a,
                                       font=("Menlo", 11, "bold"),
                                       fill=WARN, anchor="w")
                if i % 5 == 0 or i == len(sol_states) - 1:
                    canvas.create_text(spine_x - 16, y, text=f"#{i}",
                                       font=("Menlo", 9), fill=SUBTLE,
                                       anchor="e")
            spine_max_y = max(spine_max_y, (len(sol_states)) * Y_STEP + 50)

            # Rebuild positions dicts so click / nearest / selection ring see
            # spine nodes too.
            for nid, pos in self._full_path_positions.items():
                self._tree_positions[nid] = pos
                positions[nid] = pos

            # Expand scrollregion so the spine is reachable.
            sx0, sy0, sx1, sy1 = zoom_state["scrollregion"]
            zoom_state["scrollregion"] = (
                sx0, sy0,
                max(sx1, spine_x + 80),
                max(sy1, spine_max_y + 40),
            )
            canvas.configure(scrollregion=zoom_state["scrollregion"])

        # Right detail pane.
        detail = tk.Frame(body, bg=PANEL, padx=14, pady=14, width=300)
        detail.grid(row=0, column=2, sticky="nse", padx=(10, 0))
        detail.grid_propagate(False)
        self._build_detail_pane(detail)

        # Bottom status strip.
        self._tree_statusbar = tk.Label(
            win, text="", anchor="w",
            font=("Menlo", 10), bg=PANEL, fg=SUBTLE, padx=14, pady=6,
        )
        self._tree_statusbar.pack(fill="x", padx=18, pady=(6, 10))

        win.bind("<Key>", self._on_tree_key)
        win.focus_set()
        canvas.focus_set()

        self._select_node(0)

    # ----- Detail pane + selection -----

    def _build_detail_pane(self, parent):
        self._detail_widgets = {}
        tk.Label(parent, text="SELECTED NODE", font=("Menlo", 9, "bold"),
                 bg=PANEL, fg=MUTED).pack(anchor="w")

        head = tk.Frame(parent, bg=PANEL); head.pack(fill="x", pady=(4, 6))
        self._detail_widgets["nid"] = tk.Label(
            head, text="node 0", font=("Menlo", 13, "bold"),
            bg=PANEL, fg=TEXT, anchor="w")
        self._detail_widgets["nid"].pack(side="left")
        self._detail_widgets["on_path"] = tk.Label(
            head, text="", font=("Menlo", 9, "bold"),
            bg=PANEL, fg=PRIMARY, anchor="e")
        self._detail_widgets["on_path"].pack(side="right")

        stats = tk.Frame(parent, bg=PANEL); stats.pack(fill="x", pady=(0, 8))
        for key, label in [("depth", "depth"), ("g", "g"), ("h", "h"),
                           ("f", "f"), ("iter", "iter")]:
            row = tk.Frame(stats, bg=PANEL); row.pack(fill="x")
            tk.Label(row, text=label, font=("Menlo", 9),
                     bg=PANEL, fg=MUTED).pack(side="left")
            self._detail_widgets[key] = tk.Label(
                row, text="—", font=("Menlo", 10, "bold"),
                bg=PANEL, fg=PRIMARY)
            self._detail_widgets[key].pack(side="right")

        bw = CARD_TILE * 4 + CARD_PAD * 5
        self._detail_widgets["board_canvas"] = tk.Canvas(
            parent, width=bw, height=bw, bg=BG, highlightthickness=0)
        self._detail_widgets["board_canvas"].pack(pady=(8, 4))

        tk.Label(parent, text="path from root",
                 font=("Menlo", 9), bg=PANEL, fg=MUTED).pack(anchor="w", pady=(6, 0))
        self._detail_widgets["path"] = tk.Text(
            parent, height=4, width=24, bg=BG, fg=TEXT,
            font=("Menlo", 9), relief="flat", borderwidth=0, padx=4, pady=4,
            wrap="word", insertbackground=TEXT, state="disabled")
        self._detail_widgets["path"].pack(fill="x")

        actions = tk.Frame(parent, bg=PANEL); actions.pack(fill="x", pady=(10, 0))
        tk.Button(actions, text="Load in main board",
                  command=self._load_selected_in_board,
                  font=("Helvetica", 10, "bold"),
                  bg=SUCCESS, fg=BG, relief="flat", borderwidth=0,
                  cursor="hand2", padx=8, pady=6).pack(fill="x", pady=2)
        tk.Button(actions, text="Solve from here",
                  command=self._solve_from_selected,
                  font=("Helvetica", 10, "bold"),
                  bg=BG, fg=PRIMARY, relief="flat", borderwidth=0,
                  highlightthickness=1, highlightbackground=PRIMARY,
                  cursor="hand2", padx=8, pady=6).pack(fill="x", pady=2)
        tk.Button(actions, text="Copy path",
                  command=self._copy_selected_path,
                  font=("Helvetica", 10, "bold"),
                  bg=BG, fg=SUBTLE, relief="flat", borderwidth=0,
                  highlightthickness=1, highlightbackground=SUBTLE,
                  cursor="hand2", padx=8, pady=6).pack(fill="x", pady=2)

    def _select_node(self, nid):
        n = self._tree_node(nid)
        if n is None:
            return
        self._selected_nid = nid
        w = self._detail_widgets
        if not w:
            return
        sol_set = set(self._tree_solution_ids())
        on_path = (nid in sol_set) or nid < 0
        if nid < 0:
            step = -nid - 1
            total = len(getattr(self, "_full_path_states", []))
            label = (f"start (step 0/{max(0, total - 1)})" if step == 0
                     else f"step #{step}/{max(0, total - 1)}")
            w["nid"].config(text=label)
        else:
            w["nid"].config(text=f"node {nid:,}")
        w["on_path"].config(text="★ on path" if on_path else "")
        depth = n["depth"]; f = n["f_value"]
        h = max(0, f - depth)
        w["depth"].config(text=str(depth))
        w["g"].config(text=str(depth))
        w["h"].config(text=str(h))
        w["f"].config(text=str(f))
        w["iter"].config(text=str(n["iteration"]))

        board = self._tree_reconstruct_board(nid)
        bc = w["board_canvas"]; bc.delete("all")
        for i, v in enumerate(board):
            r, c = i // 4, i % 4
            x0 = CARD_PAD + c * (CARD_TILE + CARD_PAD)
            y0 = CARD_PAD + r * (CARD_TILE + CARD_PAD)
            x1, y1 = x0 + CARD_TILE, y0 + CARD_TILE
            if v == 0:
                bc.create_rectangle(x0, y0, x1, y1, fill=BG, outline=PANEL); continue
            dist = abs(r - GOAL_ROW[v]) + abs(c - GOAL_COL[v])
            fill = HEATMAP[min(dist, len(HEATMAP) - 1)]
            bc.create_rectangle(x0, y0, x1, y1, fill=fill, outline="")
            bc.create_text((x0 + x1) // 2, (y0 + y1) // 2,
                           text=str(v), font=("Menlo", 9, "bold"), fill=BG)

        path = self._tree_path_names(nid)
        arrows = {"UP": "↑", "DOWN": "↓", "LEFT": "←", "RIGHT": "→"}
        txt = " ".join(arrows.get(p, p) for p in path)
        pw = w["path"]
        pw.config(state="normal")
        pw.delete("1.0", "end")
        pw.insert("1.0", txt or "(root)")
        pw.config(state="disabled")

        if self._tree_canvas is not None:
            pos = self._tree_positions.get(nid)
            if pos is not None:
                x, y = pos; r = 9
                self._tree_canvas.coords(self._tree_sel_ring,
                                         x - r, y - r, x + r, y + r)
                try:
                    sx0, sy0, sx1, sy1 = map(float, self._tree_canvas.cget("scrollregion").split())
                    if sx1 > sx0 and sy1 > sy0:
                        self._tree_canvas.xview_moveto(max(0, (x - 200) / (sx1 - sx0)))
                        self._tree_canvas.yview_moveto(max(0, (y - 120) / (sy1 - sy0)))
                except Exception:
                    pass
            else:
                self._tree_canvas.coords(self._tree_sel_ring, -10, -10, -10, -10)

        bar = getattr(self, "_tree_statusbar", None)
        if bar is not None:
            total = self._tree_total_recorded()
            bar.config(text=(
                f"node {nid:,} / {total:,} recorded  ·  "
                f"depth {depth}  ·  g={depth}  h={h}  f={f}  ·  "
                f"iter {n['iteration']}  ·  on-path: {'yes' if on_path else 'no'}  ·  "
                f"move {n['move']}"
            ))

    def _on_tree_key(self, e):
        k = e.keysym
        nid = self._selected_nid
        if k == "Escape":
            if self._tree_window:
                self._tree_window.destroy()
                self._tree_window = None
            return
        if k == "Return":
            self._load_selected_in_board(); return
        axis_map = {
            "j": ("dfs", +1), "k": ("dfs", -1),
            "J": ("depth", +1), "K": ("depth", -1),
            "f": ("f", +1), "F": ("f", -1),
            "bracketright": ("iteration", +1), "bracketleft": ("iteration", -1),
            "s": ("solution", +1), "S": ("solution", -1),
            "Down": ("child", +1), "Up": ("parent", +1),
            "Left": ("sibling", -1), "Right": ("sibling", +1),
            "g": ("root", 0), "G": ("leaf", 0),
        }
        if k in axis_map:
            axis, direction = axis_map[k]
            new_nid = self._move_selection(nid, axis, direction)
            if new_nid is not None:
                self._select_node(new_nid)

    def _move_selection(self, nid, axis, direction):
        # When the selected node is a virtual spine node, navigation stays on
        # the spine — moves up/down the optimal path or across to root.
        if nid < 0:
            i = -nid - 1
            states = getattr(self, "_full_path_states", [])
            if axis in ("root", "parent") and direction >= 0:
                return -1
            if axis == "leaf" or (axis in ("child", "dfs", "solution") and direction > 0):
                ni = i + 1
                if 0 <= ni < len(states):
                    return -(ni + 1)
                return None
            if axis in ("parent", "dfs", "solution") and direction < 0:
                ni = i - 1
                if 0 <= ni < len(states):
                    return -(ni + 1)
                return None
            if axis == "sibling":
                return None
            # other axes: stay
            return None

        if axis == "root":
            return 0
        if axis == "leaf":
            cur = nid
            for _ in range(200):
                kids = self._tree_children(cur)
                if not kids:
                    return cur
                cur = kids[0]
            return cur
        if axis == "parent":
            n = self._tree_node(nid)
            if n and n["parent_id"] < self._tree_total_recorded():
                return n["parent_id"]
            return None
        if axis == "child":
            kids = self._tree_children(nid)
            return kids[0] if kids else None
        if axis == "sibling":
            n = self._tree_node(nid)
            if n is None or n["parent_id"] == 0xFFFFFFFF:
                return None
            sibs = self._tree_children(n["parent_id"])
            if nid not in sibs:
                return None
            ni = sibs.index(nid) + direction
            if 0 <= ni < len(sibs):
                return sibs[ni]
            return None
        if self._run is not None:
            try:
                prev_id, next_id = self._run.neighbors(nid, axis)
            except Exception:
                prev_id = next_id = None
            target = next_id if direction > 0 else prev_id
            if target is not None:
                return target
            # Solution-axis fallback: jump onto the virtual spine.
            if axis == "solution":
                states = getattr(self, "_full_path_states", [])
                if states:
                    return -1 if direction > 0 else -len(states)
            return None
        if axis == "dfs":
            new = nid + direction
            if 0 <= new < self._tree_total_recorded():
                return new
            return None
        if axis == "solution":
            # Prefer the always-complete virtual spine for cycling the path.
            states = getattr(self, "_full_path_states", [])
            if states:
                return -1 if direction > 0 else None
            sol = self._tree_solution_ids()
            if nid in sol:
                i = sol.index(nid) + direction
                if 0 <= i < len(sol):
                    return sol[i]
            elif sol:
                return sol[0]
        return None

    def _load_selected_in_board(self):
        nid = self._selected_nid
        board = self._tree_reconstruct_board(nid)
        self.board = list(board)
        self.solution_moves = []
        self.move_count = 0
        self._refresh()
        self.status.config(text=f"Loaded board from node {nid:,}", fg=SUCCESS)
        try:
            self.root.lift()
        except Exception:
            pass

    def _solve_from_selected(self):
        self._load_selected_in_board()
        if self._tree_window is not None:
            try: self._tree_window.destroy()
            except Exception: pass
            self._tree_window = None
        self.solve()

    def _copy_selected_path(self):
        path = self._tree_path_names(self._selected_nid)
        txt = " ".join(path)
        try:
            self.root.clipboard_clear(); self.root.clipboard_append(txt)
        except Exception:
            pass

    # ---------- solution path explorer ----------

    def _open_path_window(self):
        if not self.solution_moves or self._solve_start_state is None:
            return
        if self._path_window is not None and self._path_window.winfo_exists():
            self._path_window.lift(); self._path_window.focus_force(); return

        win = tk.Toplevel(self.root)
        self._path_window = win
        win.title(f"Solution Path — {len(self.solution_moves)} moves")
        win.configure(bg=BG)

        header = tk.Frame(win, bg=BG)
        header.pack(fill="x", padx=20, pady=(16, 8))
        tk.Label(header, text="Solution Path",
                 font=("Helvetica", 18, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(header,
                 text=f"{len(self.solution_moves)} optimal moves · start → goal · "
                      "heatmap shows distance from home",
                 font=("Helvetica", 11), bg=BG, fg=MUTED).pack(anchor="w", pady=(2, 0))

        # Reconstruct every state along the path
        states = [list(self._solve_start_state)]
        for m in self.solution_moves:
            zi = states[-1].index(0)
            states.append(apply_move(states[-1], zi, m))

        # Scrollable canvas with mini-boards
        outer = tk.Frame(win, bg=BG); outer.pack(fill="both", expand=True,
                                                  padx=20, pady=(0, 16))
        scroll_canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        vbar = tk.Scrollbar(outer, orient="vertical", command=scroll_canvas.yview)
        scroll_canvas.configure(yscrollcommand=vbar.set)
        scroll_canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        inner = tk.Frame(scroll_canvas, bg=BG)
        scroll_canvas.create_window((0, 0), window=inner, anchor="nw")

        COLS = 6
        for i, state in enumerate(states):
            r, c = i // COLS, i % COLS
            self._render_path_card(inner, state, i,
                                   move=self.solution_moves[i - 1] if i > 0 else None,
                                   is_goal=(i == len(states) - 1)
                                  ).grid(row=r, column=c, padx=8, pady=8)

        inner.update_idletasks()
        scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all"))

        # Mouse wheel scrolling
        def on_wheel(e):
            scroll_canvas.yview_scroll(int(-e.delta), "units")
        win.bind("<MouseWheel>", on_wheel)
        win.bind("<Button-4>", lambda e: scroll_canvas.yview_scroll(-1, "units"))
        win.bind("<Button-5>", lambda e: scroll_canvas.yview_scroll(1, "units"))

        # Sensible default size
        cards_wide = COLS * (CARD_W + 16) + 60
        cards_tall = min(700, ((len(states) + COLS - 1) // COLS) * (CARD_H + 16) + 120)
        win.geometry(f"{cards_wide}x{cards_tall}")
        win.transient(self.root)

    def _render_path_card(self, parent, state, step_idx, move, is_goal):
        frame = tk.Frame(parent, bg=PANEL, padx=8, pady=8)

        head = tk.Frame(frame, bg=PANEL); head.pack(fill="x")
        label = f"#{step_idx}" if step_idx > 0 else "start"
        if is_goal: label = "goal"
        tk.Label(head, text=label, font=("Menlo", 9, "bold"),
                 bg=PANEL, fg=SUCCESS if is_goal else PRIMARY,
                 anchor="w").pack(side="left")
        if move is not None:
            arrow = {"UP": "↑", "DOWN": "↓", "LEFT": "←", "RIGHT": "→"}[move.name]
            tk.Label(head, text=arrow, font=("Menlo", 11, "bold"),
                     bg=PANEL, fg=WARN).pack(side="right")

        # Mini-board canvas
        canvas = tk.Canvas(frame,
                           width=CARD_TILE * 4 + CARD_PAD * 5,
                           height=CARD_TILE * 4 + CARD_PAD * 5,
                           bg=BG, highlightthickness=0)
        canvas.pack(pady=(4, 0))
        for i, v in enumerate(state):
            r, c = i // 4, i % 4
            x0 = CARD_PAD + c * (CARD_TILE + CARD_PAD)
            y0 = CARD_PAD + r * (CARD_TILE + CARD_PAD)
            x1, y1 = x0 + CARD_TILE, y0 + CARD_TILE
            if v == 0:
                canvas.create_rectangle(x0, y0, x1, y1, fill=BG, outline=PANEL)
                continue
            dist = abs(r - GOAL_ROW[v]) + abs(c - GOAL_COL[v])
            fill = HEATMAP[min(dist, len(HEATMAP) - 1)]
            canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline="")
            canvas.create_text((x0 + x1) // 2, (y0 + y1) // 2,
                               text=str(v), font=("Menlo", 9, "bold"), fill=BG)

        # Heuristic value
        from astar import manhattan, linear_conflict
        h = manhattan(state) + linear_conflict(state)
        tk.Label(frame, text=f"h = {h}", font=("Menlo", 9),
                 bg=PANEL, fg=MUTED).pack(anchor="w", pady=(4, 0))
        return frame

    # ---------- solver activity panel ----------

    def _build_solver_panel(self, parent):
        panel = tk.Frame(parent, bg=PANEL, padx=10, pady=10)

        self._solver_title = tk.Label(
            panel, text="SOLVER ▸ live",
            font=("Menlo", 9, "bold"),
            bg=PANEL, fg=PRIMARY,
        )
        self._solver_title.pack(anchor="w", pady=(0, 6))

        self.hud_labels = {}
        for key in ("THRESHOLD", "H", "DEPTH", "NODES", "TIME"):
            row = tk.Frame(panel, bg=PANEL)
            row.pack(fill="x")
            tk.Label(row, text=key, font=("Menlo", 9),
                     bg=PANEL, fg=MUTED, anchor="w").pack(side="left")
            val = tk.Label(row, text="—", font=("Menlo", 11, "bold"),
                           bg=PANEL, fg=PRIMARY, anchor="e")
            val.pack(side="right")
            self.hud_labels[key] = val

        mini_size = MINI_TILE * 4 + MINI_PAD * 5
        self.mini_canvas = tk.Canvas(
            panel, width=mini_size, height=mini_size,
            bg=BG, highlightthickness=0,
        )
        self.mini_canvas.pack(pady=(10, 4))

        legend = tk.Label(panel, text="heatmap: distance to home",
                          font=("Menlo", 8), bg=PANEL, fg=MUTED)
        legend.pack(anchor="w")

        self.spine_canvas = tk.Canvas(
            panel, width=SPINE_WIDTH, height=SPINE_DOT + 6,
            bg=BG, highlightthickness=0,
        )
        self.spine_canvas.pack(pady=(8, 2))

        spine_legend = tk.Label(
            panel, text="path: ▮ up  ▮ down  ▮ left  ▮ right",
            font=("Menlo", 8), bg=PANEL, fg=MUTED,
        )
        spine_legend.pack(anchor="w")

        return panel

    def _show_solver_panel(self):
        self.preview.pack_forget()
        self.solver_panel.pack(fill="x")
        self._solve_t0 = time.time()
        self._last_threshold = None
        self._solver_snapshot = None
        for k in self.hud_labels:
            self.hud_labels[k].config(text="—", fg=PRIMARY)
        self._solver_title.config(text="SOLVER ▸ live", fg=PRIMARY)
        self.mini_canvas.delete("all")
        self.spine_canvas.delete("all")
        # Start the render polling loop on the Tk main thread.
        self._polling_solver = True
        self._poll_solver()

    def _hide_solver_panel(self):
        self._polling_solver = False
        self.solver_panel.pack_forget()
        self.preview.pack(fill="x")

    def _on_solver_progress(self, threshold, nodes, depth, h, board, path):
        # Worker thread: just drop the latest snapshot. The Tk poll loop
        # below renders it. Tuple assignment is atomic in CPython.
        self._solver_snapshot = (threshold, nodes, depth, h, board, path)

    def _poll_solver(self):
        if not self._polling_solver:
            return
        snap = self._solver_snapshot
        if snap is not None:
            self._solver_snapshot = None
            self._render_solver_state(*snap)
        # Always tick TIME so the panel feels alive even between snapshots.
        self.hud_labels["TIME"].config(
            text=f"{time.time() - self._solve_t0:.2f}s")
        self.root.after(33, self._poll_solver)  # ~30 fps

    def _render_solver_state(self, threshold, nodes, depth, h, board, path):
        elapsed = time.time() - self._solve_t0
        self._total_nodes = nodes
        self.hud_labels["THRESHOLD"].config(text=str(threshold))
        self.hud_labels["H"].config(text=str(h))
        self.hud_labels["DEPTH"].config(text=str(depth))
        self.hud_labels["NODES"].config(text=f"{nodes:,}")
        self.hud_labels["TIME"].config(text=f"{elapsed:.2f}s")

        if threshold != self._last_threshold:
            self._last_threshold = threshold
            self.hud_labels["THRESHOLD"].config(fg=WARN)
            self.root.after(220,
                lambda: self.hud_labels["THRESHOLD"].config(fg=PRIMARY))

        self._draw_mini_board(board)
        self._draw_spine(path)

    def _draw_mini_board(self, board):
        c = self.mini_canvas
        c.delete("all")
        for i, v in enumerate(board):
            r, col = i // 4, i % 4
            x0 = MINI_PAD + col * (MINI_TILE + MINI_PAD)
            y0 = MINI_PAD + r * (MINI_TILE + MINI_PAD)
            x1, y1 = x0 + MINI_TILE, y0 + MINI_TILE
            if v == 0:
                c.create_rectangle(x0, y0, x1, y1,
                                   fill=BG, outline=PANEL)
                continue
            dist = abs(r - GOAL_ROW[v]) + abs(col - GOAL_COL[v])
            fill = HEATMAP[min(dist, len(HEATMAP) - 1)]
            c.create_rectangle(x0, y0, x1, y1, fill=fill, outline="")
            c.create_text((x0 + x1) // 2, (y0 + y1) // 2,
                          text=str(v),
                          font=("Menlo", 9, "bold"), fill=BG)

    def _draw_spine(self, path):
        c = self.spine_canvas
        c.delete("all")
        if not path:
            return
        capacity = SPINE_WIDTH // (SPINE_DOT + SPINE_GAP)
        recent = path[-capacity:]
        for i, m in enumerate(recent):
            x = i * (SPINE_DOT + SPINE_GAP)
            color = SPINE_COLORS.get(m.name, MUTED)
            c.create_rectangle(x, 3, x + SPINE_DOT, 3 + SPINE_DOT,
                               fill=color, outline="")
        # Pulse cursor at the head
        head_x = (len(recent) - 1) * (SPINE_DOT + SPINE_GAP)
        c.create_rectangle(head_x - 1, 1, head_x + SPINE_DOT + 1, 3 + SPINE_DOT + 2,
                           outline=PRIMARY, width=1)

    # ---------- canvas drawing ----------

    def _rounded_rect(self, x0, y0, x1, y1, r, **kw):
        pts = [
            x0 + r, y0, x1 - r, y0,
            x1, y0, x1, y0 + r,
            x1, y1 - r, x1, y1,
            x1 - r, y1, x0 + r, y1,
            x0, y1, x0, y1 - r,
            x0, y0 + r, x0, y0,
        ]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _draw_tile(self, r, c, value):
        x0 = TILE_PAD + c * (TILE_SIZE + TILE_PAD)
        y0 = TILE_PAD + r * (TILE_SIZE + TILE_PAD)
        x1, y1 = x0 + TILE_SIZE, y0 + TILE_SIZE
        if value == 0:
            self._rounded_rect(x0, y0, x1, y1, RADIUS, fill=TILE_BLANK, outline="")
            return
        in_place = (r * 4 + c) == (value - 1)
        fill = TILE_GOAL if in_place else TILE
        text_color = TILE_GOAL_TEXT if in_place else TILE_TEXT
        self._rounded_rect(x0, y0, x1, y1, RADIUS, fill=fill, outline="")
        self.canvas.create_text(
            (x0 + x1) // 2, (y0 + y1) // 2,
            text=str(value),
            font=("Helvetica", 26, "bold"),
            fill=text_color,
        )

    def _refresh(self):
        self.canvas.delete("all")
        for i, v in enumerate(self.board):
            self._draw_tile(i // 4, i % 4, v)
        self._update_status()

    def _update_status(self):
        valid = sorted(self.board) == list(range(16))
        if not valid:
            self.status.config(text="Invalid — needs 0-15 exactly once", fg=DANGER)
        elif self.board == END_STATE:
            self.status.config(text="Solved", fg=SUCCESS)
        elif not isSolvable(self.board):
            self.status.config(text="Unsolvable (parity)", fg=DANGER)
        else:
            self.status.config(text="Ready", fg=SUBTLE)

        parts = []
        if self.move_count:
            parts.append(f"Moves: {self.move_count}")
        if self.solution_moves and not self.playing and not self.animating:
            parts.append(f"Solution: {len(self.solution_moves)}")
        self.counter.config(text="     ".join(parts))
        if self.solution_moves and not self.playing and not self.animating:
            self.path_link.config(text="▸ view path")
            if self.search_record:
                self.tree_link.config(text="▸ search tree")
            else:
                self.tree_link.config(text="")
        else:
            self.path_link.config(text="")
            self.tree_link.config(text="")

    # ---------- arrow-key play ----------

    def _on_key(self, e):
        if self.playing or self.animating or self._edit_widget is not None:
            return
        km = {"Up": MOVE.UP, "Down": MOVE.DOWN,
              "Left": MOVE.LEFT, "Right": MOVE.RIGHT}
        m = km.get(e.keysym)
        if m is None:
            return
        zi = self.board.index(0)
        zx, zy = zi // 4, zi % 4
        dx, dy = m.value
        nx, ny = zx + dx, zy + dy
        if not (0 <= nx < 4 and 0 <= ny < 4):
            return
        self.board = apply_move(self.board, zi, m)
        self.move_count += 1
        self._refresh()

    # ---------- click to edit ----------

    def _canvas_click(self, e):
        if self.playing or self.animating:
            return
        # If a cell is currently being edited, commit + dismiss it and stop.
        # A second click is required to open a new tile.
        if self._edit_widget is not None:
            self._dismiss_edit()
            return
        c = (e.x - TILE_PAD) // (TILE_SIZE + TILE_PAD)
        r = (e.y - TILE_PAD) // (TILE_SIZE + TILE_PAD)
        if not (0 <= r < 4 and 0 <= c < 4):
            return
        self._edit_cell(r * 4 + c)

    def _dismiss_edit(self):
        if self._edit_widget is None:
            return
        commit_fn = self._edit_widget[2] if len(self._edit_widget) > 2 else None
        if commit_fn is not None:
            commit_fn()
        else:
            entry, win, *_ = self._edit_widget
            self.canvas.delete(win)
            entry.destroy()
            self._edit_widget = None
            self._refresh()

    def _edit_cell(self, idx):
        if self._edit_widget is not None:
            return
        r, c = idx // 4, idx % 4
        x = TILE_PAD + c * (TILE_SIZE + TILE_PAD)
        y = TILE_PAD + r * (TILE_SIZE + TILE_PAD)
        entry = tk.Entry(
            self.canvas, width=3, justify="center",
            font=("Helvetica", 26, "bold"),
            bg=WARN, fg=BG, relief="flat", highlightthickness=0,
        )
        entry.insert(0, str(self.board[idx]) if self.board[idx] else "")
        entry.select_range(0, tk.END)
        win = self.canvas.create_window(
            x + TILE_SIZE // 2, y + TILE_SIZE // 2, window=entry,
            width=TILE_SIZE - 12, height=TILE_SIZE - 12,
        )
        entry.focus_set()

        def commit(_e=None, advance=False):
            if self._edit_widget is None:
                return
            try:
                v = int(entry.get() or 0)
                if 0 <= v <= 15:
                    self.board[idx] = v
            except ValueError:
                pass
            self.canvas.delete(win)
            entry.destroy()
            self._edit_widget = None
            self._refresh()
            if advance:
                # If we're in fix-mode, jump to next missing cell
                if idx in self._fix_queue:
                    self._fix_queue.remove(idx)
                if self._fix_queue:
                    self._edit_cell(self._fix_queue[0])
                else:
                    self._edit_cell((idx + 1) % 16)

        self._edit_widget = (entry, win, commit)

        entry.bind("<Return>", lambda e: commit(advance=True))
        entry.bind("<Tab>", lambda e: commit(advance=True))
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", lambda e: commit())

    # ---------- actions ----------

    def reset(self):
        self.board = list(END_STATE)
        self.solution_moves = []
        self.move_count = 0
        self._refresh()

    def randomize(self):
        b = list(range(16))
        random.shuffle(b)
        while not isSolvable(b):
            random.shuffle(b)
        self.board = b
        self.solution_moves = []
        self.move_count = 0
        self._refresh()

    # ---------- image paste / OCR ----------

    def paste_image(self):
        try:
            img = ImageGrab.grabclipboard()
        except Exception as ex:
            self.status.config(text=f"Clipboard error: {ex}", fg=DANGER)
            return
        if img is None:
            self.status.config(text="No image in clipboard", fg=DANGER)
            return
        if isinstance(img, list):
            try:
                img = Image.open(img[0])
            except Exception:
                self.status.config(text="Clipboard item is not an image", fg=DANGER)
                return

        self._hide_solver_panel()
        preview = img.copy()
        preview.thumbnail((260, 260))
        photo = ImageTk.PhotoImage(preview)
        self.preview.config(image=photo, text="", width=260, height=260)
        self.preview.image = photo
        self._parse_image(img)

    def _parse_image(self, img):
        if not HAS_OCR:
            self.status.config(
                text="Image loaded. Install tesseract for auto-detect.", fg=WARN,
            )
            return
        rgb = img.convert("RGB")
        bbox = self._find_puzzle_bbox(rgb)
        if bbox is None:
            self.status.config(text="Couldn't locate puzzle in image", fg=DANGER)
            return
        x0, y0, x1, y1 = bbox
        grid = rgb.crop((x0, y0, x1, y1))
        gw, gh = grid.size
        cw, ch = gw / 4, gh / 4

        # First pass: per-cell blank score + ranked digit candidates.
        # We OCR every cell — letting the constraint solver decide what's
        # blank avoids the failure where a wrong threshold strands real
        # tiles as "unknown".
        cell_info = []
        for r in range(4):
            for c in range(4):
                cx0 = int(c * cw + cw * 0.08)
                cy0 = int(r * ch + ch * 0.08)
                cx1 = int((c + 1) * cw - cw * 0.08)
                cy1 = int((r + 1) * ch - ch * 0.08)
                cell = grid.crop((cx0, cy0, cx1, cy1))
                blank_score = self._blank_score(cell)
                candidates = self._ocr_candidates(cell)
                cell_info.append({
                    "blank_score": blank_score,
                    "candidates": candidates,
                })

        detected = self._assign_with_constraints(cell_info)

        good = sum(1 for n in detected if n >= 0)
        if sorted(n for n in detected if n >= 0) == list(range(16)):
            self.board = detected
            self.solution_moves = []
            self.move_count = 0
            self._refresh()
            self.status.config(text="Loaded from image", fg=SUCCESS)
        else:
            self.board = [n if n >= 0 else 0 for n in detected]
            self.solution_moves = []
            self.move_count = 0
            self._refresh()
            missing = [i for i, n in enumerate(detected) if n < 0]
            self.status.config(
                text=f"Detected {good}/16 — type to fix {len(missing)} tile"
                     f"{'s' if len(missing) != 1 else ''}, Tab through",
                fg=WARN,
            )
            self._fix_queue = list(missing)
            if missing:
                self.root.after(80, lambda: self._edit_cell(missing[0]))

    def _assign_with_constraints(self, cell_info):
        """Pick a valid 0–15 permutation across 16 cells using OCR candidates
        and blank scores. Each non-zero digit must appear at most once, and
        there should be exactly one blank.
        """
        n = len(cell_info)
        assigned = [None] * n
        scores = [cell_info[i]["blank_score"] for i in range(n)]
        sorted_scores = sorted(scores)

        # 1. Pick the blank: it should be CLEARLY the most uniform cell,
        #    well below the rest. If scores are all similar (suggesting either
        #    no blank present or a bad bbox where many cells are background),
        #    skip — let the constraint solver tease out the blank via step 3.
        blank_idx = min(range(n), key=lambda i: scores[i])
        min_score = sorted_scores[0]
        second_min = sorted_scores[1] if len(sorted_scores) > 1 else min_score
        clearly_blank = min_score < 18 and (second_min - min_score) > 6
        if clearly_blank:
            assigned[blank_idx] = 0

        # 2. Greedy constrained assignment: cells whose top candidate is most
        # confident get first pick. Falling back to 2nd/3rd choice when the
        # top is already taken.
        used = {0} if assigned[blank_idx] == 0 else set()
        remaining = [i for i in range(n) if assigned[i] is None]
        remaining.sort(key=lambda i: -(
            cell_info[i]["candidates"][0][1] if cell_info[i]["candidates"] else 0
        ))
        for i in remaining:
            for d, _w in cell_info[i]["candidates"]:
                if 1 <= d <= 15 and d not in used:
                    assigned[i] = d
                    used.add(d)
                    break

        # 3. If no blank was confidently picked but exactly one cell ended up
        # unassigned and digits 1–15 are all placed, that cell is the blank.
        unassigned = [i for i in range(n) if assigned[i] is None]
        placed_digits = used - {0}
        if len(unassigned) == 1 and placed_digits == set(range(1, 16)):
            assigned[unassigned[0]] = 0
        else:
            for i in unassigned:
                assigned[i] = -1  # mark as unknown so the user fixes it
        return [-1 if a is None else a for a in assigned]

    def _find_puzzle_bbox(self, rgb):
        """Locate the puzzle bbox.

        Strategy:
        1. Sample corners → background colour → foreground mask.
        2. Find candidate bboxes from the top connected blobs.
        3. Refine each by sweeping position + size to MAXIMISE a 4×4 grid-fit
           score (15 dense cells + 1 sparse cell, square aspect).
        4. Return the candidate with the highest refined score.

        This fixes cases where the largest blob spans the puzzle PLUS some
        adjacent UI/clutter — the refinement pass shrinks back onto the
        actual 4×4 grid.
        """
        try:
            import numpy as np
        except ImportError:
            return None

        arr = np.array(rgb)
        h, w = arr.shape[:2]
        if h < 64 or w < 64:
            return None

        mask = self._foreground_mask(arr)
        if mask is None or not mask.any():
            return None

        # Integral image so grid_fit_score is O(1) per cell.
        integral = mask.astype(np.int32).cumsum(0).cumsum(1)

        # Collect candidate bboxes from the top blobs.
        candidates = self._blob_bboxes(mask, top_n=5)
        # Always include the whole image as a fallback candidate so the
        # refinement can drift away from a bad initial blob.
        candidates.append((0, 0, w, h))

        # Best raw-candidate (no refinement).
        initial_best = None
        initial_best_score = -1.0
        for cand in candidates:
            sc = self._grid_fit_score(integral, cand, w, h)
            if sc > initial_best_score:
                initial_best_score = sc
                initial_best = cand

        # Best refined candidate (search nearby for higher score).
        refined_best = None
        refined_best_score = -1.0
        for cand in candidates:
            refined, score = self._refine_grid_bbox(integral, cand, w, h)
            if score > refined_best_score:
                refined_best_score = score
                refined_best = refined

        # Pick refined ONLY if it's substantially better than the best initial.
        # Small score wiggles can land on bboxes that score marginally higher
        # but are slightly misaligned for OCR. A high-scoring initial is more
        # trustworthy than a marginal refinement.
        switch_threshold = max(initial_best_score * 1.10,
                               initial_best_score + 0.15)
        if (initial_best is not None
                and initial_best_score >= 0.5
                and refined_best_score <= switch_threshold):
            best_bbox, best_score = initial_best, initial_best_score
        else:
            best_bbox, best_score = refined_best, refined_best_score

        # Sanity floor: if even the best fit looks bad, bail.
        if best_bbox is None or best_score < 0.02:
            return None
        return best_bbox

    def _foreground_mask(self, arr):
        """Build a 0/1 mask of pixels that look like puzzle content.

        Multi-modal: treats EACH corner as a candidate background colour.
        A pixel is foreground only if it differs from ALL corner candidates.
        Robust to images where different corners are different colours (e.g.
        a screenshot whose left side is one UI panel and right side is another).
        """
        import numpy as np
        h, w = arr.shape[:2]
        cs = max(6, min(w, h) // 60)
        corners = [
            arr[:cs, :cs].reshape(-1, 3),
            arr[:cs, -cs:].reshape(-1, 3),
            arr[-cs:, :cs].reshape(-1, 3),
            arr[-cs:, -cs:].reshape(-1, 3),
        ]
        a32 = arr.astype(np.int32)
        diffs = []
        for c in corners:
            bg = np.median(c, axis=0).astype(np.int32)
            diffs.append(np.abs(a32 - bg).sum(axis=2))
        # Also include the median of ALL corner samples — helps with gradients.
        bg_all = np.median(np.vstack(corners), axis=0).astype(np.int32)
        diffs.append(np.abs(a32 - bg_all).sum(axis=2))
        min_diff = np.minimum.reduce(diffs)
        mask = (min_diff > 30).astype(np.uint8)

        # Degenerate-mask guard: if every corner picked a unique colour and
        # nothing matches them all, fall back to image-median background.
        if mask.mean() > 0.90:
            sample = a32.reshape(-1, 3)
            if sample.shape[0] > 20000:
                rng = np.random.default_rng(0)
                idx = rng.choice(sample.shape[0], 20000, replace=False)
                sample = sample[idx]
            bg2 = np.median(sample, axis=0).astype(np.int32)
            diff2 = np.abs(a32 - bg2).sum(axis=2)
            mask = (diff2 > 40).astype(np.uint8)
        return mask

    def _blob_bboxes(self, mask, top_n=5):
        """Coarsen the mask, find connected dense blobs, return their bboxes
        in pixel coords. Squared and centred so the refinement starts close.
        """
        import numpy as np
        h, w = mask.shape
        cell = max(4, min(w, h) // 100)
        rows, cols = h // cell, w // cell
        if rows < 8 or cols < 8:
            return []
        grid = mask[:rows * cell, :cols * cell].reshape(
            rows, cell, cols, cell
        ).mean(axis=(1, 3))
        is_dense = grid > 0.18
        pad = is_dense.copy()
        pad[1:] |= is_dense[:-1]
        pad[:-1] |= is_dense[1:]
        pad[:, 1:] |= is_dense[:, :-1]
        pad[:, :-1] |= is_dense[:, 1:]
        is_dense = pad
        if not is_dense.any():
            return []

        visited = np.zeros_like(is_dense, dtype=bool)
        blobs = []
        for sr in range(rows):
            for sc in range(cols):
                if not is_dense[sr, sc] or visited[sr, sc]:
                    continue
                stack = [(sr, sc)]
                size = 0
                mn_r = mx_r = sr; mn_c = mx_c = sc
                while stack:
                    r, c = stack.pop()
                    if r < 0 or r >= rows or c < 0 or c >= cols:
                        continue
                    if visited[r, c] or not is_dense[r, c]:
                        continue
                    visited[r, c] = True
                    size += 1
                    if r < mn_r: mn_r = r
                    if r > mx_r: mx_r = r
                    if c < mn_c: mn_c = c
                    if c > mx_c: mx_c = c
                    stack.append((r - 1, c)); stack.append((r + 1, c))
                    stack.append((r, c - 1)); stack.append((r, c + 1))
                blobs.append((size, mn_r, mx_r, mn_c, mx_c))

        blobs.sort(reverse=True)
        out = []
        for size, mn_r, mx_r, mn_c, mx_c in blobs[:top_n]:
            if size < 16:
                continue
            px0 = mn_c * cell; px1 = (mx_c + 1) * cell
            py0 = mn_r * cell; py1 = (mx_r + 1) * cell
            bw, bh = px1 - px0, py1 - py0
            side = max(bw, bh)
            cx, cy = (px0 + px1) // 2, (py0 + py1) // 2
            bx0 = max(0, cx - side // 2)
            by0 = max(0, cy - side // 2)
            bx1 = min(w, bx0 + side)
            by1 = min(h, by0 + side)
            out.append((bx0, by0, bx1, by1))
        return out

    @staticmethod
    def _rect_sum(integral, y0, x0, y1, x1):
        """Sum of mask[y0:y1, x0:x1] using a precomputed integral image."""
        a = integral[y1 - 1, x1 - 1]
        b = integral[y0 - 1, x1 - 1] if y0 > 0 else 0
        c = integral[y1 - 1, x0 - 1] if x0 > 0 else 0
        d = integral[y0 - 1, x0 - 1] if (y0 > 0 and x0 > 0) else 0
        return a - b - c + d

    def _grid_fit_score(self, integral, bbox, img_w, img_h):
        """Score how well bbox fits a 4×4 puzzle.

        A real puzzle has:
        - 15 tile cells with similar density (the tiles)
        - Optionally 1 less-dense cell (the blank)
        - Low density along the 3 horizontal + 3 vertical gutter strips
          between cells (these are real gaps, not content)
        - Roughly square aspect ratio

        Random sub-regions that happen to have uniform foreground density
        lose because their "gutter" strips are just as dense as their cells.
        """
        x0, y0, x1, y1 = bbox
        if (x0 < 0 or y0 < 0 or x1 > img_w or y1 > img_h
                or x1 - x0 < 64 or y1 - y0 < 64):
            return -1.0
        sw = x1 - x0
        sh = y1 - y0

        # Cell interior densities (drop outer ~1/6 of each cell to dodge gutters).
        densities = []
        for r in range(4):
            for c in range(4):
                cy0 = y0 + (r * sh) // 4
                cy1 = y0 + ((r + 1) * sh) // 4
                cx0 = x0 + (c * sw) // 4
                cx1 = x0 + ((c + 1) * sw) // 4
                ih = cy1 - cy0
                iw = cx1 - cx0
                mh = max(1, ih // 6)
                mw = max(1, iw // 6)
                ay0, ay1 = cy0 + mh, cy1 - mh
                ax0, ax1 = cx0 + mw, cx1 - mw
                area = (ay1 - ay0) * (ax1 - ax0)
                if area <= 0:
                    densities.append(0.0); continue
                s = self._rect_sum(integral, ay0, ax0, ay1, ax1)
                densities.append(float(s) / area)
        densities.sort()
        top15 = densities[1:]
        m = sum(top15) / 15.0
        v = sum((d - m) ** 2 for d in top15) / 15.0

        # Gutter density: 3 horizontal strips + 3 vertical strips at the 4×4
        # cell boundaries inside the bbox. Real puzzles have BG-coloured
        # gutters → near-zero mask density there.
        gt = max(2, min(sh, sw) // 60)
        gutters = []
        for r in range(1, 4):
            by = y0 + (r * sh) // 4
            gy0 = max(y0, by - gt)
            gy1 = min(y0 + sh, by + gt)
            if gy1 > gy0:
                s = self._rect_sum(integral, gy0, x0, gy1, x1)
                gutters.append(s / ((gy1 - gy0) * sw))
        for c in range(1, 4):
            bx = x0 + (c * sw) // 4
            gx0 = max(x0, bx - gt)
            gx1 = min(x0 + sw, bx + gt)
            if gx1 > gx0:
                s = self._rect_sum(integral, y0, gx0, y1, gx1)
                gutters.append(s / (sh * (gx1 - gx0)))
        avg_gutter = sum(gutters) / len(gutters) if gutters else 0.0

        # Base: dense tiles + uniform tiles.
        score = m - 4.0 * v
        # Strong bonus when there's a real cell-vs-gutter contrast.
        gutter_gap = max(0.0, m - avg_gutter)
        score += gutter_gap * 1.2

        # Aspect penalty (puzzle should be square).
        aspect = sw / max(1, sh)
        if aspect > 1.15 or aspect < 0.87:
            score *= 0.5

        # If gutters are AS dense as the cells, we're not looking at a puzzle.
        if m > 0 and avg_gutter / max(m, 1e-6) > 0.85:
            score *= 0.3

        return score

    def _refine_grid_bbox(self, integral, initial, img_w, img_h):
        """Multi-phase hill climb on bbox to maximise grid-fit score.

        Phase 1: coarse global sweep — handles wildly wrong initial guesses.
        Phase 2: medium local search around the best — closes the ~10–20px gap.
        Phase 3: fine local search — pixel-accurate corner alignment.

        Returns (bbox, score).
        """
        best = initial
        best_score = self._grid_fit_score(integral, best, img_w, img_h)

        min_dim = min(img_w, img_h)
        x0, y0, x1, y1 = initial
        init_side = min(x1 - x0, y1 - y0) if (x1 > x0 and y1 > y0) else min_dim

        # Phase 1: coarse multi-scale sweep over the whole image.
        size_mults = (0.35, 0.5, 0.65, 0.8, 0.95, 1.1)
        sizes = {max(64, int(init_side * m)) for m in size_mults}
        sizes.update(int(min_dim * m) for m in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95))
        sizes.add(min_dim)  # always test full available square
        sizes = sorted(s for s in sizes if 64 <= s <= min_dim)

        for s in sizes:
            step = max(8, s // 18)
            for nx0 in range(0, img_w - s + 1, step):
                for ny0 in range(0, img_h - s + 1, step):
                    sc = self._grid_fit_score(
                        integral, (nx0, ny0, nx0 + s, ny0 + s), img_w, img_h)
                    if sc > best_score:
                        best_score = sc; best = (nx0, ny0, nx0 + s, ny0 + s)

        # Phase 2: medium local search around the best bbox.
        for span_frac, step_frac in ((0.10, 0.04), (0.04, 0.008)):
            bx0, by0, bx1, by1 = best
            side = bx1 - bx0
            fstep = max(2, int(side * step_frac))
            span = max(8, int(side * span_frac))
            for ds in range(-span, span + 1, fstep):
                ns = side + ds
                if ns < 64:
                    continue
                for dx in range(-span, span + 1, fstep):
                    for dy in range(-span, span + 1, fstep):
                        nx0 = bx0 + dx
                        ny0 = by0 + dy
                        nx1 = nx0 + ns
                        ny1 = ny0 + ns
                        sc = self._grid_fit_score(
                            integral, (nx0, ny0, nx1, ny1), img_w, img_h)
                        if sc > best_score:
                            best_score = sc; best = (nx0, ny0, nx1, ny1)
        return best, best_score

    def _blank_score(self, cell):
        """Return a 'how textured is this cell' score. Lower = more blank."""
        gray = cell.convert("L")
        w, h = gray.size
        center = gray.crop((int(w * 0.22), int(h * 0.22),
                            int(w * 0.78), int(h * 0.78)))
        try:
            import numpy as np
            arr = np.array(center, dtype=np.float32)
            # Combine std with high-percentile dynamic range. A digit-bearing
            # tile has both elevated std AND a wide bright/dark spread; a
            # uniform tile has neither. Subtle gradients (anti-aliasing on a
            # blank cell) inflate std a touch but not the spread, so requiring
            # both filters those out.
            if arr.size == 0:
                return 0.0
            p10, p90 = float(np.percentile(arr, 10)), float(np.percentile(arr, 90))
            spread = p90 - p10
            return float(arr.std()) * 0.5 + spread * 0.5
        except ImportError:
            pixels = list(center.getdata())
            if not pixels:
                return 0.0
            m = sum(pixels) / len(pixels)
            sd = (sum((p - m) ** 2 for p in pixels) / len(pixels)) ** 0.5
            return sd

    def _is_blank_cell(self, cell):
        return self._blank_score(cell) < 15

    @staticmethod
    def _otsu_threshold(arr):
        """Otsu's method for bimodal grayscale data."""
        import numpy as np
        hist, _ = np.histogram(arr.ravel(), bins=256, range=(0, 256))
        total = arr.size
        sum_total = float(np.dot(np.arange(256), hist))
        sum_bg = 0.0
        weight_bg = 0
        best_var = -1.0
        best_t = 127
        for t in range(256):
            weight_bg += hist[t]
            if weight_bg == 0:
                continue
            weight_fg = total - weight_bg
            if weight_fg == 0:
                break
            sum_bg += t * hist[t]
            mean_bg = sum_bg / weight_bg
            mean_fg = (sum_total - sum_bg) / weight_fg
            var = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
            if var > best_var:
                best_var = var
                best_t = t
        return best_t

    def _prep_cell(self, cell):
        """Binarise a tile into black-digit-on-white-bg, padded, ready for OCR.

        Returns (base_pil, dilated_pil) — two variants so we can OCR both and
        vote across them. Dilated helps with thin/cartoony strokes.
        """
        import numpy as np

        cell = cell.resize((360, 360), Image.LANCZOS)
        gray = cell.convert("L")
        gray = gray.filter(ImageFilter.MedianFilter(size=3))  # kill texture noise
        arr = np.array(gray)

        # Crop tighter to dodge tile borders / rounded-corner shadow.
        h, w = arr.shape
        my, mx = h // 7, w // 7
        arr = arr[my:h - my, mx:w - mx]

        # Otsu binarise: True = pixel is in the darker cluster.
        t = self._otsu_threshold(arr)
        dark = arr < t

        # Polarity: the digit is the MINORITY cluster (foreground).
        if dark.sum() > dark.size - dark.sum():
            # Most pixels are dark → background is dark, digit is light. Flip.
            dark = ~dark

        # Build black-on-white image (digit = 0, bg = 255).
        binary = np.where(dark, 0, 255).astype(np.uint8)

        # White padding around the digit so tesseract sees a clean margin.
        pad = 50
        canvas = np.full((binary.shape[0] + 2 * pad, binary.shape[1] + 2 * pad),
                         255, dtype=np.uint8)
        canvas[pad:pad + binary.shape[0], pad:pad + binary.shape[1]] = binary
        base = Image.fromarray(canvas)

        # MinFilter expands the darker (low-value) region → thicker strokes.
        dilated = base.filter(ImageFilter.MinFilter(size=3))
        return base, dilated

    def _ocr_candidates(self, cell):
        """OCR a tile cell. Returns ranked [(digit, weight), …]."""
        try:
            base, dilated = self._prep_cell(cell)
        except Exception:
            return []

        candidates = {}
        # PSM 7 = line, 8 = word, 6 = uniform block, 13 = raw line, 10 = char.
        # PSM 10 only fires meaningfully on single digits; we still try it but
        # it carries less weight when the result is a multi-digit token.
        psms = (7, 8, 6, 13, 10)
        for variant_name, img in (("base", base), ("dilated", dilated)):
            for psm in psms:
                try:
                    data = pytesseract.image_to_data(
                        img, output_type=pytesseract.Output.DICT,
                        config=f"--psm {psm} "
                               f"-c tessedit_char_whitelist=0123456789",
                    )
                except Exception:
                    continue
                texts = data.get("text", [])
                confs = data.get("conf", [])
                for text, conf in zip(texts, confs):
                    digits = "".join(ch for ch in text if ch.isdigit())
                    if not digits:
                        continue
                    try:
                        n = int(digits)
                    except ValueError:
                        continue
                    if not (1 <= n <= 15):
                        continue
                    try:
                        conf_v = max(int(float(conf)), 0)
                    except (TypeError, ValueError):
                        conf_v = 0
                    # weight = vote + confidence bonus; dilated variant gets a
                    # slight boost because it's often the only one that reads
                    # cartoony fonts correctly.
                    bonus = 0.15 if variant_name == "dilated" else 0.0
                    candidates[n] = candidates.get(n, 0.0) + 1.0 + conf_v / 100.0 + bonus

        # Fallback: also try image_to_string for PSMs that may yield text but
        # not a confident bounding box (older tesseract builds).
        if not candidates:
            for img in (base, dilated):
                for psm in psms:
                    try:
                        s = pytesseract.image_to_string(
                            img,
                            config=f"--psm {psm} "
                                   f"-c tessedit_char_whitelist=0123456789",
                        )
                    except Exception:
                        continue
                    for token in s.replace("\n", " ").split():
                        digits = "".join(ch for ch in token if ch.isdigit())
                        if not digits:
                            continue
                        try:
                            n = int(digits)
                        except ValueError:
                            continue
                        if 1 <= n <= 15:
                            candidates[n] = candidates.get(n, 0.0) + 1.0
                            break

        return sorted(candidates.items(), key=lambda kv: -kv[1])

    def _ocr_cell(self, cell):
        """Compatibility shim: returns the single best digit or None."""
        cands = self._ocr_candidates(cell)
        return cands[0][0] if cands else None

    # ---------- solve / animate / auto-play ----------

    def _on_record_mode_change(self):
        new_mode = self._record_mode_var.get()
        if new_mode == "full":
            ok = messagebox.askyesno(
                "Full recording",
                "Full recording stores every visited node to disk.\n\n"
                "Cost on a hard puzzle (~150 M nodes):\n"
                "  · solve time roughly doubles\n"
                "  · ~1.2 GB on disk per run\n\n"
                "Sampled (default) keeps detail near root + the solution path "
                "and writes ~10 MB with ~3% overhead.\n\nProceed with full?",
                parent=self.root,
            )
            if not ok:
                self._record_mode_var.set(self._record_mode)
                return
        self._record_mode = new_mode

    def load_run(self):
        if not HAS_PSEARCH:
            self.status.config(text="psearch module not available", fg=DANGER)
            return
        path = filedialog.askopenfilename(
            title="Load search run",
            initialdir=RUNS_DIR if os.path.isdir(RUNS_DIR) else os.path.expanduser("~"),
            filetypes=[("psearch run", "*.psearch"), ("all files", "*.*")],
            parent=self.root,
        )
        if not path:
            return
        try:
            run = Run(path)
        except Exception as ex:
            messagebox.showerror("Load Run", f"Could not open run:\n{ex}", parent=self.root)
            return
        self._run = run
        self._solve_start_state = list(run.start_state)
        self.board = list(run.start_state)
        # Reconstruct Move objects from string names so existing UI bits keep working.
        name_to_move = {m.name: m for m in (MOVE.UP, MOVE.DOWN, MOVE.LEFT, MOVE.RIGHT)}
        self.solution_moves = [name_to_move[n] for n in run.solution_moves if n in name_to_move]
        self.move_count = 0
        self.search_record = []  # tree window will read from self._run instead
        self.search_stats = {
            "iterations": list(run.iterations),
            "nodes_per_depth": list(run.nodes_per_depth),
            "total_nodes": run.total_explored,
        }
        self._total_nodes = run.total_explored
        self._selected_nid = 0
        self._refresh()
        self.status.config(
            text=(f"Loaded {os.path.basename(path)} · "
                  f"{run.total_explored:,} nodes explored, "
                  f"{len(run):,} recorded · solution {len(self.solution_moves)}"),
            fg=SUCCESS,
        )
        # Auto-open the tree window so the user can explore immediately.
        self._open_tree_window()

    def save_run(self):
        if not HAS_PSEARCH:
            self.status.config(text="psearch module not available", fg=DANGER)
            return
        src = getattr(self, "_pending_run_path", None)
        if not src or not os.path.exists(src):
            self.status.config(text="No unsaved run — solve first", fg=WARN)
            return
        default_name = f"run-{time.strftime('%Y%m%d-%H%M%S')}.psearch"
        dest = filedialog.asksaveasfilename(
            title="Save search run",
            initialdir=RUNS_DIR if os.path.isdir(RUNS_DIR) else os.path.expanduser("~"),
            initialfile=default_name,
            defaultextension=".psearch",
            filetypes=[("psearch run", "*.psearch"), ("all files", "*.*")],
            parent=self.root,
        )
        if not dest:
            return
        try:
            # Move main file + sidecar index together.
            if self._run is not None:
                try: self._run.close()
                except Exception: pass
                self._run = None
            os.replace(src, dest)
            idx_src = src + ".idx"
            idx_dest = dest + ".idx"
            if os.path.exists(idx_src):
                os.replace(idx_src, idx_dest)
            self._pending_run_path = dest
            self._run = Run(dest)
            self.status.config(text=f"Saved → {os.path.basename(dest)}", fg=SUCCESS)
        except Exception as ex:
            messagebox.showerror("Save Run", f"Could not save:\n{ex}", parent=self.root)

    def _discard_pending_run(self):
        """Delete the temp recorder file if the user hasn't saved it."""
        if self._run is not None:
            try: self._run.close()
            except Exception: pass
            self._run = None
        path = getattr(self, "_pending_run_path", None)
        if path and path.startswith(os.path.join(RUNS_DIR, ".tmp-")):
            for p in (path, path + ".idx"):
                try:
                    if os.path.exists(p): os.remove(p)
                except Exception:
                    pass
        self._pending_run_path = None

    def solve(self):
        if sorted(self.board) != list(range(16)):
            self.status.config(text="Fix board first", fg=DANGER); return
        if not isSolvable(self.board):
            self.status.config(text="Unsolvable", fg=DANGER); return
        if self.board == END_STATE:
            self.status.config(text="Already solved", fg=SUCCESS); return

        snapshot = list(self.board)
        self._solve_start_state = snapshot[:]
        self.search_record = []
        self.search_stats = {}
        # Discard any prior unsaved temp recording, close any loaded run.
        self._discard_pending_run()
        self.status.config(text="Solving…", fg=PRIMARY)
        self._show_solver_panel()

        mode = self._record_mode
        run_path = None
        recorder = None
        if HAS_PSEARCH and mode != "off":
            ts = time.strftime("%Y%m%d-%H%M%S-%f")
            run_path = os.path.join(RUNS_DIR, f".tmp-{ts}.psearch")
            try:
                recorder = Recorder(run_path, mode, snapshot)
            except Exception as ex:
                recorder = None
                self.status.config(text=f"Recorder init failed: {ex}", fg=WARN)

        self._pending_run_path = run_path

        def run():
            try:
                end = bytes(END_STATE)
                _p, cost, mv = aStar(
                    bytes(snapshot), end,
                    progress=self._on_solver_progress,
                    record=self.search_record,
                    record_max=1500,
                    stats_out=self.search_stats,
                    recorder=recorder,
                )
                self.solution_moves = [m for m in mv if m is not None]
                n = cost[end]
                if recorder is not None and run_path is not None:
                    try:
                        self._run = Run(run_path)
                    except Exception:
                        self._run = None
                self.root.after(0, self._solver_finished, n)
            except Exception as ex:
                self.root.after(0, self._solver_failed, str(ex))

        threading.Thread(target=run, daemon=True).start()

    def _solver_finished(self, n_moves):
        # Stop the live ticker, freeze the final HUD state, switch title to "done".
        self._polling_solver = False
        snap = self._solver_snapshot
        if snap is not None:
            self._render_solver_state(*snap)
        elapsed = time.time() - self._solve_t0
        self.hud_labels["TIME"].config(text=f"{elapsed:.2f}s", fg=SUCCESS)
        self.hud_labels["H"].config(text="0", fg=SUCCESS)
        self.hud_labels["DEPTH"].config(text=str(n_moves))
        # Show goal state in the mini board (all tiles green)
        self._draw_mini_board(END_STATE)
        self._draw_spine(self.solution_moves)
        if hasattr(self, "_solver_title"):
            self._solver_title.config(text="SOLVER ▸ done", fg=SUCCESS)
        self.status.config(
            text=f"Solved in {n_moves} moves — Animate or Auto-play",
            fg=SUCCESS,
        )
        self._update_status()

    def _solver_failed(self, msg):
        self._polling_solver = False
        self.status.config(text=f"Error: {msg}", fg=DANGER)

    def animate(self):
        if not self.solution_moves:
            self.status.config(text="Solve first", fg=WARN); return
        if self.animating or self.playing:
            return
        self.animating = True
        self._animate_step(0)

    def _animate_step(self, i):
        total = len(self.solution_moves)
        if i >= total:
            self.animating = False
            self.move_count = 0
            self._refresh()
            self._celebrate_solved()
            return
        self.counter.config(text=f"Animating  {i + 1} / {total}")
        self.status.config(text="Animating…", fg=PRIMARY)

        m = self.solution_moves[i]
        zi = self.board.index(0)
        zx, zy = zi >> 2, zi & 3
        dx, dy = m.value
        nx, ny = zx + dx, zy + dy
        ni = (nx << 2) + ny
        tile_value = self.board[ni]

        self._slide_tile(tile_value, (nx, ny), (zx, zy),
                         on_done=lambda: self._commit_move(i, zi, ni))

    def _commit_move(self, i, zi, ni):
        m = self.solution_moves[i]
        self.board = apply_move(self.board, zi, m)
        # Brief flash if the tile just landed on its goal cell
        landed_idx = ni  # blank's old position, where the tile now sits... no:
        # actually after apply_move, tile is at zi (old blank). Re-check.
        landed_idx = zi  # tile now sits at zi
        v = self.board[landed_idx]
        if v != 0 and landed_idx == (v - 1):
            self._flash_cell(landed_idx)
        self._refresh()
        self.root.after(INTER_MOVE_MS, lambda: self._animate_step(i + 1))

    def _slide_tile(self, value, src, dst, on_done):
        """Smoothly slide a tile from src=(r,c) to dst=(r,c) using cubic ease-out."""
        sr, sc = src
        dr, dc = dst

        sx = TILE_PAD + sc * (TILE_SIZE + TILE_PAD)
        sy = TILE_PAD + sr * (TILE_SIZE + TILE_PAD)
        ex = TILE_PAD + dc * (TILE_SIZE + TILE_PAD)
        ey = TILE_PAD + dr * (TILE_SIZE + TILE_PAD)

        # Pre-render the static board (everything except the moving tile)
        # and grab a snapshot of the canvas state we keep across frames.
        self.canvas.delete("all")
        for i, v in enumerate(self.board):
            r, c = i // 4, i % 4
            if i == sr * 4 + sc:
                # Where the moving tile starts → draw blank (its destination
                # in source position once moved); we draw it as blank too.
                self._draw_tile(r, c, 0)
            elif v == 0:
                self._draw_tile(r, c, 0)
            else:
                self._draw_tile(r, c, v)

        # Create the floating tile items (rect + text). Track ids for movement.
        in_place = (dr * 4 + dc) == (value - 1)
        fill = TILE_GOAL if in_place else TILE
        text_color = TILE_GOAL_TEXT if in_place else TILE_TEXT
        pts = self._rounded_points(sx, sy, sx + TILE_SIZE, sy + TILE_SIZE, RADIUS)
        rect_id = self.canvas.create_polygon(pts, smooth=True, fill=fill, outline="")
        text_id = self.canvas.create_text(
            sx + TILE_SIZE // 2, sy + TILE_SIZE // 2,
            text=str(value), font=("Helvetica", 26, "bold"), fill=text_color,
        )

        start = time.time()

        def frame():
            t = min(1.0, (time.time() - start) * 1000 / SLIDE_MS)
            eased = 1 - (1 - t) ** 3  # cubic ease-out
            cur_x = sx + (ex - sx) * eased
            cur_y = sy + (ey - sy) * eased
            # Move floating tile to current position
            self.canvas.coords(text_id, cur_x + TILE_SIZE // 2, cur_y + TILE_SIZE // 2)
            new_pts = self._rounded_points(cur_x, cur_y,
                                           cur_x + TILE_SIZE, cur_y + TILE_SIZE, RADIUS)
            self.canvas.coords(rect_id, *new_pts)
            if t < 1.0:
                self.root.after(FRAME_MS, frame)
            else:
                self.canvas.delete(rect_id); self.canvas.delete(text_id)
                on_done()

        frame()

    def _rounded_points(self, x0, y0, x1, y1, r):
        return [
            x0 + r, y0, x1 - r, y0,
            x1, y0,    x1, y0 + r,
            x1, y1 - r, x1, y1,
            x1 - r, y1, x0 + r, y1,
            x0, y1,    x0, y1 - r,
            x0, y0 + r, x0, y0,
        ]

    def _flash_cell(self, idx):
        r, c = idx // 4, idx % 4
        x0 = TILE_PAD + c * (TILE_SIZE + TILE_PAD)
        y0 = TILE_PAD + r * (TILE_SIZE + TILE_PAD)
        pts = self._rounded_points(x0, y0, x0 + TILE_SIZE, y0 + TILE_SIZE, RADIUS)
        glow = self.canvas.create_polygon(pts, smooth=True,
                                          fill=SUCCESS, outline="")
        # Fade out by stepwise color (Tk has no alpha)
        def fade(step=0):
            if step >= 4:
                self.canvas.delete(glow)
                return
            self.root.after(40, lambda: fade(step + 1))
        fade()

    def _celebrate_solved(self):
        # Quick wave: flash each tile bright→back, sweeping diagonally.
        self.status.config(text="✓ Solved", fg=SUCCESS)
        for idx in range(16):
            r, c = idx // 4, idx % 4
            delay = (r + c) * 60
            self.root.after(delay, lambda i=idx: self._wave_tile(i))

    def _wave_tile(self, idx):
        r, c = idx // 4, idx % 4
        v = self.board[idx]
        if v == 0:
            return
        x0 = TILE_PAD + c * (TILE_SIZE + TILE_PAD)
        y0 = TILE_PAD + r * (TILE_SIZE + TILE_PAD)
        pts = self._rounded_points(x0, y0, x0 + TILE_SIZE, y0 + TILE_SIZE, RADIUS)
        flash = self.canvas.create_polygon(pts, smooth=True,
                                           fill="#f9e2af", outline="")
        text = self.canvas.create_text(
            x0 + TILE_SIZE // 2, y0 + TILE_SIZE // 2,
            text=str(v), font=("Helvetica", 26, "bold"), fill=BG,
        )
        self.root.after(180, lambda: (
            self.canvas.delete(flash), self.canvas.delete(text), self._refresh(),
        ))

    # delay (seconds) per keystroke for each speed mode. 0 = no sleep.
    SPEED_PRESETS = {
        "Safe":     0.080,   # 12 TPS — for apps with input lag
        "Normal":   0.030,   # 33 TPS
        "Fast":     0.018,   # ~55 TPS — beats #1 leaderboard time
        "Speedrun": 0.010,   # ~90 TPS — risky, drops possible
        "Max":      0.000,   # let pynput rip; OS may coalesce
    }

    def auto_play(self):
        if not self.solution_moves:
            self.status.config(text="Solve first", fg=WARN); return
        if self.playing or self.animating:
            return
        self.playing = True
        total = len(self.solution_moves)
        speed_label = getattr(self, "_speed_var", None)
        delay = self.SPEED_PRESETS.get(
            speed_label.get() if speed_label else "Fast", 0.018)
        countdown = max(1, int(getattr(self, "_countdown_var", tk.IntVar(value=3)).get()))

        def run():
            try:
                for n in range(countdown, 0, -1):
                    self.root.after(0, lambda v=n: self.status.config(
                        text=f"Switch to the puzzle window — playing in {v}…",
                        fg=WARN,
                    ))
                    time.sleep(1)
                self.root.after(0, lambda: self.status.config(
                    text=f"Auto-playing {total} moves at "
                         f"{1/delay if delay > 0 else float('inf'):.0f} TPS target…",
                    fg=PRIMARY,
                ))
                elapsed = runMoves(self.solution_moves, delay=delay)
                tps = total / elapsed if elapsed > 0 else float('inf')
                msg = (f"Done — {total} moves in {elapsed:.3f}s "
                       f"({tps:.1f} TPS actual)")
                self.root.after(0, lambda: (
                    self.status.config(text=msg, fg=SUCCESS),
                    self.counter.config(text=""),
                ))
            except Exception as ex:
                self.root.after(0, lambda: self.status.config(
                    text=f"Playback error: {ex} (grant Accessibility permission?)",
                    fg=DANGER,
                ))
            finally:
                self.playing = False

        threading.Thread(target=run, daemon=True).start()

    def shuffle_until_easy(self):
        """Reroll random scrambles until one's optimal length is ≤ threshold.

        Useful for leaderboard chasing — most random scrambles are 50+ optimal,
        but a small fraction are ≤ 30. We brute-force until we find one.
        """
        if self.playing or self.animating:
            return
        target = max(10, int(getattr(self, "_easy_target_var",
                                     tk.IntVar(value=30)).get()))
        self.status.config(
            text=f"Hunting for ≤{target}-move scramble…", fg=PRIMARY,
        )

        def hunt():
            from astar import isSolvable, aStar
            tries = 0
            best = None
            while True:
                tries += 1
                b = list(range(16)); random.shuffle(b)
                while not isSolvable(b):
                    random.shuffle(b)
                # Quick optimal solve.
                try:
                    _, cost, mv = aStar(bytes(b), bytes(END_STATE))
                    n = cost[bytes(END_STATE)]
                except Exception:
                    continue
                if best is None or n < best[0]:
                    best = (n, list(b), [m for m in mv if m is not None])
                    self.root.after(0, lambda t=tries, n=n: self.status.config(
                        text=f"Try {t}: best so far {n} moves — searching…",
                        fg=PRIMARY,
                    ))
                if n <= target:
                    break
                if tries >= 400:  # bail eventually
                    break

            n, board, moves = best
            def apply():
                self.board = list(board)
                self.solution_moves = list(moves)
                self._solve_start_state = list(board)
                self.move_count = 0
                self._refresh()
                self.status.config(
                    text=f"Found {n}-move scramble after {tries} tries "
                         f"(target ≤{target}) — Auto-play to attack the leaderboard",
                    fg=SUCCESS,
                )
            self.root.after(0, apply)

        threading.Thread(target=hunt, daemon=True).start()


if __name__ == "__main__":
    root = tk.Tk()
    PuzzleGUI(root)
    root.mainloop()
