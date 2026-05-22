"""MACRO_DTC — Smart AI Auto Hunting Macro v14"""

import tkinter as tk
from tkinter import ttk, messagebox
import cv2
import numpy as np
import keyboard
import pydirectinput
import time
import json
import os
import glob
import threading
import random
import mss

pydirectinput.FAILSAFE = True
os.makedirs("macro_profiles", exist_ok=True)

# ── Constants ──────────────────────────────────────────────────────────────────
SETTINGS_FILE = "macro_settings.json"
PROFILES_DIR  = "macro_profiles"
LOG_MAX_LINES = 5000

DEFAULT_SETTINGS: dict = {
    "minimap_area":          {"x": 0, "y": 0, "width": 300, "height": 300},
    "char_color":            [0, 255, 255],
    "left_boundary":         100,
    "right_boundary":        500,
    "monitor_pixel":         {"x": -1, "y": -1},
    "monitor_color":         [-1, -1, -1],
    "jitter_delay":          [-0.02, 0.04],
    "jitter_duration":       [-0.02, 0.05],
    "buffs":                 [],
    "buff_jitter_delay":     [-0.02, 0.04],
    "buff_jitter_duration":  [-0.02, 0.05],
    "game_area":             {"x": 0, "y": 0, "width": 0, "height": 0},
    "smart_radius_x":        150,
    "smart_radius_y":        100,
    "move_skill_key":        "alt",
    "move_skill_cd":         1.0,
    "attack_key":            "ctrl",
    "smart_attack_duration": 0.5,
    "smart_attack_delay":    0.1,
    "smart_move_duration":   0.1,
    "smart_colors":          {"m1": [0, 0, 0], "m2": [0, 0, 0], "char": [0, 0, 0]},
    "m1_min_area":   10,   "m1_max_area":   2000,
    "m2_min_area":   10,   "m2_max_area":   2000,
    "char_min_area": 100,  "char_max_area": 3000,
}

# ── Colour Palette ─────────────────────────────────────────────────────────────
CLR = {
    "bg":      "#1e1e2e",   # main background
    "panel":   "#2a2a3e",   # card background
    "border":  "#3d3d5c",   # separator / border
    "accent":  "#7c6af7",   # primary purple
    "green":   "#4ade80",
    "red":     "#f87171",
    "orange":  "#fb923c",
    "blue":    "#60a5fa",
    "yellow":  "#fbbf24",
    "text":    "#e2e8f0",
    "subtext": "#94a3b8",
    "entry":   "#16162a",
    "btn_g":   "#166534",
    "btn_r":   "#7f1d1d",
    "btn_b":   "#1e3a8a",
    "btn_o":   "#78350f",
    "btn_p":   "#4c1d95",
}

FONT_TITLE  = ("맑은 고딕", 11, "bold")
FONT_LABEL  = ("맑은 고딕",  9)
FONT_MONO   = ("Consolas",   9)
FONT_STATUS = ("맑은 고딕", 18, "bold")
FONT_KEY    = ("맑은 고딕", 12, "bold")


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
        # 게임 인식 영역이 없으면 모니터 전체로 초기화
        if self.data.get("game_area", {}).get("width", 0) <= 5:
            with mss.mss() as sct:
                m = sct.monitors[1]
                self.data["game_area"] = {
                    "x": m["left"], "y": m["top"],
                    "width": m["width"], "height": m["height"],
                }

    def save(self):
        with open(SETTINGS_FILE, "w") as f:
            json.dump(self.data, f, indent=4)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)

    def setdefault(self, key, default):
        return self.data.setdefault(key, default)


# ══════════════════════════════════════════════════════════════════════════════
# Macro Engine  (vision + hunting + buff threads)
# ══════════════════════════════════════════════════════════════════════════════
class MacroEngine:
    def __init__(self, settings: SettingsManager, log_fn):
        self.cfg = settings
        self.log = log_fn

        self.is_hunting         = False
        self.direction          = "right"
        self.char_x             = 0
        self.char_y             = 0
        self.pressed_dir        = None
        self.is_pixel_changed   = False
        self.action_lock        = threading.Lock()
        self.last_buff_times: dict = {}
        self.picking_target     = None

        # Vision shared state
        self.smart_char_pos     = None
        self.smart_monsters: list = []
        self._last_char_pos     = None
        self._last_char_time    = 0.0
        self._last_move_skill_t = 0.0

    # ── Vision ────────────────────────────────────────────────────────────────
    def _find_targets(self, prefix: str, hsv_img):
        h, s, v = self.cfg["smart_colors"][prefix]
        if h == 0 and s == 0 and v == 0:
            return [], []
        h_off = 10
        s_lo, v_lo = (80, 150) if prefix == "char" else (10, 10)
        lo  = np.array([max(0,   h - h_off), max(0,   s - s_lo), max(0,   v - v_lo)], dtype=np.uint8)
        hi  = np.array([min(179, h + h_off), min(255, s + 10),   min(255, v + 10)],   dtype=np.uint8)
        mask = cv2.inRange(hsv_img, lo, hi)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        a_min = self.cfg[f"{prefix}_min_area"]
        a_max = self.cfg[f"{prefix}_max_area"]
        valid = sorted(
            [c for c in cnts if a_min <= cv2.contourArea(c) <= a_max],
            key=cv2.contourArea, reverse=True,
        )
        centers = []
        for c in valid:
            M = cv2.moments(c)
            if M["m00"] != 0:
                centers.append((int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"])))
        return centers, valid

    def vision_loop(self):
        cv2.namedWindow("Live Monitor", cv2.WINDOW_NORMAL)
        cv2.setWindowProperty("Live Monitor", cv2.WND_PROP_TOPMOST, 1)
        last_w = last_h = 0

        with mss.mss() as sct:
            while True:
                try:
                    ga = self.cfg["game_area"]
                    if ga["width"] <= 5:
                        ga = {"x": 0, "y": 0, "width": 800, "height": 600}
                    monitor = {"top": ga["y"], "left": ga["x"],
                               "width": ga["width"], "height": ga["height"]}
                    raw      = np.array(sct.grab(monitor))
                    bgr      = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)
                    hsv      = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

                    if ga["width"] != last_w or ga["height"] != last_h:
                        cv2.resizeWindow("Live Monitor",
                                         max(200, ga["width"] // 2),
                                         max(150, ga["height"] // 2))
                        last_w, last_h = ga["width"], ga["height"]

                    def on_mouse(event, x, y, flags=None, _param=None):
                        if event == cv2.EVENT_LBUTTONDOWN and self.picking_target:
                            rx = max(0, min(x, hsv.shape[1] - 1))
                            ry = max(0, min(y, hsv.shape[0] - 1))
                            hv, sv, vv = hsv[ry, rx]
                            self.cfg["smart_colors"][self.picking_target] = [int(hv), int(sv), int(vv)]
                            messagebox.showinfo(
                                "색상 등록 완료",
                                f"[{self.picking_target}] 등록됨  HSV: {hv}, {sv}, {vv}",
                            )
                            self.picking_target = None

                    cv2.setMouseCallback("Live Monitor", on_mouse)

                    chars, char_cnts = self._find_targets("char", hsv)
                    m1_centers, m1_cnts = self._find_targets("m1", hsv)
                    m2_centers, m2_cnts = self._find_targets("m2", hsv)

                    if chars:
                        self.smart_char_pos  = chars[0]
                        self._last_char_pos  = chars[0]
                        self._last_char_time = time.time()
                    elif self._last_char_pos and (time.time() - self._last_char_time < 2.0):
                        self.smart_char_pos = self._last_char_pos
                    else:
                        self.smart_char_pos = None

                    self.smart_monsters = m1_centers + m2_centers

                    cv2.drawContours(bgr, m1_cnts,   -1, (0, 255, 0),   -1)
                    cv2.drawContours(bgr, m2_cnts,   -1, (0, 165, 255), -1)
                    cv2.drawContours(bgr, char_cnts, -1, (255, 255, 0), -1)
                    for c in m1_cnts:
                        x, y, w, h = cv2.boundingRect(c)
                        cv2.rectangle(bgr, (x, y), (x + w, y + h), (0, 200, 0), 2)
                    for c in m2_cnts:
                        x, y, w, h = cv2.boundingRect(c)
                        cv2.rectangle(bgr, (x, y), (x + w, y + h), (0, 140, 255), 2)
                    for c in char_cnts:
                        x, y, w, h = cv2.boundingRect(c)
                        cv2.rectangle(bgr, (x, y), (x + w, y + h), (200, 200, 0), 2)

                    if self.smart_char_pos:
                        cx, cy = self.smart_char_pos
                        rx = self.cfg["smart_radius_x"]
                        ry = self.cfg["smart_radius_y"]
                        color = (0, 0, 255) if chars else (255, 0, 255)
                        if self.direction == "right":
                            cv2.rectangle(bgr, (cx - 20, cy - ry), (cx + rx, cy + ry), color, 2)
                            cv2.putText(bgr, "FORWARD (R)", (cx - 20, cy - ry - 8),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
                        else:
                            cv2.rectangle(bgr, (cx - rx, cy - ry), (cx + 20, cy + ry), color, 2)
                            cv2.putText(bgr, "FORWARD (L)", (cx - rx, cy - ry - 8),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

                    if self.picking_target:
                        cv2.putText(bgr, "CLICK TARGET ON SCREEN", (20, 35),
                                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)

                    cv2.imshow("Live Monitor", bgr)
                    cv2.waitKey(1)
                except Exception:
                    pass
                time.sleep(0.01)

    # ── Minimap position ──────────────────────────────────────────────────────
    def get_char_position(self):
        try:
            area = self.cfg["minimap_area"]
            if area["width"] <= 5 or area["height"] <= 5:
                return None
            with mss.mss() as sct:
                monitor = {"top": area["y"], "left": area["x"],
                           "width": area["width"], "height": area["height"]}
                frame = np.array(sct.grab(monitor))[:, :, :3]
            target = self.cfg["char_color"]
            lo = np.array([max(0, c - 10) for c in target])
            hi = np.array([min(255, c + 10) for c in target])
            mask = cv2.inRange(frame, lo, hi)
            y_coords, x_coords = np.where(mask > 0)
            if len(x_coords) > 0:
                return int(np.mean(x_coords)), int(np.mean(y_coords))
        except Exception:
            pass
        return None

    # ── Pixel watcher ─────────────────────────────────────────────────────────
    def check_pixel_change(self):
        px = self.cfg.get("monitor_pixel", {}).get("x", -1)
        py = self.cfg.get("monitor_pixel", {}).get("y", -1)
        if px == -1:
            return
        try:
            with mss.mss() as sct:
                frame = np.array(sct.grab({"top": py, "left": px, "width": 1, "height": 1}))
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

    # ── Key helpers ───────────────────────────────────────────────────────────
    def _release_all(self):
        try:
            pydirectinput.keyUp("left")
            pydirectinput.keyUp("right")
            pydirectinput.keyUp(self.cfg.get("attack_key", "ctrl"))
            mk = self.cfg.get("move_skill_key", "")
            if mk: pydirectinput.keyUp(mk)
            for b in self.cfg.get("buffs", []):
                if b.get("key"): pydirectinput.keyUp(b["key"])
                
            if self.pressed_dir:
                self.log(f"⏹  [정지] '{self.pressed_dir}' 방향키 뗌")
                self.pressed_dir = None
        except Exception:
            pass

    def _interruptible_sleep(self, duration: float, interval: float = 0.05) -> bool:
        """Sleep in small chunks; returns False if hunting stopped mid-sleep."""
        start = time.time()
        while time.time() - start < duration:
            if not self.is_hunting:
                return False
            time.sleep(min(interval, max(0.0, duration - (time.time() - start))))
        return True

    # ── Hunting loop ──────────────────────────────────────────────────────────
    def hunting_loop(self):
        last_not_found_log = 0.0
        while True:
            if not self.is_hunting:
                if self.pressed_dir:
                    pydirectinput.keyUp(self.pressed_dir)
                    self.pressed_dir = None
                time.sleep(0.1)
                continue

            # 실시간 미니맵 좌표 갱신 (경계 턴 딜레이 최소화)
            pos = self.get_char_position()
            if pos:
                self.char_x, self.char_y = pos

            if self.smart_char_pos is None:
                self._release_all()
                if time.time() - last_not_found_log > 3.0:
                    self.log("⚠  캐릭터를 인식하지 못했습니다. 색상을 먼저 등록해주세요.")
                    last_not_found_log = time.time()
                time.sleep(0.05)
                continue

            jd = self.cfg.get("jitter_delay",    [-0.02, 0.04])
            ju = self.cfg.get("jitter_duration", [-0.02, 0.05])

            # 경계 도달 시 방향 전환
            if self.direction == "right" and self.char_x >= self.cfg["right_boundary"]:
                if self.pressed_dir == "right":
                    pydirectinput.keyUp("right")
                    self.log("⏹  'right' 방향키 뗌 (경계 턴)")
                    self.pressed_dir = None
                time.sleep(0.15)
                self.direction = "left"
                self.log(f"🔄 우측 경계({self.cfg['right_boundary']}) 도달 → 좌로 전환")

            elif self.direction == "left" and self.char_x <= self.cfg["left_boundary"]:
                if self.pressed_dir == "left":
                    pydirectinput.keyUp("left")
                    self.log("⏹  'left' 방향키 뗌 (경계 턴)")
                    self.pressed_dir = None
                time.sleep(0.15)
                self.direction = "right"
                self.log(f"🔄 좌측 경계({self.cfg['left_boundary']}) 도달 → 우로 전환")

            # 방향키 유지
            if self.pressed_dir != self.direction:
                if self.pressed_dir:
                    pydirectinput.keyUp(self.pressed_dir)
                pydirectinput.keyDown(self.direction)
                self.pressed_dir = self.direction
                self.log(f"▶  [{self.direction.upper()}] 이동 시작")

            # 전방 몬스터 탐색
            target = None
            cx, cy = self.smart_char_pos
            rx = self.cfg["smart_radius_x"]
            ry = self.cfg["smart_radius_y"]
            for m in self.smart_monsters:
                if abs(m[1] - cy) > ry:
                    continue
                if self.direction == "right" and -20 <= (m[0] - cx) <= rx:
                    target = m
                    break
                if self.direction == "left" and -20 <= (cx - m[0]) <= rx:
                    target = m
                    break

            if target:
                ak    = self.cfg["attack_key"]
                a_dur = max(0.01, self.cfg.get("smart_attack_duration", 0.5) + random.uniform(ju[0], ju[1]))
                a_del = max(0.00, self.cfg.get("smart_attack_delay",   0.1) + random.uniform(jd[0], jd[1]))
                if self.action_lock.acquire(blocking=False):
                    try:
                        self.log(f"⚔  공격 '{ak}' | 선딜: {a_del:.2f}s | 지속: {a_dur:.2f}s")
                        if self._interruptible_sleep(a_del) and self.is_hunting:
                            pydirectinput.keyDown(ak)
                            self._interruptible_sleep(a_dur)
                            pydirectinput.keyUp(ak)
                    finally:
                        self.action_lock.release()
            else:
                mk = self.cfg.get("move_skill_key", "")
                cd = self.cfg.get("move_skill_cd",  1.0)
                if mk and (time.time() - self._last_move_skill_t > cd):
                    m_dur = max(0.01, self.cfg.get("smart_move_duration", 0.1) + random.uniform(ju[0], ju[1]))
                    if self.action_lock.acquire(blocking=False):
                        try:
                            self.log(f"💨 이동 스킬 '{mk}' | 지속: {m_dur:.2f}s")
                            pydirectinput.keyDown(mk)
                            self._interruptible_sleep(m_dur)
                            pydirectinput.keyUp(mk)
                            self._last_move_skill_t = time.time()
                        finally:
                            self.action_lock.release()

            time.sleep(0.05)

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
            ju    = self.cfg.get("buff_jitter_duration", [-0.02, 0.05])
            for i, buff in enumerate(buffs):
                if not self.is_hunting:
                    break
                cd  = buff.get("cooldown", 180.0)
                key = buff.get("key", "")
                if not key or (now - self.last_buff_times.get(i, 0)) < cd:
                    continue
                a_del = max(0.01, buff.get("delay",    0.5) + random.uniform(jd[0], jd[1]))
                a_dur = max(0.01, buff.get("duration", 0.2) + random.uniform(ju[0], ju[1]))
                with self.action_lock:
                    if not self.is_hunting:
                        break
                    time.sleep(a_del)
                    self.log(f"✨ 버프 '{key}' 사용!")
                    try:
                        pydirectinput.keyDown(key)
                        time.sleep(a_dur)
                        pydirectinput.keyUp(key)
                    except Exception:
                        pass
                self.last_buff_times[i] = time.time()
            time.sleep(0.1)

    # ── Toggle ────────────────────────────────────────────────────────────────
    def toggle(self):
        self.is_hunting = not self.is_hunting
        if self.is_hunting:
            self.log("🚀 사냥 시작!")
        else:
            self._release_all()
            self.log("🛑 사냥 중지.")
        return self.is_hunting

    def start_threads(self):
        for fn in (self.hunting_loop, self.vision_loop, self.buff_loop):
            threading.Thread(target=fn, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════════════
# UI helpers
# ══════════════════════════════════════════════════════════════════════════════
def styled_button(parent, text, bg, command, width=None, height=None, fg="white", font=FONT_LABEL):
    kw = dict(text=text, bg=bg, fg=fg, font=font, relief="flat",
               activebackground=bg, activeforeground=fg, cursor="hand2",
               command=command, padx=8, pady=4)
    if width:  kw["width"]  = width
    if height: kw["height"] = height
    return tk.Button(parent, **kw)


def section_frame(parent, title: str) -> tk.Frame:
    lf = tk.LabelFrame(parent, text=f"  {title}  ", font=FONT_TITLE,
                        bg=CLR["panel"], fg=CLR["text"],
                        bd=1, relief="groove",
                        labelanchor="nw",
                        padx=8, pady=6)
    return lf


def dark_entry(parent, textvariable=None, width=7) -> tk.Entry:
    return tk.Entry(parent, bg=CLR["entry"], fg=CLR["text"],
                    insertbackground=CLR["text"],
                    relief="flat", bd=4,
                    font=FONT_MONO, width=width,
                    textvariable=textvariable)


def label(parent, text, fg=None, font=FONT_LABEL, **kw) -> tk.Label:
    return tk.Label(parent, text=text, bg=CLR["panel"],
                    fg=fg or CLR["subtext"], font=font, **kw)


# ══════════════════════════════════════════════════════════════════════════════
# Main Application
# ══════════════════════════════════════════════════════════════════════════════
class MacroApp:
    def __init__(self):
        self.cfg    = SettingsManager()
        self.engine = MacroEngine(self.cfg, self._log)

        self._last_f5_time  = 0.0
        self._show_log      = True

        self._build_window()
        self._build_style()
        self._build_ui()

        self._log("시스템: MACRO_DTC 시작됨.")
        self._ui_update()
        self._hotkey_poll()
        self.engine.start_threads()
        self.root.mainloop()

    # ── Window ────────────────────────────────────────────────────────────────
    def _build_window(self):
        self.root = tk.Tk()
        self.root.title("MACRO_DTC - Smart AI Macro v14")
        self.root.geometry("1220x900")
        self.root.configure(bg=CLR["bg"])
        self.root.attributes("-topmost", True)
        self.root.resizable(True, True)

    def _build_style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TNotebook",
                     background=CLR["panel"], borderwidth=0)
        s.configure("TNotebook.Tab",
                     background=CLR["border"], foreground=CLR["subtext"],
                     padding=[10, 5], font=FONT_LABEL)
        s.map("TNotebook.Tab",
              background=[("selected", CLR["accent"])],
              foreground=[("selected", "white")])
        s.configure("TCombobox",
                     fieldbackground=CLR["entry"], background=CLR["panel"],
                     foreground=CLR["text"], arrowcolor=CLR["text"])
        s.configure("Horizontal.TScale",
                     background=CLR["panel"], troughcolor=CLR["border"],
                     sliderlength=18)
        s.configure("Vertical.TScrollbar",
                     background=CLR["border"], troughcolor=CLR["entry"],
                     arrowcolor=CLR["text"])

    # ── Master layout ─────────────────────────────────────────────────────────
    def _build_ui(self):
        # Three columns
        self._col_left  = tk.Frame(self.root, bg=CLR["bg"], width=310)
        self._col_mid   = tk.Frame(self.root, bg=CLR["bg"], width=440)
        self._col_right = tk.Frame(self.root, bg=CLR["bg"])
        for col in (self._col_left, self._col_mid, self._col_right):
            col.pack(side="left", fill="y", padx=6, pady=6)
        self._col_right.pack(fill="both", expand=True)

        self._build_left()
        self._build_mid()
        self._build_right()

    # ── Left column ───────────────────────────────────────────────────────────
    def _build_left(self):
        p = self._col_left

        # ── Status card ──────────────────────────────────────────────────────
        status_card = tk.Frame(p, bg=CLR["panel"], bd=0, relief="flat",
                                padx=10, pady=10)
        status_card.pack(fill="x", pady=(0, 6))

        label(status_card, "캐릭터 좌표", font=FONT_TITLE, fg=CLR["text"]).pack()
        self._coord_label = tk.Label(
            status_card, text="탐색 중...",
            font=FONT_STATUS, fg=CLR["blue"], bg=CLR["panel"])
        self._coord_label.pack(pady=4)

        self._status_label = tk.Label(
            status_card, text="● 대기 중",
            font=("맑은 고딕", 11, "bold"), fg=CLR["red"], bg=CLR["panel"])
        self._status_label.pack(pady=2)

        self._toggle_btn = styled_button(
            status_card, "▶  사냥 시작  (F5)",
            CLR["btn_g"], self._toggle_hunting,
            width=24, font=FONT_KEY)
        self._toggle_btn.pack(pady=8)

        # ── Minimap section ───────────────────────────────────────────────────
        sf = section_frame(p, "🗺  미니맵 설정")
        sf.pack(fill="x", pady=4)
        a = self.cfg.get("minimap_area", {"x": 0, "y": 0, "width": 0, "height": 0})
        c = self.cfg.get("char_color",   [0, 0, 0])
        self._area_label  = label(sf, f"범위: X={a['x']} Y={a['y']} W={a['width']} H={a['height']}")
        self._area_label.pack(anchor="w")
        self._color_label = label(sf, f"캐릭터 색상: BGR {c}")
        self._color_label.pack(anchor="w")
        btn_row = tk.Frame(sf, bg=CLR["panel"]); btn_row.pack(pady=4)
        styled_button(btn_row, "드래그 설정", CLR["btn_b"], self._pick_minimap).grid(row=0, column=0, padx=3)
        styled_button(btn_row, "색상 찍기",   CLR["btn_p"], self._pick_char_color).grid(row=0, column=1, padx=3)

        # ── Pixel watcher ─────────────────────────────────────────────────────
        pf = section_frame(p, "📍 1픽셀 변화 감지")
        pf.pack(fill="x", pady=4)
        px = self.cfg.get("monitor_pixel", {}).get("x", -1)
        py = self.cfg.get("monitor_pixel", {}).get("y", -1)
        self._px_label = label(pf, "감시 없음" if px == -1 else f"X={px} Y={py}")
        self._px_label.pack(anchor="w")
        styled_button(pf, "픽셀 찍기", CLR["btn_b"], self._pick_monitor_pixel).pack(pady=3)

        # ── Boundaries ───────────────────────────────────────────────────────
        bf = section_frame(p, "↔  이동 경계")
        bf.pack(fill="x", pady=4)
        row = tk.Frame(bf, bg=CLR["panel"]); row.pack()
        label(row, "좌:").grid(row=0, column=0, padx=4)
        self._ent_left = dark_entry(row, width=6)
        self._ent_left.insert(0, str(self.cfg.get("left_boundary", 100)))
        self._ent_left.grid(row=0, column=1, padx=4)
        label(row, "우:").grid(row=0, column=2, padx=4)
        self._ent_right = dark_entry(row, width=6)
        self._ent_right.insert(0, str(self.cfg.get("right_boundary", 500)))
        self._ent_right.grid(row=0, column=3, padx=4)

        # ── Jitter ────────────────────────────────────────────────────────────
        jf = section_frame(p, "🎲 랜덤 오차 (초)")
        jf.pack(fill="x", pady=4)
        grid = tk.Frame(jf, bg=CLR["panel"]); grid.pack()

        def jitter_row(parent, text, row_idx, key):
            vals = self.cfg.get(key, [-0.02, 0.04])
            label(parent, text).grid(row=row_idx, column=0, sticky="w", pady=2)
            e_min = dark_entry(parent, width=6); e_min.insert(0, str(vals[0]))
            e_min.grid(row=row_idx, column=1, padx=3)
            label(parent, "~").grid(row=row_idx, column=2)
            e_max = dark_entry(parent, width=6); e_max.insert(0, str(vals[1]))
            e_max.grid(row=row_idx, column=3, padx=3)
            return e_min, e_max

        self._jd_min, self._jd_max = jitter_row(grid, "공격 대기:", 0, "jitter_delay")
        self._ju_min, self._ju_max = jitter_row(grid, "공격 유지:", 1, "jitter_duration")
        self._bd_min, self._bd_max = jitter_row(grid, "버프 대기:", 2, "buff_jitter_delay")

        # ── Bottom buttons ────────────────────────────────────────────────────
        bb = tk.Frame(p, bg=CLR["bg"]); bb.pack(fill="x", pady=6)
        styled_button(bb, "💾  좌측 설정 저장", CLR["btn_g"],
                      self._save_common, width=24).pack(pady=3)
        styled_button(bb, "🛡  버프 설정",       CLR["btn_p"],
                      self._open_buff_editor, width=24).pack(pady=3)
        self._log_btn = styled_button(
            bb, "📝  로그 숨기기", CLR["btn_o"],
            self._toggle_log, width=24)
        self._log_btn.pack(pady=3)

    # ── Middle column ─────────────────────────────────────────────────────────
    def _build_mid(self):
        p = self._col_mid

        label(p, "🧠  몬스터 인식 사냥 설정",
              font=("맑은 고딕", 12, "bold"), fg=CLR["accent"]).pack(pady=(4, 8))

        # ── Game area ────────────────────────────────────────────────────────
        gf = section_frame(p, "🖥  인식 영역")
        gf.pack(fill="x", pady=4)
        ga = self.cfg.get("game_area", {"x": 0, "y": 0, "width": 0, "height": 0})
        self._ga_label = label(gf, f"X={ga['x']} Y={ga['y']} W={ga['width']} H={ga['height']}")
        self._ga_label.pack(anchor="w")
        styled_button(gf, "🖱  화면 드래그로 지정", CLR["btn_b"],
                      self._pick_game_area).pack(pady=4)

        # ── Profile manager ───────────────────────────────────────────────────
        pf = section_frame(p, "💾  사냥터 프로필")
        pf.pack(fill="x", pady=4)
        row1 = tk.Frame(pf, bg=CLR["panel"]); row1.pack(fill="x", pady=2)
        label(row1, "이름:").pack(side="left")
        self._ent_profile = dark_entry(row1, width=16)
        self._ent_profile.pack(side="left", padx=4)
        styled_button(row1, "저장", CLR["btn_g"], self._save_profile).pack(side="left")
        row2 = tk.Frame(pf, bg=CLR["panel"]); row2.pack(fill="x", pady=2)
        self._combo_profiles = ttk.Combobox(row2, values=self._get_profiles(),
                                             state="readonly", width=16, font=FONT_MONO)
        self._combo_profiles.pack(side="left", padx=4)
        styled_button(row2, "불러오기", CLR["btn_o"], self._load_profile).pack(side="left")

        # ── Attack / move settings ────────────────────────────────────────────
        af = section_frame(p, "⚔  사냥 방식")
        af.pack(fill="x", pady=4)

        def trace_entry(parent, text, key, width=5, is_float=False):
            f = tk.Frame(parent, bg=CLR["panel"]); f.pack(side="left", padx=5)
            label(f, text).pack()
            var = tk.StringVar(value=str(self.cfg[key]))
            ent = dark_entry(f, textvariable=var, width=width)
            ent.pack()
            def on_change(*_):
                try:
                    self.cfg[key] = float(var.get()) if is_float else int(var.get())
                except Exception:
                    pass
            var.trace_add("write", on_change)
            return var, ent

        r0 = tk.Frame(af, bg=CLR["panel"]); r0.pack(fill="x", pady=2)
        self._v_rx, _ = trace_entry(r0, "인식 X범위", "smart_radius_x")
        self._v_ry, _ = trace_entry(r0, "인식 Y범위", "smart_radius_y")

        r1 = tk.Frame(af, bg=CLR["panel"]); r1.pack(fill="x", pady=2)
        label(r1, "공격 키:").pack(side="left")
        self._ent_ak = dark_entry(r1, width=5)
        self._ent_ak.insert(0, self.cfg["attack_key"])
        self._ent_ak.pack(side="left", padx=3)
        self._ent_ak.bind("<KeyRelease>",
            lambda e: self.cfg.__setitem__("attack_key", self._ent_ak.get().strip().lower()))
        _, self._ent_ad = trace_entry(r1, "유지(초)", "smart_attack_duration", is_float=True)
        _, self._ent_al = trace_entry(r1, "선딜(초)", "smart_attack_delay",    is_float=True)

        r2 = tk.Frame(af, bg=CLR["panel"]); r2.pack(fill="x", pady=2)
        label(r2, "이동 키:").pack(side="left")
        self._ent_mk = dark_entry(r2, width=5)
        self._ent_mk.insert(0, self.cfg["move_skill_key"])
        self._ent_mk.pack(side="left", padx=3)
        self._ent_mk.bind("<KeyRelease>",
            lambda e: self.cfg.__setitem__("move_skill_key", self._ent_mk.get().strip().lower()))
        _, self._ent_md = trace_entry(r2, "유지(초)", "smart_move_duration", is_float=True)
        _, self._ent_mc = trace_entry(r2, "주기(초)", "move_skill_cd",       is_float=True)

        # ── Target colour tabs ────────────────────────────────────────────────
        nb = ttk.Notebook(p)
        nb.pack(fill="both", expand=True, pady=6)
        self._sliders: dict = {}

        def make_slider(parent, text, skey):
            outer = tk.Frame(parent, bg=CLR["panel"]); outer.pack(fill="x", padx=8, pady=4)
            label(outer, text, fg=CLR["text"]).pack(anchor="w")
            inner = tk.Frame(outer, bg=CLR["panel"]); inner.pack(fill="x")
            val = tk.StringVar(value=str(self.cfg[skey]))
            def scale_cb(v):
                iv = int(float(v)); val.set(str(iv)); self.cfg[skey] = iv
            def entry_cb(*_):
                try: iv = int(val.get()); scale.set(iv); self.cfg[skey] = iv
                except Exception: pass
            val.trace_add("write", entry_cb)
            scale = ttk.Scale(inner, from_=1, to=5000, orient="horizontal",
                              command=scale_cb)
            scale.set(self.cfg[skey])
            scale.pack(side="left", fill="x", expand=True)
            ent = dark_entry(inner, textvariable=val, width=7)
            ent.pack(side="right", padx=4)
            return val

        tabs = [("m1", "🟢 몬스터 1"), ("m2", "🟠 몬스터 2"), ("char", "👤 캐릭터")]
        for prefix, tab_text in tabs:
            frame = ttk.Frame(nb)
            nb.add(frame, text=tab_text)
            inner = tk.Frame(frame, bg=CLR["panel"]); inner.pack(fill="both", expand=True, padx=4, pady=4)
            def _pick(p=prefix):
                self.engine.picking_target = p
                messagebox.showinfo("색상 등록",
                    "오른쪽 Live Monitor 창에서 대상을 직접 클릭하세요!")
            styled_button(inner, "🖱  Live Monitor 에서 색상 찍기",
                          CLR["btn_p"], _pick, height=2).pack(pady=6, fill="x")
            v_min = make_slider(inner, "🔽 최소 크기", f"{prefix}_min_area")
            v_max = make_slider(inner, "🔼 최대 크기", f"{prefix}_max_area")
            self._sliders[prefix] = (v_min, v_max)

    # ── Right column (log) ────────────────────────────────────────────────────
    def _build_right(self):
        p = self._col_right

        header = tk.Frame(p, bg=CLR["panel"], padx=8, pady=6)
        header.pack(fill="x")
        tk.Label(header, text="📝  실시간 입력 로그",
                  font=FONT_TITLE, bg=CLR["panel"], fg=CLR["text"]).pack(side="left")
        styled_button(header, "지우기", CLR["btn_r"], self._clear_log).pack(side="right")

        self._log_frame = tk.Frame(p, bg=CLR["panel"]); self._log_frame.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(self._log_frame, orient="vertical")
        sb.pack(side="right", fill="y")
        self._log_text = tk.Text(
            self._log_frame, yscrollcommand=sb.set,
            font=FONT_MONO, state=tk.DISABLED,
            bg=CLR["entry"], fg=CLR["text"],
            insertbackground=CLR["text"],
            relief="flat", padx=8, pady=6,
            wrap="word",
        )
        self._log_text.pack(fill="both", expand=True)
        sb.config(command=self._log_text.yview)

    # ── Logging ───────────────────────────────────────────────────────────────
    def _log(self, msg: str):
        print(msg)
        if not self._show_log:
            return
        def _update():
            self._log_text.config(state=tk.NORMAL)
            self._log_text.insert(tk.END, msg + "\n")
            if int(self._log_text.index("end-1c").split(".")[0]) > LOG_MAX_LINES:
                self._log_text.delete("1.0", "2.0")
            self._log_text.see(tk.END)
            self._log_text.config(state=tk.DISABLED)
        self.root.after(0, _update)

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

    # ── UI update loop ────────────────────────────────────────────────────────
    def _ui_update(self):
        pos = self.engine.get_char_position()
        if pos:
            self.engine.char_x, self.engine.char_y = pos
            self._coord_label.config(text=f"X: {pos[0]}   Y: {pos[1]}")
        else:
            self._coord_label.config(text="캐릭터 탐색 중...")
        self.engine.check_pixel_change()
        self.root.after(500, self._ui_update)

    def _hotkey_poll(self):
        try:
            if keyboard.is_pressed("f5") and time.time() - self._last_f5_time > 0.5:
                self._toggle_hunting()
                self._last_f5_time = time.time()
        except Exception:
            pass
        self.root.after(30, self._hotkey_poll)

    # ── Hunting toggle ────────────────────────────────────────────────────────
    def _toggle_hunting(self):
        hunting = self.engine.toggle()
        if hunting:
            self._status_label.config(text="● 사냥 중", fg=CLR["green"])
            self._toggle_btn.config(text="⏹  사냥 중지  (F5)", bg=CLR["btn_r"])
        else:
            self._status_label.config(text="● 대기 중", fg=CLR["red"])
            self._toggle_btn.config(text="▶  사냥 시작  (F5)", bg=CLR["btn_g"])

    # ── Settings save ─────────────────────────────────────────────────────────
    def _save_common(self):
        try:
            self.cfg["left_boundary"]     = int(self._ent_left.get())
            self.cfg["right_boundary"]    = int(self._ent_right.get())
            self.cfg["jitter_delay"]      = [float(self._jd_min.get()), float(self._jd_max.get())]
            self.cfg["jitter_duration"]   = [float(self._ju_min.get()), float(self._ju_max.get())]
            self.cfg["buff_jitter_delay"] = [float(self._bd_min.get()), float(self._bd_max.get())]
            self.cfg.save()
            self._status_label.config(text="✔ 설정 저장됨", fg=CLR["blue"])
            self.root.after(2000, lambda: self._status_label.config(
                text="● 대기 중" if not self.engine.is_hunting else "● 사냥 중"))
        except ValueError as e:
            messagebox.showerror("저장 오류", str(e))

    # ── Area / colour pickers ─────────────────────────────────────────────────
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
            x = min(sx, e.x); y = min(sy, e.y)
            w = abs(e.x - sx); h = abs(e.y - sy)
            sel.destroy()
            on_done(x, y, w, h)
        canvas.bind("<ButtonPress-1>",   press)
        canvas.bind("<B1-Motion>",       drag)
        canvas.bind("<ButtonRelease-1>", release)

    def _pick_minimap(self):
        def done(x, y, w, h):
            self.cfg["minimap_area"] = {"x": x, "y": y, "width": w, "height": h}
            self._area_label.config(text=f"범위: X={x} Y={y} W={w} H={h}")
            self.cfg.save()
        self._drag_area(done, outline="red")

    def _pick_game_area(self):
        def done(x, y, w, h):
            if w < 50 or h < 50:
                w, h = 800, 600
            self.cfg["game_area"] = {"x": x, "y": y, "width": w, "height": h}
            self._ga_label.config(text=f"X={x} Y={y} W={w} H={h}")
            self.cfg.save()
        self._drag_area(done, outline="blue")

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
            self._color_label.config(text=f"캐릭터 색상: BGR {self.cfg['char_color']}")
            win.destroy()
        win.bind("<Button-1>", click)

    def _pick_monitor_pixel(self):
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
            self._px_label.config(text=f"감시 중: X={e.x} Y={e.y}")
            win.destroy()
        win.bind("<Button-1>", click)

    # ── Profile manager ───────────────────────────────────────────────────────
    def _get_profiles(self) -> list:
        return [os.path.basename(f).replace(".json", "")
                for f in glob.glob(os.path.join(PROFILES_DIR, "*.json"))]

    def _save_profile(self):
        name = self._ent_profile.get().strip()
        if not name:
            messagebox.showwarning("경고", "프로필 이름을 입력하세요.")
            return
        self.cfg.save()
        path = os.path.join(PROFILES_DIR, f"{name}.json")
        with open(path, "w") as f:
            json.dump(self.cfg.data, f, indent=4)
        self._combo_profiles.config(values=self._get_profiles())
        self._combo_profiles.set(name)
        messagebox.showinfo("저장 완료", f"'{name}' 프로필이 저장되었습니다.")

    def _load_profile(self):
        name = self._combo_profiles.get().strip()
        if not name:
            return
        path = os.path.join(PROFILES_DIR, f"{name}.json")
        with open(path, "r") as f:
            data = json.load(f)
        for k, v in data.items():
            self.cfg[k] = v
        # Sync UI widgets
        self._v_rx.set(str(self.cfg["smart_radius_x"]))
        self._v_ry.set(str(self.cfg["smart_radius_y"]))
        for w, key in [(self._ent_ak, "attack_key"), (self._ent_mk, "move_skill_key")]:
            w.delete(0, "end"); w.insert(0, str(self.cfg[key]))
        for prefix in ("m1", "m2", "char"):
            self._sliders[prefix][0].set(str(self.cfg[f"{prefix}_min_area"]))
            self._sliders[prefix][1].set(str(self.cfg[f"{prefix}_max_area"]))
        messagebox.showinfo("불러오기 완료", f"'{name}' 프로필을 불러왔습니다.")

    # ── Buff editor ───────────────────────────────────────────────────────────
    def _open_buff_editor(self):
        win = tk.Toplevel(self.root)
        win.title("자동 버프 설정")
        win.geometry("540x480")
        win.attributes("-topmost", True)
        win.configure(bg=CLR["bg"])

        tk.Label(win, text="자동 버프 관리", font=FONT_TITLE,
                  bg=CLR["bg"], fg=CLR["text"]).pack(pady=8)

        list_frame = tk.Frame(win, bg=CLR["panel"], padx=8, pady=8)
        list_frame.pack(fill="both", expand=True, padx=12)

        lb = tk.Listbox(list_frame, font=FONT_MONO,
                         bg=CLR["entry"], fg=CLR["text"],
                         selectbackground=CLR["accent"],
                         relief="flat", bd=0)
        lb.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_frame, command=lb.yview)
        sb.pack(side="right", fill="y")
        lb.config(yscrollcommand=sb.set)

        def refresh():
            lb.delete(0, tk.END)
            for i, b in enumerate(self.cfg.get("buffs", [])):
                lb.insert(tk.END, f"  [{i+1}]  키: {b['key']!r:<8}  주기: {b['cooldown']}s")

        def delete_selected():
            sel = lb.curselection()
            if not sel:
                return
            del self.cfg.data["buffs"][sel[0]]
            self.cfg.save()
            refresh()

        styled_button(list_frame, "삭제", CLR["btn_r"], delete_selected
                      ).pack(side="right", padx=6)

        add_frame = tk.LabelFrame(win, text="  버프 추가  ", font=FONT_LABEL,
                                   bg=CLR["panel"], fg=CLR["subtext"],
                                   padx=12, pady=8)
        add_frame.pack(fill="x", padx=12, pady=8)
        inner = tk.Frame(add_frame, bg=CLR["panel"]); inner.pack()
        label(inner, "사용 키:").grid(row=0, column=0, sticky="w", pady=3)
        ent_key = dark_entry(inner, width=10); ent_key.grid(row=0, column=1, padx=6)
        label(inner, "주기(초):").grid(row=1, column=0, sticky="w", pady=3)
        ent_cd = dark_entry(inner, width=10); ent_cd.insert(0, "180")
        ent_cd.grid(row=1, column=1, padx=6)

        def add_buff():
            k = ent_key.get().strip().lower()
            if not k:
                return
            try:
                cd = float(ent_cd.get())
            except ValueError:
                cd = 180.0
            self.cfg.setdefault("buffs", []).append(
                {"key": k, "cooldown": cd, "delay": 0.5, "duration": 0.2})
            self.cfg.save()
            refresh()
            ent_key.delete(0, tk.END)

        styled_button(inner, "추가", CLR["btn_g"], add_buff).grid(row=1, column=2, padx=8)
        refresh()


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    MacroApp()
