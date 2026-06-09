import random
import threading
import time
import tkinter as tk

from PIL import Image, ImageTk, ImageGrab, ImageOps

from astar import aStar, isSolvable, move as apply_move, runMoves
import Move

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
ANIM_DELAY_MS = 160


class PuzzleGUI:
    def __init__(self, root):
        self.root = root
        root.title("15-Puzzle")
        root.configure(bg=BG)
        root.resizable(False, False)

        self.board = list(END_STATE)
        self.solution_moves = []
        self.move_count = 0
        self.animating = False
        self.playing = False
        self._edit_widget = None
        self._fix_queue = []

        self._build_ui()
        self._refresh()

    # ---------- UI construction ----------

    def _build_ui(self):
        header = tk.Frame(self.root, bg=BG)
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

        board_wrap = tk.Frame(self.root, bg=BG)
        board_wrap.grid(row=1, column=0, sticky="n", padx=(24, 12), pady=4)

        size = TILE_SIZE * 4 + TILE_PAD * 5
        self.canvas = tk.Canvas(
            board_wrap, width=size, height=size,
            bg=PANEL, highlightthickness=0,
        )
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._canvas_click)

        side = tk.Frame(self.root, bg=BG)
        side.grid(row=1, column=1, sticky="n", padx=(12, 24), pady=4)

        self._section(side, "INPUT")
        row = tk.Frame(side, bg=BG); row.pack(fill="x")
        self._btn(row, "Paste", self.paste_image, PRIMARY, side="left")
        self._btn(row, "Shuffle", self.randomize, PRIMARY, ghost=True, side="left")
        self._btn(row, "Reset", self.reset, PRIMARY, ghost=True, side="left")

        self._section(side, "SOLVE", pad_top=12)
        row = tk.Frame(side, bg=BG); row.pack(fill="x")
        self._btn(row, "Solve", self.solve, SUCCESS, side="left")
        self._btn(row, "Animate", self.animate, WARN, ghost=True, side="left")
        self._btn(row, "Auto-play", self.auto_play, DANGER, ghost=True, side="left")

        self.preview = tk.Label(
            side, bg=PANEL, fg=MUTED,
            width=22, height=8, padx=6, pady=6,
            font=("Helvetica", 10), justify="center",
            text="No image\n\n"
                 + ("OCR ready" if HAS_OCR else "tesseract not found"),
        )
        self.preview.pack(fill="x", pady=(14, 0))

        footer = tk.Frame(self.root, bg=BG)
        footer.grid(row=2, column=0, columnspan=2, sticky="ew",
                    padx=24, pady=(8, 4))
        self.status = tk.Label(
            footer, text="Ready",
            font=("Helvetica", 11, "bold"),
            bg=BG, fg=MUTED, anchor="w",
        )
        self.status.pack(side="left")
        self.counter = tk.Label(
            footer, text="",
            font=("Helvetica", 10),
            bg=BG, fg=SUBTLE, anchor="e",
        )
        self.counter.pack(side="right")

        hint_row = tk.Frame(self.root, bg=BG)
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

    def _btn(self, parent, text, cmd, color=PRIMARY, ghost=False, side=None):
        bg = BG if ghost else color
        fg = color if ghost else BG
        b = tk.Button(
            parent, text=text, command=cmd,
            font=("Helvetica", 11, "bold"),
            bg=bg, fg=fg,
            activebackground=color, activeforeground=BG,
            relief="flat", borderwidth=0,
            highlightthickness=1 if ghost else 0,
            highlightbackground=color, highlightcolor=color,
            padx=8, pady=7, cursor="hand2",
        )
        if side:
            b.pack(side=side, expand=True, fill="x", padx=2)
        else:
            b.pack(fill="x", pady=2)
        return b

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
        detected = []
        for r in range(4):
            for c in range(4):
                cx0 = int(c * cw + cw * 0.10)
                cy0 = int(r * ch + ch * 0.10)
                cx1 = int((c + 1) * cw - cw * 0.10)
                cy1 = int((r + 1) * ch - ch * 0.10)
                cell = grid.crop((cx0, cy0, cx1, cy1))
                if self._is_blank_cell(cell):
                    detected.append(0); continue
                d = self._ocr_cell(cell)
                detected.append(d if d is not None else -1)
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

    def _find_puzzle_bbox(self, rgb):
        w, h = rgb.size
        px = rgb.load()
        xs, ys = [], []
        step = max(1, min(w, h) // 400)
        for y in range(0, h, step):
            for x in range(0, w, step):
                r, g, b = px[x, y]
                if r < 180 and g > 130 and b > 110 and (g - r) > 25 and g >= b - 20:
                    xs.append(x); ys.append(y)
        if len(xs) < 25:
            return None
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        side = max(x1 - x0, y1 - y0)
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        return (max(0, cx - side // 2), max(0, cy - side // 2),
                min(w, cx + side // 2), min(h, cy + side // 2))

    def _is_blank_cell(self, cell):
        small = cell.resize((24, 24))
        px = small.load()
        sr = sg = sb = 0
        for y in range(24):
            for x in range(24):
                r, g, b = px[x, y]
                sr += r; sg += g; sb += b
        n = 24 * 24
        ar, ag, ab = sr // n, sg // n, sb // n
        brightness = (ar + ag + ab) / 3
        is_teal = ag > ar + 25 and ag > 120
        is_orange = ar > 180 and ar > ab + 40
        return brightness < 110 and not is_teal and not is_orange

    def _ocr_cell(self, cell):
        # Simple: upscale, invert (white digit on dark → dark digit on light),
        # autocontrast, OCR with several PSM modes, majority vote.
        cell = cell.resize((220, 220), Image.LANCZOS)
        gray = ImageOps.invert(cell.convert("L"))
        gray = ImageOps.autocontrast(gray, cutoff=8)

        votes = {}
        for psm in (7, 8, 6, 10, 13):
            text = pytesseract.image_to_string(
                gray,
                config=f"--psm {psm} -c tessedit_char_whitelist=0123456789",
            ).strip()
            for token in text.replace("\n", " ").split():
                try:
                    n = int(token)
                    if 1 <= n <= 15:
                        votes[n] = votes.get(n, 0) + 1
                        break
                except ValueError:
                    pass
        if not votes:
            return None
        return max(votes.items(), key=lambda kv: kv[1])[0]

    # ---------- solve / animate / auto-play ----------

    def solve(self):
        if sorted(self.board) != list(range(16)):
            self.status.config(text="Fix board first", fg=DANGER); return
        if not isSolvable(self.board):
            self.status.config(text="Unsolvable", fg=DANGER); return
        if self.board == END_STATE:
            self.status.config(text="Already solved", fg=SUCCESS); return

        snapshot = list(self.board)
        self.status.config(text="Solving…", fg=PRIMARY)

        def run():
            try:
                end = bytes(END_STATE)
                _p, cost, mv = aStar(bytes(snapshot), end)
                self.solution_moves = [m for m in mv if m is not None]
                n = cost[end]
                self.root.after(0, lambda: (
                    self.status.config(
                        text=f"Solved in {n} moves — Animate or Auto-play",
                        fg=SUCCESS),
                    self._update_status(),
                ))
            except Exception as ex:
                self.root.after(0, lambda: self.status.config(
                    text=f"Error: {ex}", fg=DANGER))

        threading.Thread(target=run, daemon=True).start()

    def animate(self):
        if not self.solution_moves:
            self.status.config(text="Solve first", fg=WARN); return
        if self.animating or self.playing:
            return
        self.animating = True
        total = len(self.solution_moves)

        def step(i):
            if i >= total:
                self.animating = False
                self.move_count = 0
                self._update_status()
                self.status.config(text="Solved", fg=SUCCESS)
                return
            m = self.solution_moves[i]
            zi = self.board.index(0)
            self.board = apply_move(self.board, zi, m)
            self._refresh()
            self.counter.config(text=f"Animating  {i + 1} / {total}")
            self.status.config(text="Animating…", fg=PRIMARY)
            self.root.after(ANIM_DELAY_MS, lambda: step(i + 1))

        step(0)

    def auto_play(self):
        if not self.solution_moves:
            self.status.config(text="Solve first", fg=WARN); return
        if self.playing or self.animating:
            return
        self.playing = True
        total = len(self.solution_moves)

        def run():
            try:
                for n in range(5, 0, -1):
                    self.root.after(0, lambda v=n: self.status.config(
                        text=f"Switch to the puzzle window — playing in {v}…",
                        fg=WARN,
                    ))
                    time.sleep(1)
                self.root.after(0, lambda: self.status.config(
                    text=f"Auto-playing {total} moves…", fg=PRIMARY))
                runMoves(self.solution_moves)
                self.root.after(0, lambda: (
                    self.status.config(text="Auto-play finished", fg=SUCCESS),
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


if __name__ == "__main__":
    root = tk.Tk()
    PuzzleGUI(root)
    root.mainloop()
