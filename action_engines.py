import time
import random
import queue
from typing import Callable
import logging
import threading

import win32gui
import win32con
import win32api

from config import BOT_CONFIG, LogLevel
from core_engine import BaseEngine, OSGlobalLock, WindowTracker
from utils import send_hardware_key_sync, parse_spintax

class AntiAfkEngine(BaseEngine):
    def run(self):
        try:
            while not self.stop_event.is_set():
                sleep_time = random.uniform(20.0, 50.0)
                waited = 0.0
                while waited < sleep_time and not self.stop_event.is_set():
                    self.stop_event.wait(1.0); waited += 1.0

                if self.stop_event.is_set(): break
                if self.pause_event.is_set() or not self.ensure_target(): continue

                stealth = self.get_stealth()
                try:
                    with OSGlobalLock("Global\\StreamBotByAdonis_Master_Mutex"):
                        with self.action_lock:
                            cx, cy = self.tracker.get_current_coords()
                            dx, dy = random.choice([-2, 2]), random.choice([-2, 2])

                            if stealth:
                                lparam = ((cy + dy) << 16) | ((cx + dx) & 0xFFFF)
                                win32api.PostMessage(self.tracker.data.hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
                                time.sleep(0.05)
                                lparam_back = (cy << 16) | (cx & 0xFFFF)
                                win32api.PostMessage(self.tracker.data.hwnd, win32con.WM_MOUSEMOVE, 0, lparam_back)
                            else:
                                try:
                                    sx, sy = win32gui.ClientToScreen(self.tracker.data.hwnd, (cx, cy))
                                    win32api.SetCursorPos((sx + dx, sy + dy))
                                    time.sleep(0.05)
                                    win32api.SetCursorPos((sx, sy))
                                except Exception: pass
                    self.send_log(LogLevel.DEBUG, "Anti-AFK movement performed.")
                except Exception: pass
        except Exception as e:
            self.send_log(LogLevel.ERROR, f"Fatal Crash: {e}"); self.trigger_error()

class ClickEngine(BaseEngine):
    def run(self):
        try:
            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    self.stop_event.wait(BOT_CONFIG["action_pause_wait"]); continue
                if not self.ensure_target(): continue

                stealth = self.get_stealth()
                if stealth: self.stop_event.wait(random.uniform(BOT_CONFIG["stealth_pre_action_min"], BOT_CONFIG["stealth_pre_action_max"]))

                if self.stop_event.is_set(): break

                try:
                    with OSGlobalLock("Global\\StreamBotByAdonis_Master_Mutex"):
                        with self.action_lock:
                            cx, cy = self.tracker.get_current_coords()
                            lparam = (cy << 16) | (cx & 0xFFFF)

                            if not stealth:
                                try:
                                    sx, sy = win32gui.ClientToScreen(self.tracker.data.hwnd, (cx, cy))
                                    win32api.SetCursorPos((sx, sy))
                                except Exception: pass
                            else:
                                win32api.SendMessage(self.tracker.data.hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
                                win32api.SendMessage(self.tracker.data.hwnd, win32con.WM_SETFOCUS, 0, 0)

                            win32api.PostMessage(self.tracker.data.hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
                            win32api.PostMessage(self.tracker.data.hwnd, win32con.WM_LBUTTONUP, 0, lparam)
                    self.send_stat("click")
                except Exception as e:
                    self.send_log(LogLevel.ERROR, f"Click failed: {e}")

                self.stop_event.wait(self.get_interval())
        except Exception as e:
            self.send_log(LogLevel.ERROR, f"Fatal Crash: {e}"); self.trigger_error()

class TypingEngine(BaseEngine):
    def __init__(self, name: str, tracker: WindowTracker, event_queue: queue.Queue, get_interval_func: Callable, get_msgs_func: Callable, instance_lock: threading.Lock, get_stealth_func: Callable, logger: logging.Logger, force_msg_queue: queue.Queue, spintax_enabled_func: Callable):
        super().__init__(name, tracker, event_queue, get_interval_func, instance_lock, get_stealth_func, logger)
        self.get_msgs = get_msgs_func
        self.force_msg_queue = force_msg_queue
        self.spintax_enabled = spintax_enabled_func

    def run(self):
        try:
            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    self.stop_event.wait(BOT_CONFIG["action_pause_wait"]); continue
                if not self.ensure_target(): continue

                final_msg = ""
                if not self.force_msg_queue.empty():
                    final_msg = self.force_msg_queue.get()
                elif self.spintax_enabled():
                    msgs = self.get_msgs()
                    if msgs: final_msg = parse_spintax(random.choice(msgs))
                
                if not final_msg:
                    self.stop_event.wait(1.0); continue
                    
                stealth = self.get_stealth()
                if stealth: self.stop_event.wait(random.uniform(BOT_CONFIG["stealth_pre_action_min"], BOT_CONFIG["stealth_pre_action_max"]))
                if self.stop_event.is_set(): break

                try:
                    with OSGlobalLock("Global\\StreamBotByAdonis_Master_Mutex"):
                        with self.action_lock:
                            self.send_log(LogLevel.INFO, f"Sending Chat: '{final_msg}'")
                            cx, cy = self.tracker.get_current_coords()
                            lparam = (cy << 16) | (cx & 0xFFFF)

                            if not stealth:
                                try:
                                    sx, sy = win32gui.ClientToScreen(self.tracker.data.hwnd, (cx, cy))
                                    win32api.SetCursorPos((sx, sy))
                                except Exception: pass
                            else:
                                win32api.SendMessage(self.tracker.data.hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
                                win32api.SendMessage(self.tracker.data.hwnd, win32con.WM_SETFOCUS, 0, 0)
                                win32api.PostMessage(self.tracker.data.hwnd, win32con.WM_MOUSEMOVE, 0, lparam)

                            win32api.PostMessage(self.tracker.data.hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
                            win32api.PostMessage(self.tracker.data.hwnd, win32con.WM_LBUTTONUP, 0, lparam)

                        self.stop_event.wait(BOT_CONFIG["render_wait_delay"])

                        for char in final_msg:
                            if self.stop_event.is_set() or self.pause_event.is_set(): break
                            with self.action_lock:
                                win32api.PostMessage(self.tracker.data.hwnd, win32con.WM_CHAR, ord(char), 0)

                            if char == ' ': self.stop_event.wait(random.uniform(BOT_CONFIG["type_space_min"], BOT_CONFIG["type_space_max"]))
                            else:
                                if random.random() < BOT_CONFIG["type_pause_chance"]: self.stop_event.wait(random.uniform(BOT_CONFIG["type_pause_min"], BOT_CONFIG["type_pause_max"]))
                                else: self.stop_event.wait(random.uniform(BOT_CONFIG["type_char_delay_min"], BOT_CONFIG["type_char_delay_max"]))

                        if self.stop_event.is_set() or self.pause_event.is_set(): continue
                        self.stop_event.wait(BOT_CONFIG["post_type_delay"])
                        if self.stop_event.is_set() or self.pause_event.is_set(): continue

                        with self.action_lock:
                            if not stealth:
                                send_hardware_key_sync(self.tracker.data.hwnd, win32con.VK_RETURN, self.logger)
                            else:
                                win32api.PostMessage(self.tracker.data.hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0); time.sleep(0.02)  
                                win32api.PostMessage(self.tracker.data.hwnd, win32con.WM_CHAR, win32con.VK_RETURN, 0); time.sleep(0.02)  
                                win32api.PostMessage(self.tracker.data.hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 0)

                        self.send_stat("msg", final_msg)
                    self.stop_event.wait(BOT_CONFIG["global_engine_cooldown"])
                except Exception as e: self.send_log(LogLevel.ERROR, f"Chat error: {e}")

                self.stop_event.wait(self.get_interval())
        except Exception as e:
            self.send_log(LogLevel.ERROR, f"Fatal Crash: {e}"); self.trigger_error()