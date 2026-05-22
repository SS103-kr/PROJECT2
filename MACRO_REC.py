"""MACRO_REC — 자동사냥 매크로 Pro v8 (패턴 녹화 방식)"""

import tkinter as tk
from tkinter import ttk, messagebox
import cv2
import numpy as np
import keyboard
import pydirectinput
import time
import json
import os
import threading
import random
import mss

pydirectinput.FAILSAFE = True

# ── Constants ──────────────────────────────────────────────────────────────────
SETTINGS_FILE = "macro_settings.json"
LOG_MAX_LINES = 5000

DEFAULT_SETTINGS: dict = {
    "minimap_area":         {"x": 0, "y": 0, "width": 300, "height": 300},
    "char_color":           [0, 255, 255],
    "left_boundary":        100,
    "right_boundary":       500,
    "patterns":             {"right": [], "left": []},
    "monitor_pixel":        {"x": -1, "y": -1},
    "monitor_color":        [-1, -1, -1],
    "jitter_delay":         [-0.02, 0.04],
    "jitter_duration":      [-0.02, 0.05],
    "buffs":                [],
    "buff_jitter_delay":    [-0.02, 0.04],
    "buff_jitter_duration": [-0.02, 0.05],
}

# ── Colour Palette ─────────────────────────────────────────────────────────────
CLR = {
    "bg":      "#1e1e2e",
    "panel":   "#2a2a3e",
    "border":  "#3d3d5c",
    "accent":  "#7c6af7",
    "green":   "#4ade80",
    "red":     "#f87171",
    "orange":  "#fb923c",
    "blue":    "#60a5fa",
    "yellow":  "#fbbf24",
    "purple":  "#c084fc",
    "text":    "#e2e8f0",
    "subtext": "#94a3b8",
    "entry":   "#16162a",
    "btn_g":   "#166534",
    "btn_r":   "#7f1d1d",
    "btn_b":   "#1e3a8a",
    "btn_o":   "#78350f",
    "btn_p":   "#4c1d95",
    "btn_y":   "#713f12",
}

FONT_TITLE  = ("맑은 고딕", 10, "bold")
FONT_LABEL  = ("맑은 고딕",  8)
FONT_BOLD   = ("맑은 고딕",  8, "bold")
FONT_MONO   = ("Consolas",   8)
FONT_STATUS = ("맑은 고딕", 15, "bold")
FONT_KEY    = ("맑은 고딕", 10, "bold")


# ══════════════════════════════════════════════════════════════════════════════
# Settings Manager
# ══════════════════════════════════════════════════════════════════════════════
class SettingsManager:
    def __init__(self):
        self.data: dict = {}
        self.load()

    def load(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}
        for k, v in DEFAULT_SETTINGS.items():
            self.data.setdefault(k, v)

    def save(self):
        with open(SETTINGS_FILE, "w") as f:
            json.dump(self.data, f, indent=4)

    def __getitem__(self, key):        return self.data[key]
    def __setitem__(self, key, value): self.data[key] = value
    def get(self, key, default=None):  return self.data.get(key, default)
    def setdefault(self, key, d):      return self.data.setdefault(key, d)


# ══════════════════════════════════════════════════════════════════════════════
# Macro Engine
# ══════════════════════════════════════════════════════════════════════════════
class MacroEngine:
    def __init__(self, cfg: SettingsManager, log_fn):
        self.cfg = cfg
        self.log = log_fn

        self.is_hunting        = False
        self.direction         = "right"
        self.char_x            = 0
        self.char_y            = 0
        self.is_pixel_changed  = False
        self.action_lock       = threading.Lock()
        self.last_buff_times: dict = {}
        self.right_pattern: list   = []
        self.left_pattern: list    = []
        self.pressed_dir       = None
        self._sct              = mss.mss()

        self._load_patterns()

    def _load_patterns(self):
        p = self.cfg.get("patterns", {})
        self.right_pattern = p.get("right", [])
        self.left_pattern  = p.get("left",  [])

    def save_patterns(self):
        self.cfg["patterns"] = {
            "right": self.right_pattern,
            "left":  self.left_pattern,
        }
        self.cfg.save()

    # ── Vision helpers ────────────────────────────────────────────────────────
    def get_char_position(self):
        try:
            area = self.cfg["minimap_area"]
            if area["width"] <= 5 or area["height"] <= 5:
                return None
            monitor = {"top": area["y"], "left": area["x"],
                       "width": area["width"], "height": area["height"]}
            frame = np.array(self._sct.grab(monitor))[:, :, :3]
            target = self.cfg["char_color"]
            lo = np.array([max(0,   c - 10) for c in target])
            hi = np.array([min(255, c + 10) for c in target])
            mask = cv2.inRange(frame, lo, hi)
            ys, xs = np.where(mask > 0)
            if len(xs) > 0:
                return int(np.mean(xs)), int(np.mean(ys))
        except Exception:
            pass
        return None

    def check_pixel_change(self):
        px = self.cfg.get("monitor_pixel", {}).get("x", -1)
        py = self.cfg.get("monitor_pixel", {}).get("y", -1)
        if px == -1:
            return
        try:
            frame = np.array(self._sct.grab({"top": py, "left": px, "width": 1, "height": 1}))
            b, g, r = frame[0, 0, :3]
            orig = self.cfg.get("monitor_color", [-1, -1, -1])
            changed = abs(r - orig[0]) > 5 or abs(g - orig[1]) > 5 or abs(b - orig[2]) > 5
            if changed and not self.is_pixel_changed:
                self.log(f"🚨 [변화감지] 픽셀 색상 변화!  X:{px}  Y:{py}")
                self.is_pixel_changed = True
            elif not changed:
                self.is_pixel_changed = False
        except Exception:
            pass

    def _release_all(self):
        try:
            if self.pressed_dir:
                pydirectinput.keyUp(self.pressed_dir)
                self.pressed_dir = None
            for b in self.cfg.get("buffs", []):
                if b.get("key"): pydirectinput.keyUp(b["key"])
            for p in self.right_pattern + self.left_pattern:
                if p.get("key"): pydirectinput.keyUp(p["key"])
        except Exception:
            pass

    def _interruptible_sleep(self, duration: float, abort_fn=None) -> bool:
        start = time.time()
        while time.time() - start < duration:
            if not self.is_hunting:
                return False
            
            # 슬립 중에도 좌표를 갱신하여 경계 조건 확인
            pos = self.get_char_position()
            if pos:
                self.char_x, self.char_y = pos
                
            if abort_fn and abort_fn():
                return False
                
            time.sleep(min(0.05, max(0.0, duration - (time.time() - start))))
        return True

    # ── Pattern recording ─────────────────────────────────────────────────────
    def record_pattern(self, direction_name: str, mode: str = "block") -> list:
        self.log(f"⏺  [{direction_name}] 녹화 시작 ({'블록' if mode == 'block' else '타임라인'} 모드) — F8로 종료")
        recorded = keyboard.record(until="f8")
        actions  = []

        if not recorded:
            self.log("⚠ 녹화된 입력이 없습니다.")
            return []

        if mode == "block":
            pressed: dict = {}
            last_end = recorded[0].time if recorded else 0
            for e in recorded:
                name = str(getattr(e, "name", "")).lower()
                if not name or name == "f8":
                    continue
                if e.event_type == "down":
                    pressed.setdefault(name, e.time)
                elif e.event_type == "up" and name in pressed:
                    start    = pressed.pop(name)
                    delay    = max(0.0, start - last_end)
                    duration = e.time - start
                    actions.append({
                        "type": "block", "key": name,
                        "delay": round(delay, 2), "duration": round(duration, 2),
                    })
                    last_end = e.time

        elif mode == "timeline":
            last_t    = recorded[0].time if recorded else 0
            pressing: set = set()
            for e in recorded:
                name = str(getattr(e, "name", "")).lower()
                if not name or name == "f8":
                    continue
                if e.event_type == "down":
                    if name in pressing:
                        continue
                    pressing.add(name)
                elif e.event_type == "up":
                    pressing.discard(name)
                delay = max(0.0, e.time - last_t)
                actions.append({
                    "type": "timeline", "key": name,
                    "action": e.event_type, "delay": round(delay, 2),
                })
                last_t = e.time

        self.log(f"⏹  [{direction_name}] 녹화 완료 — 총 {len(actions)}개 동작")
        return actions

    # ── Pattern playback ──────────────────────────────────────────────────────
    def play_pattern(self, actions: list, label: str, abort_fn=None):
        if not actions:
            return
        jd  = self.cfg.get("jitter_delay",    [-0.02, 0.04])
        jdu = self.cfg.get("jitter_duration", [-0.02, 0.05])

        for action in actions:
            if not self.is_hunting:
                break
            if abort_fn and abort_fn():
                break
                
            key = action.get("key", "")
            # 이동키는 매크로가 자동 유지하므로 패턴 재생에서는 무시 (충돌 방지)
            if not key or key in ["left", "right"]:
                continue
                
            base_delay   = action.get("delay", 0.1)
            actual_delay = max(0.0, base_delay + random.uniform(jd[0], jd[1]))
            
            if not self._interruptible_sleep(actual_delay, abort_fn):
                return

            if action.get("type") == "timeline":
                act     = action.get("action")
                act_str = "누름↓" if act == "down" else "뗌↑"
                self.log(f"▶  [{label}] '{key}' {act_str} | 대기: {actual_delay:.3f}s")
                with self.action_lock:
                    if not self.is_hunting:
                        return
                    try:
                        if act == "down":
                            pydirectinput.keyDown(key)
                        elif act == "up":
                            pydirectinput.keyUp(key)
                    except Exception as e:
                        self.log(f"⚠ 키 입력 오류 ({key}): {e}")
            else:
                actual_dur = max(0.01, action.get("duration", 0.1) + random.uniform(jdu[0], jdu[1]))
                self.log(f"▶  [{label}] '{key}' | 대기: {actual_delay:.3f}s | 유지: {actual_dur:.3f}s")
                with self.action_lock:
                    if not self.is_hunting:
                        return
                    try:
                        pydirectinput.keyDown(key)
                        if not self._interruptible_sleep(actual_dur):
                            pydirectinput.keyUp(key)
                            return
                        pydirectinput.keyUp(key)
                    except Exception as e:
                        self.log(f"⚠ 키 입력 오류 ({key}): {e}")
            time.sleep(random.uniform(0.01, 0.03))

    # ── Hunting loop ──────────────────────────────────────────────────────────
    def hunting_loop(self):
        while True:
            if not self.is_hunting:
                if self.pressed_dir:
                    pydirectinput.keyUp(self.pressed_dir)
                    self.pressed_dir = None
                time.sleep(0.1)
                continue

            # 실시간 좌표 갱신
            pos = self.get_char_position()
            if pos:
                self.char_x, self.char_y = pos

            # 방향키 계속 유지 (MACRO_DTC.py 방식)
            if self.pressed_dir != self.direction:
                if self.pressed_dir:
                    pydirectinput.keyUp(self.pressed_dir)
                pydirectinput.keyDown(self.direction)
                self.pressed_dir = self.direction
                self.log(f"▶  [{self.direction.upper()}] 자동 이동 시작")

            if self.right_pattern and self.left_pattern:
                if self.direction == "right":
                    self.play_pattern(self.right_pattern, "오른쪽",
                                      abort_fn=lambda: self.char_x >= self.cfg["right_boundary"])
                    if self.char_x >= self.cfg["right_boundary"]:
                        self.log(f"🔄 우측 경계({self.cfg['right_boundary']}) 도달 → 좌로 전환")
                        self.direction = "left"
                else:
                    self.play_pattern(self.left_pattern, "왼쪽",
                                      abort_fn=lambda: self.char_x <= self.cfg["left_boundary"])
                    if self.char_x <= self.cfg["left_boundary"]:
                        self.log(f"🔄 좌측 경계({self.cfg['left_boundary']}) 도달 → 우로 전환")
                        self.direction = "right"
            else:
                time.sleep(0.1)

    # ── Buff loop ─────────────────────────────────────────────────────────────
    def buff_loop(self):
        while True:
            if not self.is_hunting:
                self.last_buff_times.clear()
                time.sleep(0.1)
                continue
            now   = time.time()
            buffs = self.cfg.get("buffs", [])
            jd    = self.cfg.get("buff_jitter_delay",    [-0.02, 0.04])
            jdu   = self.cfg.get("buff_jitter_duration", [-0.02, 0.05])
            for i, buff in enumerate(buffs):
                if not self.is_hunting:
                    break
                key = buff.get("key", "")
                cd  = buff.get("cooldown", 180.0)
                if not key or (now - self.last_buff_times.get(i, 0)) < cd:
                    continue
                a_del = max(0.01, buff.get("delay",    0.5) + random.uniform(jd[0],  jd[1]))
                a_dur = max(0.01, buff.get("duration", 0.2) + random.uniform(jdu[0], jdu[1]))
                
                if not self._interruptible_sleep(a_del):
                    break
                    
                with self.action_lock:
                    if not self.is_hunting:
                        break
                    self.log(f"✨ 버프 '{key}' 사용! (주기: {cd}초)")
                    try:
                        pydirectinput.keyDown(key)
                        self._interruptible_sleep(a_dur)
                        pydirectinput.keyUp(key)
                    except Exception as e:
                        self.log(f"⚠ 버프 입력 오류: {e}")
                self.last_buff_times[i] = time.time()
            time.sleep(0.1)

    # ── Toggle ────────────────────────────────────────────────────────────────
    def toggle(self) -> bool:
        self.is_hunting = not self.is_hunting
        if self.is_hunting:
            self.log("🚀 자동사냥 시작!")
        else:
            self._release_all()
            self.log("🛑 자동사냥 중지.")
        return self.is_hunting

    def start_threads(self):
        for fn in (self.hunting_loop, self.buff_loop):
            threading.Thread(target=fn, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════════════
# UI Helpers
# ══════════════════════════════════════════════════════════════════════════════
def styled_btn(parent, text, bg, cmd, width=None, font=FONT_LABEL, fg="white", **kw):
    b = tk.Button(parent, text=text, bg=bg, fg=fg, font=font,
                  relief="flat", activebackground=bg, activeforeground=fg,
                  cursor="hand2", command=cmd, padx=8, pady=5, **kw)
    if width:
        b.config(width=width)
    return b


def dark_entry(parent, textvariable=None, width=7) -> tk.Entry:
    return tk.Entry(parent, bg=CLR["entry"], fg=CLR["text"],
                    insertbackground=CLR["text"], relief="flat", bd=4,
                    font=FONT_MONO, width=width, textvariable=textvariable)


def lbl(parent, text, fg=None, font=FONT_LABEL, **kw) -> tk.Label:
    return tk.Label(parent, text=text, bg=CLR["panel"],
                    fg=fg or CLR["subtext"], font=font, **kw)


def section(parent, title: str) -> tk.LabelFrame:
    return tk.LabelFrame(parent, text=f"  {title}  ", font=FONT_BOLD,
                          bg=CLR["panel"], fg=CLR["text"],
                          bd=1, relief="groove", labelanchor="nw",
                          padx=6, pady=3)


# ══════════════════════════════════════════════════════════════════════════════
# Main Application
# ══════════════════════════════════════════════════════════════════════════════
class MacroApp:
    def __init__(self):
        self.cfg    = SettingsManager()
        self.engine = MacroEngine(self.cfg, self._log)

        self._show_log    = True
        self._last_f5     = 0.0
        self._recording   = False

        self._build_window()
        self._build_style()
        self._build_ui()

        self._update_pattern_label()
        self._log("시스템: MACRO_REC 시작됨.")
        self._ui_update()
        self._hotkey_poll()
        self.engine.start_threads()
        self.root.mainloop()

    # ── Window / style ────────────────────────────────────────────────────────
    def _build_window(self):
        self.root = tk.Tk()
        self.root.title("MACRO_REC - 자동사냥 매크로 Pro v8")
        self.root.geometry("820x900")
        self.root.configure(bg=CLR["bg"])
        self.root.attributes("-topmost", True)
        self.root.resizable(True, True)

    def _build_style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TNotebook",        background=CLR["panel"], borderwidth=0)
        s.configure("TNotebook.Tab",    background=CLR["border"], foreground=CLR["subtext"],
                    padding=[10, 5], font=FONT_LABEL)
        s.map("TNotebook.Tab",
              background=[("selected", CLR["accent"])],
              foreground=[("selected", "white")])
        s.configure("TCombobox",
                    fieldbackground=CLR["entry"], background=CLR["panel"],
                    foreground=CLR["text"], arrowcolor=CLR["text"])
        s.configure("Vertical.TScrollbar",
                    background=CLR["border"], troughcolor=CLR["entry"],
                    arrowcolor=CLR["text"])

    # ── Master layout ─────────────────────────────────────────────────────────
    def _build_ui(self):
        self._col_left  = tk.Frame(self.root, bg=CLR["bg"], width=330)
        self._col_right = tk.Frame(self.root, bg=CLR["bg"])
        self._col_left.pack(side="left",  fill="y",    padx=6, pady=6)
        self._col_right.pack(side="right", fill="both", expand=True, padx=6, pady=6)

        self._build_left()
        self._build_right()

    # ── Left column ───────────────────────────────────────────────────────────
    def _build_left(self):
        p = self._col_left

        # ── Status card ───────────────────────────────────────────────────────
        card = tk.Frame(p, bg=CLR["panel"], padx=10, pady=6)
        card.pack(fill="x", pady=(0, 4))
        lbl(card, "현재 캐릭터 위치", font=FONT_TITLE, fg=CLR["text"]).pack()
        self._coord_lbl = tk.Label(card, text="탐색 중...",
                                    font=FONT_STATUS, fg=CLR["blue"], bg=CLR["panel"])
        self._coord_lbl.pack(pady=2)
        self._status_lbl = tk.Label(card, text="● 대기 중",
                                     font=("맑은 고딕", 9, "bold"),
                                     fg=CLR["red"], bg=CLR["panel"])
        self._status_lbl.pack(pady=1)
        self._toggle_btn = styled_btn(card, "▶  사냥 시작  (F5)",
                                       CLR["btn_g"], self._toggle, width=22, font=FONT_KEY)
        self._toggle_btn.pack(pady=4)

        # ── Minimap ───────────────────────────────────────────────────────────
        sf = section(p, "🗺  미니맵 / 색상 설정")
        sf.pack(fill="x", pady=2)
        a = self.cfg.get("minimap_area", {"x": 0, "y": 0, "width": 0, "height": 0})
        c = self.cfg.get("char_color",   [0, 0, 0])
        self._area_lbl  = lbl(sf, f"범위: X={a['x']} Y={a['y']} W={a['width']} H={a['height']}")
        self._area_lbl.pack(anchor="w")
        self._color_lbl = lbl(sf, f"캐릭터 색상: BGR {c}")
        self._color_lbl.pack(anchor="w")
        row = tk.Frame(sf, bg=CLR["panel"]); row.pack(pady=2)
        styled_btn(row, "드래그 설정", CLR["btn_b"], self._pick_minimap).grid(row=0, column=0, padx=3)
        styled_btn(row, "색상 찍기",   CLR["btn_p"], self._pick_char_color).grid(row=0, column=1, padx=3)

        # ── Pixel watcher ─────────────────────────────────────────────────────
        pf = section(p, "📍 1픽셀 변화 감지")
        pf.pack(fill="x", pady=2)
        px = self.cfg.get("monitor_pixel", {}).get("x", -1)
        py = self.cfg.get("monitor_pixel", {}).get("y", -1)
        self._px_lbl = lbl(pf, "감시 없음" if px == -1 else f"X={px}  Y={py}")
        self._px_lbl.pack(anchor="w")
        styled_btn(pf, "픽셀 찍기", CLR["btn_b"], self._pick_px).pack(pady=2)

        # ── Boundary ──────────────────────────────────────────────────────────
        bf = section(p, "↔  이동 경계")
        bf.pack(fill="x", pady=2)
        row = tk.Frame(bf, bg=CLR["panel"]); row.pack()
        lbl(row, "좌:").grid(row=0, column=0, padx=4)
        self._ent_left = dark_entry(row, width=6)
        self._ent_left.insert(0, str(self.cfg.get("left_boundary", 100)))
        self._ent_left.grid(row=0, column=1, padx=4)
        lbl(row, "우:").grid(row=0, column=2, padx=4)
        self._ent_right = dark_entry(row, width=6)
        self._ent_right.insert(0, str(self.cfg.get("right_boundary", 500)))
        self._ent_right.grid(row=0, column=3, padx=4)

        # ── Jitter ────────────────────────────────────────────────────────────
        jf = section(p, "🎲 랜덤 오차 (초)")
        jf.pack(fill="x", pady=2)
        grid = tk.Frame(jf, bg=CLR["panel"]); grid.pack()

        def jrow(text, row_idx, key):
            vals = self.cfg.get(key, [-0.02, 0.04])
            lbl(grid, text).grid(row=row_idx, column=0, sticky="w", pady=1, padx=(0, 4))
            e_min = dark_entry(grid, width=6); e_min.insert(0, str(vals[0]))
            e_min.grid(row=row_idx, column=1, padx=2)
            lbl(grid, "~").grid(row=row_idx, column=2)
            e_max = dark_entry(grid, width=6); e_max.insert(0, str(vals[1]))
            e_max.grid(row=row_idx, column=3, padx=2)
            return e_min, e_max

        self._jd_min,  self._jd_max  = jrow("공격 대기:", 0, "jitter_delay")
        self._ju_min,  self._ju_max  = jrow("공격 유지:", 1, "jitter_duration")
        self._bd_min,  self._bd_max  = jrow("버프 대기:", 2, "buff_jitter_delay")
        self._bu_min,  self._bu_max  = jrow("버프 유지:", 3, "buff_jitter_duration")

        # ── Record & pattern controls ─────────────────────────────────────────
        rf = section(p, "⏺  패턴 녹화")
        rf.pack(fill="x", pady=2)

        mode_row = tk.Frame(rf, bg=CLR["panel"]); mode_row.pack(pady=1)
        lbl(mode_row, "모드:").pack(side="left")
        self._mode_var = tk.StringVar(value="block")
        for val, txt in (("block", "블록(기본)"), ("timeline", "타임라인(동시입력)")):
            tk.Radiobutton(mode_row, text=txt, variable=self._mode_var, value=val,
                           bg=CLR["panel"], fg=CLR["text"], selectcolor=CLR["entry"],
                           activebackground=CLR["panel"], font=FONT_LABEL).pack(side="left", padx=4)

        styled_btn(rf, "⏺  공격 패턴 녹화 시작 (F8 종료)", CLR["btn_r"],
                   self._start_record, width=28).pack(pady=2)

        self._pat_lbl = lbl(rf, "저장된 패턴: 오른쪽 0개 / 왼쪽 0개", fg=CLR["yellow"])
        self._pat_lbl.pack(pady=1)
        styled_btn(rf, "🔍 패턴 상세 편집", CLR["btn_p"], self._open_pattern_editor).pack(pady=1)

        # ── Bottom buttons ────────────────────────────────────────────────────
        bb = tk.Frame(p, bg=CLR["bg"]); bb.pack(fill="x", pady=2)
        styled_btn(bb, "💾  설정 저장",       CLR["btn_g"],  self._save_common, width=22).pack(pady=2)
        styled_btn(bb, "🛡  버프 설정",        CLR["btn_p"],  self._open_buff_editor, width=22).pack(pady=2)
        self._log_btn = styled_btn(bb, "📝  로그 숨기기", CLR["btn_o"],
                                    self._toggle_log, width=22)
        self._log_btn.pack(pady=2)

        lbl(p, "▶  시작 / 중지  :  F5 ◀", font=FONT_KEY, fg=CLR["red"]).pack(side="bottom", pady=4)

    # ── Right column (log) ────────────────────────────────────────────────────
    def _build_right(self):
        p = self._col_right

        hdr = tk.Frame(p, bg=CLR["panel"], padx=8, pady=6)
        hdr.pack(fill="x")
        tk.Label(hdr, text="📝  실시간 입력 로그", font=FONT_TITLE,
                  bg=CLR["panel"], fg=CLR["text"]).pack(side="left")
        styled_btn(hdr, "지우기", CLR["btn_r"], self._clear_log).pack(side="right")

        self._log_frame = tk.Frame(p, bg=CLR["panel"])
        self._log_frame.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(self._log_frame, orient="vertical")
        sb.pack(side="right", fill="y")
        self._log_text = tk.Text(
            self._log_frame, yscrollcommand=sb.set,
            font=FONT_MONO, state=tk.DISABLED,
            bg=CLR["entry"], fg=CLR["text"],
            insertbackground=CLR["text"],
            relief="flat", padx=8, pady=6, wrap="word",
        )
        self._log_text.pack(fill="both", expand=True)
        sb.config(command=self._log_text.yview)

    # ── Logging ───────────────────────────────────────────────────────────────
    def _log(self, msg: str):
        print(msg)
        if not self._show_log:
            return
        def _upd():
            self._log_text.config(state=tk.NORMAL)
            self._log_text.insert(tk.END, msg + "\n")
            if int(self._log_text.index("end-1c").split(".")[0]) > LOG_MAX_LINES:
                self._log_text.delete("1.0", "2.0")
            self._log_text.see(tk.END)
            self._log_text.config(state=tk.DISABLED)
        self.root.after(0, _upd)

    def _clear_log(self):
        self._log_text.config(state=tk.NORMAL)
        self._log_text.delete("1.0", tk.END)
        self._log_text.config(state=tk.DISABLED)

    def _toggle_log(self):
        self._show_log = not self._show_log
        if self._show_log:
            self._log_frame.pack(fill="both", expand=True)
            self._log_btn.config(text="📝  로그 숨기기", bg=CLR["btn_o"])
        else:
            self._log_frame.pack_forget()
            self._log_btn.config(text="📝  로그 보이기", bg=CLR["btn_g"])

    # ── UI loops ──────────────────────────────────────────────────────────────
    def _ui_update(self):
        pos = self.engine.get_char_position()
        if pos:
            self.engine.char_x, self.engine.char_y = pos
            self._coord_lbl.config(text=f"X: {pos[0]}   Y: {pos[1]}")
        else:
            self._coord_lbl.config(text="캐릭터 탐색 중...")
        self.engine.check_pixel_change()
        self.root.after(500, self._ui_update)

    def _hotkey_poll(self):
        try:
            if keyboard.is_pressed("f5") and time.time() - self._last_f5 > 0.5:
                self._toggle()
                self._last_f5 = time.time()
        except Exception:
            pass
        self.root.after(30, self._hotkey_poll)

    # ── Toggle hunting ────────────────────────────────────────────────────────
    def _toggle(self):
        if self._recording:
            return
        hunting = self.engine.toggle()
        if hunting:
            self._status_lbl.config(text="● 사냥 중",    fg=CLR["green"])
            self._toggle_btn.config(text="⏹  사냥 중지  (F5)", bg=CLR["btn_r"])
        else:
            self._status_lbl.config(text="● 대기 중",    fg=CLR["red"])
            self._toggle_btn.config(text="▶  사냥 시작  (F5)", bg=CLR["btn_g"])

    # ── Settings save ─────────────────────────────────────────────────────────
    def _save_common(self):
        try:
            self.cfg["left_boundary"]       = int(self._ent_left.get())
            self.cfg["right_boundary"]      = int(self._ent_right.get())
            self.cfg["jitter_delay"]        = [float(self._jd_min.get()), float(self._jd_max.get())]
            self.cfg["jitter_duration"]     = [float(self._ju_min.get()), float(self._ju_max.get())]
            self.cfg["buff_jitter_delay"]   = [float(self._bd_min.get()), float(self._bd_max.get())]
            self.cfg["buff_jitter_duration"]= [float(self._bu_min.get()), float(self._bu_max.get())]
            self.cfg.save()
            self._status_lbl.config(text="✔ 설정 저장됨", fg=CLR["blue"])
            self.root.after(2000, lambda: self._status_lbl.config(
                text="● 사냥 중" if self.engine.is_hunting else "● 대기 중",
                fg=CLR["green"] if self.engine.is_hunting else CLR["red"],
            ))
        except ValueError as e:
            messagebox.showerror("저장 오류", str(e))

    def _update_pattern_label(self):
        r = len(self.engine.right_pattern)
        l = len(self.engine.left_pattern)
        self._pat_lbl.config(text=f"저장된 패턴: 오른쪽 {r}개 / 왼쪽 {l}개")

    # ── Pickers ───────────────────────────────────────────────────────────────
    def _drag_area(self, on_done, outline="red"):
        sel = tk.Toplevel(self.root)
        sel.attributes("-fullscreen", True)
        sel.attributes("-alpha", 0.25)
        sel.config(cursor="cross", bg="black")
        canvas = tk.Canvas(sel, bg="black", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        sx = sy = 0
        rect = None
        def press(e):
            nonlocal sx, sy, rect
            sx, sy = e.x, e.y
            rect = canvas.create_rectangle(sx, sy, sx, sy, outline=outline, width=2)
        def drag(e):
            canvas.coords(rect, sx, sy, e.x, e.y)
        def release(e):
            x, y = min(sx, e.x), min(sy, e.y)
            w, h = abs(e.x - sx), abs(e.y - sy)
            sel.destroy()
            on_done(x, y, w, h)
        canvas.bind("<ButtonPress-1>",   press)
        canvas.bind("<B1-Motion>",       drag)
        canvas.bind("<ButtonRelease-1>", release)

    def _pick_minimap(self):
        def done(x, y, w, h):
            self.cfg["minimap_area"] = {"x": x, "y": y, "width": w, "height": h}
            self._area_lbl.config(text=f"범위: X={x} Y={y} W={w} H={h}")
            self.cfg.save()
        self._drag_area(done, outline="red")

    def _pick_char_color(self):
        with mss.mss() as sct:
            screen = np.array(sct.grab(sct.monitors[1]))[:, :, :3]
        win = tk.Toplevel(self.root)
        win.attributes("-fullscreen", True)
        win.attributes("-alpha", 0.1)
        win.config(cursor="crosshair")
        def click(e):
            b, g, r = screen[e.y, e.x]
            self.cfg["char_color"] = [int(b), int(g), int(r)]
            self.cfg.save()
            self._color_lbl.config(text=f"캐릭터 색상: BGR {self.cfg['char_color']}")
            win.destroy()
        win.bind("<Button-1>", click)

    def _pick_px(self):
        with mss.mss() as sct:
            screen = cv2.cvtColor(np.array(sct.grab(sct.monitors[1])), cv2.COLOR_BGRA2RGB)
        win = tk.Toplevel(self.root)
        win.attributes("-fullscreen", True)
        win.attributes("-alpha", 0.1)
        win.config(cursor="crosshair")
        def click(e):
            r, g, b = screen[e.y, e.x]
            self.cfg["monitor_pixel"] = {"x": e.x, "y": e.y}
            self.cfg["monitor_color"] = [int(r), int(g), int(b)]
            self.cfg.save()
            self._px_lbl.config(text=f"X={e.x}  Y={e.y}")
            win.destroy()
        win.bind("<Button-1>", click)

    # ── Pattern recording ─────────────────────────────────────────────────────
    def _start_record(self):
        if self._recording:
            return
        self._recording = True  # 스레드 시작 전에 설정해 레이스 컨디션 방지
        def run():
            try:
                mode = self._mode_var.get()
                self.root.after(0, lambda: self._status_lbl.config(
                    text="⏺ 오른쪽 녹화 중 (F8 종료)", fg=CLR["purple"]))
                self.engine.right_pattern = self.engine.record_pattern("오른쪽", mode)

                self.root.after(0, lambda: self._status_lbl.config(
                    text="⏳ 3초 후 왼쪽 녹화...", fg=CLR["orange"]))
                self._log("⚠  3초 후 왼쪽 방향 녹화 시작. 자리를 잡으세요!")
                time.sleep(3)

                self.root.after(0, lambda: self._status_lbl.config(
                    text="⏺ 왼쪽 녹화 중 (F8 종료)", fg=CLR["purple"]))
                self.engine.left_pattern = self.engine.record_pattern("왼쪽", mode)

                self.engine.save_patterns()
                self.root.after(0, self._update_pattern_label)
                self.root.after(0, lambda: self._status_lbl.config(
                    text="✔ 녹화 완료!", fg=CLR["blue"]))
                self._log("✅ 양방향 패턴 저장 완료.")
            finally:
                self._recording = False

        threading.Thread(target=run, daemon=True).start()

    # ── Pattern editor ────────────────────────────────────────────────────────
    def _open_pattern_editor(self):
        win = tk.Toplevel(self.root)
        win.title("패턴 상세 편집기")
        win.geometry("700x640")
        win.attributes("-topmost", True)
        win.configure(bg=CLR["bg"])

        tk.Label(win, text="녹화된 패턴 편집  (타임라인 / 블록 모두 지원)",
                  font=FONT_TITLE, bg=CLR["bg"], fg=CLR["subtext"]).pack(pady=6)

        list_outer = tk.Frame(win, bg=CLR["bg"]); list_outer.pack(fill="both", expand=True, padx=10)

        def make_panel(parent, title, direction):
            f = tk.Frame(parent, bg=CLR["panel"], padx=4, pady=4)
            f.pack(side="left", fill="both", expand=True, padx=4)
            tk.Label(f, text=title, font=FONT_BOLD, bg=CLR["panel"], fg=CLR["text"]).pack()
            lb = tk.Listbox(f, font=FONT_MONO, bg=CLR["entry"], fg=CLR["text"],
                             selectbackground=CLR["accent"], relief="flat")
            lb.pack(fill="both", expand=True, pady=4)
            btn_row = tk.Frame(f, bg=CLR["panel"]); btn_row.pack(fill="x")
            styled_btn(btn_row, "수정", CLR["btn_b"],
                       lambda: edit_item(direction, lb)).pack(side="left", expand=True, fill="x", padx=2)
            styled_btn(btn_row, "삭제", CLR["btn_r"],
                       lambda: delete_item(direction, lb)).pack(side="right", expand=True, fill="x", padx=2)
            return lb

        lb_r = make_panel(list_outer, "▶  오른쪽 패턴", "right")
        lb_l = make_panel(list_outer, "◀  왼쪽 패턴",   "left")

        def fmt(i, a):
            if a.get("type") == "timeline":
                sym = "↓" if a.get("action") == "down" else "↑"
                return f"{i+1:>2}. [타임] '{a.get('key','?')}' {sym}  대기:{a.get('delay',0)}s"
            return (f"{i+1:>2}. [블록] '{a.get('key','?')}'  "
                    f"대기:{a.get('delay',0)}s  유지:{a.get('duration',0)}s")

        def refresh():
            lb_r.delete(0, tk.END)
            lb_l.delete(0, tk.END)
            for i, a in enumerate(self.engine.right_pattern):
                lb_r.insert(tk.END, f"  {fmt(i, a)}")
            for i, a in enumerate(self.engine.left_pattern):
                lb_l.insert(tk.END, f"  {fmt(i, a)}")

        def delete_item(direction, lb):
            sel = lb.curselection()
            if not sel:
                return
            pat = self.engine.right_pattern if direction == "right" else self.engine.left_pattern
            del pat[sel[0]]
            self.engine.save_patterns()
            self._update_pattern_label()
            refresh()

        def edit_item(direction, lb):
            sel = lb.curselection()
            if not sel:
                return
            pat  = self.engine.right_pattern if direction == "right" else self.engine.left_pattern
            item = pat[sel[0]]
            is_tl = item.get("type") == "timeline"

            ew = tk.Toplevel(win); ew.title("타이밍 수정")
            ew.geometry("300x180"); ew.attributes("-topmost", True)
            ew.configure(bg=CLR["bg"])
            tk.Label(ew, text=f"'{item.get('key','?')}' 키 타이밍 수정",
                      font=FONT_BOLD, bg=CLR["bg"], fg=CLR["text"]).pack(pady=8)
            f1 = tk.Frame(ew, bg=CLR["bg"]); f1.pack(pady=2)
            lbl(f1, "대기(초):").pack(side="left")
            e_del = dark_entry(f1, width=8); e_del.insert(0, str(item.get("delay", 0.0)))
            e_del.pack(side="left", padx=6)
            e_dur = None
            if not is_tl:
                f2 = tk.Frame(ew, bg=CLR["bg"]); f2.pack(pady=2)
                lbl(f2, "유지(초):").pack(side="left")
                e_dur = dark_entry(f2, width=8); e_dur.insert(0, str(item.get("duration", 0.0)))
                e_dur.pack(side="left", padx=6)
            else:
                lbl(ew, "※ 타임라인 모드는 유지 시간 없음", fg=CLR["subtext"]).pack()

            def do_save():
                try:
                    item["delay"] = round(float(e_del.get()), 3)
                    if not is_tl and e_dur:
                        item["duration"] = round(float(e_dur.get()), 3)
                    self.engine.save_patterns()
                    refresh()
                    ew.destroy()
                except ValueError:
                    pass

            styled_btn(ew, "저장", CLR["btn_g"], do_save).pack(pady=10)

        # Manual add
        af = tk.LabelFrame(win, text="  동작 직접 추가 (블록 형태)  ", font=FONT_BOLD,
                            bg=CLR["panel"], fg=CLR["text"], padx=8, pady=6)
        af.pack(fill="x", padx=10, pady=8)
        row = tk.Frame(af, bg=CLR["panel"]); row.pack(pady=4)
        lbl(row, "키:").grid(row=0, column=0)
        e_key = dark_entry(row, width=8); e_key.grid(row=0, column=1, padx=4)
        lbl(row, "대기(초):").grid(row=0, column=2)
        e_del = dark_entry(row, width=7); e_del.insert(0, "0.1"); e_del.grid(row=0, column=3, padx=4)
        lbl(row, "유지(초):").grid(row=0, column=4)
        e_dur = dark_entry(row, width=7); e_dur.insert(0, "0.1"); e_dur.grid(row=0, column=5, padx=4)

        def add_to(direction):
            k = e_key.get().strip().lower()
            if not k:
                return
            try:
                item = {"type": "block", "key": k,
                        "delay":    round(float(e_del.get()), 2),
                        "duration": round(float(e_dur.get()), 2)}
                pat = self.engine.right_pattern if direction == "right" else self.engine.left_pattern
                pat.append(item)
                self.engine.save_patterns()
                self._update_pattern_label()
                refresh()
                e_key.delete(0, tk.END)
            except ValueError:
                pass

        brow = tk.Frame(af, bg=CLR["panel"]); brow.pack(fill="x")
        styled_btn(brow, "➕ 오른쪽에 추가", CLR["btn_b"],
                   lambda: add_to("right")).pack(side="left", expand=True, fill="x", padx=4)
        styled_btn(brow, "➕ 왼쪽에 추가",   CLR["btn_p"],
                   lambda: add_to("left")).pack(side="right", expand=True, fill="x", padx=4)

        refresh()

    # ── Buff editor ───────────────────────────────────────────────────────────
    def _open_buff_editor(self):
        win = tk.Toplevel(self.root)
        win.title("자동 버프 설정")
        win.geometry("580x520")
        win.attributes("-topmost", True)
        win.configure(bg=CLR["bg"])

        tk.Label(win, text="🛡  자동 버프 관리", font=FONT_TITLE,
                  bg=CLR["bg"], fg=CLR["text"]).pack(pady=8)

        lf = tk.Frame(win, bg=CLR["panel"], padx=8, pady=8)
        lf.pack(fill="both", expand=True, padx=12)
        lb = tk.Listbox(lf, font=FONT_MONO, bg=CLR["entry"], fg=CLR["text"],
                         selectbackground=CLR["accent"], relief="flat")
        lb.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lf, command=lb.yview)
        sb.pack(side="right", fill="y")
        lb.config(yscrollcommand=sb.set)
        def delete_selected():
            sel = lb.curselection()
            if not sel:
                return
            del self.cfg.data["buffs"][sel[0]]
            self.cfg.save()
            refresh()

        styled_btn(lf, "삭제", CLR["btn_r"], delete_selected).pack(side="right", padx=6)

        def refresh():
            lb.delete(0, tk.END)
            for i, b in enumerate(self.cfg.get("buffs", [])):
                lb.insert(tk.END,
                           f"  [{i+1}]  키: {b['key']!r:<8}  주기: {b['cooldown']}s  "
                           f"대기: {b['delay']}s  유지: {b['duration']}s")

        af = tk.LabelFrame(win, text="  버프 추가  ", font=FONT_BOLD,
                            bg=CLR["panel"], fg=CLR["text"], padx=12, pady=8)
        af.pack(fill="x", padx=12, pady=8)
        g = tk.Frame(af, bg=CLR["panel"]); g.pack()

        lbl(g, "사용 키:").grid(row=0, column=0, sticky="w", pady=3)
        e_key = dark_entry(g, width=10); e_key.grid(row=0, column=1, padx=6)

        def detect_key():
            e_key.delete(0, tk.END)
            e_key.insert(0, "누르는 중...")
            e_key.config(bg="#3a3a1a")
            def wait():
                ev = keyboard.read_event()
                if ev.event_type == keyboard.KEY_DOWN:
                    n = ev.name.lower().replace(" ", "")
                    for mod in ("ctrl", "shift", "alt"):
                        if mod in n:
                            n = mod
                            break
                    self.root.after(0, lambda: (
                        e_key.delete(0, tk.END),
                        e_key.insert(0, n),
                        e_key.config(bg=CLR["entry"]),
                    ))
            threading.Thread(target=wait, daemon=True).start()

        styled_btn(g, "키 감지", CLR["btn_b"], detect_key).grid(row=0, column=2, padx=6)

        fields = [("주기(초):", "180"), ("대기(초):", "0.5"), ("유지(초):", "0.2")]
        entries = []
        for ri, (ltext, default) in enumerate(fields, 1):
            lbl(g, ltext).grid(row=ri, column=0, sticky="w", pady=3)
            e = dark_entry(g, width=10); e.insert(0, default)
            e.grid(row=ri, column=1, padx=6)
            entries.append(e)
        e_cd, e_del, e_dur = entries

        def add_buff():
            k = e_key.get().strip().lower()
            if not k or k == "누르는 중...":
                return
            try:
                self.cfg.setdefault("buffs", []).append({
                    "key":      k,
                    "cooldown": round(float(e_cd.get()),  2),
                    "delay":    round(float(e_del.get()), 2),
                    "duration": round(float(e_dur.get()), 2),
                })
                self.cfg.save()
                refresh()
                e_key.delete(0, tk.END)
            except ValueError:
                messagebox.showerror("입력 오류", "시간 값에는 숫자만 입력하세요.")

        styled_btn(af, "➕ 버프 등록", CLR["btn_g"], add_buff, width=16).pack(pady=6)
        refresh()


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    MacroApp()
