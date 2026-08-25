import os
import time
import threading
import queue
from typing import Tuple, Callable
import logging

import win32gui
import win32con
import win32api
import win32process
import win32event

from config import BOT_CONFIG, LogLevel, BotState, TargetData

class OSGlobalLock:
    def __init__(self, name: str):
        self.name = name
        self.mutex = win32event.CreateMutex(None, False, self.name)
        
    def __enter__(self):
        win32event.WaitForSingleObject(self.mutex, win32event.INFINITE)
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        try: win32event.ReleaseMutex(self.mutex)
        except Exception: pass
        try:
            if hasattr(self, 'mutex') and self.mutex: win32api.CloseHandle(self.mutex)
        except Exception: pass

class WindowTracker:
    def __init__(self) -> None:
        self.data = TargetData()
        self.last_recovery_mode = ""

    def _get_process_name(self, pid: int) -> str:
        try:
            h_process = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
            exe_path = win32process.GetModuleFileNameEx(h_process, 0)
            win32api.CloseHandle(h_process)
            return os.path.basename(exe_path)
        except Exception: return ""

    def set_target_from_mouse(self) -> bool:
        try:
            screen_point = win32api.GetCursorPos()
            hwnd = win32gui.WindowFromPoint(screen_point)
            if not hwnd or not win32gui.IsWindow(hwnd): return False

            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            class_name = win32gui.GetClassName(hwnd)
            exe_name = self._get_process_name(pid)

            rel_x, rel_y = win32gui.ScreenToClient(hwnd, screen_point)
            rect = win32gui.GetClientRect(hwnd)
            width, height = rect[2] - rect[0], rect[3] - rect[1]

            anc_x = "LEFT" if rel_x < (width / 2) else "RIGHT"
            anc_y = "TOP" if rel_y < (height / 2) else "BOTTOM"
            offset_x = rel_x if anc_x == "LEFT" else width - rel_x
            offset_y = rel_y if anc_y == "TOP" else height - rel_y

            parent = hwnd
            for _ in range(BOT_CONFIG["search_window_depth"]):
                p = win32gui.GetParent(parent)
                if p == 0: break
                parent = p

            self.data = TargetData(
                hwnd=hwnd, pid=pid, exe_name=exe_name, title=win32gui.GetWindowText(parent), 
                class_name=class_name, offset_x=offset_x, offset_y=offset_y, 
                anc_x=anc_x, anc_y=anc_y
            )
            return True
        except Exception: return False

    def get_current_coords(self) -> Tuple[int, int]:
        try:
            rect = win32gui.GetClientRect(self.data.hwnd)
            width, height = rect[2] - rect[0], rect[3] - rect[1]
            if width <= 0 or height <= 0: return self.data.offset_x, self.data.offset_y

            cx = self.data.offset_x if self.data.anc_x == "LEFT" else width - self.data.offset_x
            cy = self.data.offset_y if self.data.anc_y == "TOP" else height - self.data.offset_y
            return int(cx), int(cy)
        except Exception: return 0, 0

    def is_valid(self) -> bool:
        if win32gui.IsWindow(self.data.hwnd) == 0: return False
        if win32gui.IsWindowVisible(win32gui.GetAncestor(self.data.hwnd, win32con.GA_ROOT)) == 0: return False
        return True

    def recover(self) -> bool:
        if not self.data.title and not self.data.exe_name: return False
        handles = []
        def title_cb(h, _):
            if win32gui.IsWindowVisible(h) and self.data.title and self.data.title in win32gui.GetWindowText(h): handles.append(h)
        win32gui.EnumWindows(title_cb, None)

        valid_hwnds = []
        for h in handles:
            _, p = win32process.GetWindowThreadProcessId(h)
            if self.data.pid and p == self.data.pid: valid_hwnds.append(h)
            elif self.data.exe_name and self._get_process_name(p) == self.data.exe_name: valid_hwnds.append(h)

        top_hwnd = valid_hwnds[0] if valid_hwnds else (handles[0] if handles else 0)
        if not top_hwnd: return False

        found = [0]
        def child_cb(h, _):
            if win32gui.GetClassName(h) == self.data.class_name: found[0] = h
        try: win32gui.EnumChildWindows(top_hwnd, child_cb, None)
        except Exception: pass

        new_hwnd = found[0] if found[0] != 0 else top_hwnd
        if new_hwnd:
            self.data.hwnd = new_hwnd
            self.last_recovery_mode = "CHILD WINDOW" if new_hwnd == found[0] else "TOP WINDOW"
            try:
                if win32gui.IsIconic(new_hwnd): win32gui.ShowWindow(new_hwnd, win32con.SW_RESTORE)
            except Exception: pass
            return True
        return False

class BaseEngine(threading.Thread):
    def __init__(self, name: str, tracker: WindowTracker, event_queue: queue.Queue, get_interval_func: Callable, instance_lock: threading.Lock, get_stealth_func: Callable, logger: logging.Logger):
        super().__init__(daemon=True, name=name)
        self.tracker = tracker
        self.event_queue = event_queue
        self.get_interval = get_interval_func
        self.action_lock = instance_lock
        self.get_stealth = get_stealth_func
        self.logger = logger
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()

    def send_log(self, level: LogLevel, msg: str) -> None:
        try: self.event_queue.put_nowait({"type": "log", "level": level, "msg": f"[{self.name}] {msg}"})
        except queue.Full: self.logger.warning("Event queue full, message dropped.")

        if level == LogLevel.INFO: self.logger.info(f"[{self.name}] {msg}")
        elif level == LogLevel.WARNING: self.logger.warning(f"[{self.name}] {msg}")
        elif level == LogLevel.ERROR: self.logger.error(f"[{self.name}] {msg}")

    def send_stat(self, stat_type: str, data: str = "") -> None:
        try: self.event_queue.put_nowait({"type": "stat", "metric": stat_type, "data": data})
        except queue.Full: pass

    def trigger_error(self) -> None:
        try: self.event_queue.put_nowait({"type": "state_change", "new_state": BotState.ERROR, "source": self.name})
        except queue.Full: pass
        self.pause_event.set()

    def ensure_target(self) -> bool:
        if self.tracker.is_valid():
            try:
                if win32gui.IsIconic(self.tracker.data.hwnd):
                    self.send_log(LogLevel.WARNING, "Target minimized! Restoring...")
                    win32gui.ShowWindow(self.tracker.data.hwnd, win32con.SW_SHOWNOACTIVATE)
                    time.sleep(0.3)
            except Exception: pass
            return True

        self.send_log(LogLevel.WARNING, "Target window lost. Recovery backoff...")
        for delay in BOT_CONFIG["recovery_backoff_steps"]:
            self.stop_event.wait(delay)
            if self.stop_event.is_set() or self.pause_event.is_set(): return False
            if self.tracker.recover():
                self.send_log(LogLevel.INFO, f"Target RECOVERED via {self.tracker.last_recovery_mode}.")
                return True

        if not self.stop_event.is_set():
            self.send_log(LogLevel.ERROR, "Permanent target loss.")
            self.trigger_error()
        return False