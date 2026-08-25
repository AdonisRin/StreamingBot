import os
import sys
import time
import json
import re
import random
import queue
import ctypes
from ctypes import wintypes
import logging
from logging.handlers import RotatingFileHandler
import threading
import urllib.request
import winsound
import base64
from typing import Optional

import win32api
import win32con
import win32gui

from config import BOT_CONFIG, VK_MAP

def enable_high_dpi_awareness():
    try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try: ctypes.windll.user32.SetProcessDPIAware()
        except Exception: pass

def obfuscate_secret(text: str) -> str:
    if not text: return ""
    try: return base64.b64encode(text[::-1].encode('utf-8')).decode('utf-8')
    except Exception: return ""

def deobfuscate_secret(text: str) -> str:
    if not text: return ""
    try: return base64.b64decode(text.encode('utf-8')).decode('utf-8')[::-1]
    except Exception: return text

def safe_get_float(val_str: str, default_val: float) -> float:
    try: return float(val_str.strip())
    except (ValueError, TypeError): return default_val

def get_app_icon_path() -> Optional[str]:
    base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    ico_path = os.path.join(base_dir, "app_icon.ico")
    png_path = os.path.join(base_dir, "app_icon.png")
    if os.path.exists(ico_path): return ico_path
    if os.path.exists(png_path): return png_path
    return None

def get_appdata_log_path() -> str:
    appdata = os.getenv("APPDATA") or os.path.expanduser("~")
    log_dir = os.path.join(appdata, "StreamBotByAdonis", "logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "rpa_engine.log")

def get_autosave_path() -> str:
    appdata = os.getenv("APPDATA") or os.path.expanduser("~")
    log_dir = os.path.join(appdata, "StreamBotByAdonis", "profiles")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "autosave_profiles.json")

def is_admin() -> bool:
    try: return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except AttributeError: return False

def parse_spintax(text: str) -> str:
    pattern = re.compile(r'\{([^{}]*)\}')
    loop_count = 0
    while (match := pattern.search(text)) and loop_count < 50:
        options = match.group(1).split('|')
        text = text[:match.start()] + random.choice(options) + text[match.end():]
        loop_count += 1
    return text

def play_audio_cue(cue_type: str, enabled: bool):
    if not enabled: return
    def _play():
        try:
            if cue_type == "start": winsound.Beep(1200, 150); winsound.Beep(1600, 150)
            elif cue_type == "stop": winsound.Beep(800, 300)
            elif cue_type == "error": winsound.Beep(500, 400); winsound.Beep(500, 400)
            elif cue_type == "break": winsound.Beep(1000, 150); winsound.Beep(1000, 150)
        except Exception: pass
    threading.Thread(target=_play, daemon=True).start()

def send_discord_webhook(url: str, message: str, bot_name: str):
    if not url or not url.startswith("http"): return
    def _send():
        try:
            req = urllib.request.Request(url, method="POST")
            req.add_header('Content-Type', 'application/json'); req.add_header('User-Agent', 'Mozilla/5.0')
            data = json.dumps({"content": f" **[{bot_name}]** {message}"}).encode("utf-8")
            urllib.request.urlopen(req, data=data, timeout=5)
        except Exception: pass
    threading.Thread(target=_send, daemon=True).start()

def send_hardware_key_sync(hwnd: int, vk_code: int, logger: logging.Logger) -> None:
    scan_code = win32api.MapVirtualKey(vk_code, 0)
    lparam_down = (scan_code << 16) | 1
    lparam_up = (1 << 31) | (1 << 30) | (scan_code << 16) | 1
    try:
        win32gui.SendMessageTimeout(hwnd, win32con.WM_KEYDOWN, vk_code, lparam_down, win32con.SMTO_ABORTIFHUNG, 150)
        time.sleep(BOT_CONFIG["hw_key_duration"])
        win32gui.SendMessageTimeout(hwnd, win32con.WM_KEYUP, vk_code, lparam_up, win32con.SMTO_ABORTIFHUNG, 150)
    except Exception as e: logger.error(f"Hardware key error: {e}")

def create_system_logger() -> logging.Logger:
    logger = logging.getLogger("StreamBotByAdonis")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        try:
            log_file = get_appdata_log_path()
            handler = RotatingFileHandler(log_file, maxBytes=2*1024*1024, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
            logger.addHandler(handler)
        except Exception: pass
    return logger

class NativeHotkeyManager(threading.Thread):
    WM_STOP_HOTKEY_THREAD = win32con.WM_USER + 101
    def __init__(self, callback_queue: queue.Queue, logger: logging.Logger):
        super().__init__(daemon=True, name="HotkeyThread")
        self.callback_queue = callback_queue
        self.logger = logger
        self.user32 = ctypes.windll.user32
        self.thread_id = None
        self.HOTKEYS = {}
        actions = ["target_click", "target_chat", "stop_core", "toggle_core", "pause_click", "boss_key", "kill_all"]
        for idx, action in enumerate(actions, start=1):
            key_str = BOT_CONFIG.get("hotkeys", {}).get(action, "").upper()
            vk_code = VK_MAP.get(key_str)
            if vk_code: self.HOTKEYS[idx] = (vk_code, action)

    def run(self):
        self.thread_id = win32api.GetCurrentThreadId()
        registered = []
        for hotkey_id, (vk, action) in self.HOTKEYS.items():
            if self.user32.RegisterHotKey(None, hotkey_id, 0, vk): registered.append(hotkey_id)
            else: self.logger.warning(f"Unable to register hotkey: {vk} ({action})")
                
        msg = wintypes.MSG()
        while self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == win32con.WM_HOTKEY:
                action_data = self.HOTKEYS.get(msg.wParam)
                if action_data: self.callback_queue.put({"type": "hotkey", "action": action_data[1]})
            elif msg.message == self.WM_STOP_HOTKEY_THREAD: break
            self.user32.TranslateMessage(ctypes.byref(msg))
            self.user32.DispatchMessageW(ctypes.byref(msg))
            
        for hk_id in registered: self.user32.UnregisterHotKey(None, hk_id)

    def stop(self):
        if self.thread_id: self.user32.PostThreadMessageW(self.thread_id, self.WM_STOP_HOTKEY_THREAD, 0, 0)