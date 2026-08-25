import time
import queue
import random
import json
import urllib.request
import urllib.error
import base64
import gc
from typing import Callable
import logging
import threading
from datetime import datetime

import win32gui
import win32con
from PIL import ImageGrab

from config import BOT_CONFIG, LogLevel
from core_engine import BaseEngine, WindowTracker
from utils import safe_get_float

class PanicEngine(BaseEngine):
    def __init__(self, name: str, tracker: WindowTracker, event_queue: queue.Queue, instance_lock: threading.Lock, logger: logging.Logger, stop_callback: Callable, get_panic_words_func: Callable):
        super().__init__(name, tracker, event_queue, lambda: 2.0, instance_lock, lambda: False, logger)
        self.stop_callback = stop_callback
        self.get_panic_words = get_panic_words_func
        
    def run(self):
        try:
            while not self.stop_event.is_set():
                if self.pause_event.is_set() or not self.ensure_target():
                    self.stop_event.wait(1.0); continue
                    
                root_hwnd = win32gui.GetAncestor(self.tracker.data.hwnd, win32con.GA_ROOT)
                if not root_hwnd: root_hwnd = self.tracker.data.hwnd
                    
                fg_hwnd = win32gui.GetForegroundWindow()
                root_title = win32gui.GetWindowText(root_hwnd).lower()
                target_title = win32gui.GetWindowText(self.tracker.data.hwnd).lower()
                fg_title = win32gui.GetWindowText(fg_hwnd).lower() if fg_hwnd else ""
                combined_title = f"{root_title} | {target_title} | {fg_title}"

                for word in self.get_panic_words():
                    if word in combined_title:
                        trigger_hwnd = fg_hwnd if word in fg_title else root_hwnd
                        trigger_name = fg_title if word in fg_title else root_title
                        
                        self.send_log(LogLevel.ERROR, f"🚨 PANIC TRIGGERED! Detected '{word}' in window: '{trigger_name}'")
                        try:
                            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                            filename = f"PANIC_SCREENSHOT_{timestamp}.png"
                            rect = win32gui.GetWindowRect(trigger_hwnd)
                            img = ImageGrab.grab(bbox=(rect[0], rect[1], rect[2], rect[3]))
                            img.save(filename)
                            self.send_log(LogLevel.INFO, f"Saved panic screenshot: {filename}")
                        except Exception as e: self.send_log(LogLevel.ERROR, f"Failed screenshot: {e}")
                            
                        self.event_queue.put_nowait({"type": "panic_stop"})
                        break
                self.stop_event.wait(1.5)
        except Exception as e:
            self.send_log(LogLevel.ERROR, f"Fatal Crash: {e}"); self.trigger_error()

class AIVisionEngine(BaseEngine):
    def __init__(self, name: str, tracker: WindowTracker, event_queue: queue.Queue, instance_lock: threading.Lock, logger: logging.Logger, api_key_func: Callable, chat_queue: queue.Queue, prompt_func: Callable, interval_min_func: Callable, interval_max_func: Callable, provider_func: Callable):
        super().__init__(name, tracker, event_queue, lambda: 45.0, instance_lock, lambda: False, logger)
        self.get_api_key = api_key_func
        self.chat_queue = chat_queue
        self.get_prompt = prompt_func
        self.get_min = interval_min_func
        self.get_max = interval_max_func
        self.get_provider = provider_func
        
    def run(self):
        fail_count = 0 
        try:
            while not self.stop_event.is_set():
                if self.pause_event.is_set() or not self.ensure_target():
                    self.stop_event.wait(1.0); continue
                    
                api_key = self.get_api_key()
                if not api_key: self.stop_event.wait(10.0); continue
                    
                try:
                    rect = win32gui.GetWindowRect(self.tracker.data.hwnd)
                    img = ImageGrab.grab(bbox=(rect[0], rect[1], rect[2], rect[3]))
                    img.thumbnail((800, 800))
                    
                    import io
                    buffered = io.BytesIO()
                    img.save(buffered, format="JPEG", quality=70)
                    img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
                    img.close(); buffered.close()
                    
                    provider = self.get_provider()
                    self.send_log(LogLevel.DEBUG, f"AI Vision ({provider}): Captured screenshot. Calling API...")
                    custom_prompt = self.get_prompt()

                    if provider == "OpenAI":
                        req = urllib.request.Request("https://api.openai.com/v1/chat/completions", method="POST")
                        req.add_header("Authorization", f"Bearer {api_key}")
                        req.add_header("Content-Type", "application/json")
                        payload = {
                            "model": "gpt-4o-mini",
                            "messages": [{"role": "user", "content": [{"type": "text", "text": custom_prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_str}"}}] }],
                            "max_tokens": 50
                        }
                        data = json.dumps(payload).encode("utf-8")
                        response = urllib.request.urlopen(req, data=data, timeout=10)
                        result = json.loads(response.read().decode('utf-8'))
                        ai_message = result['choices'][0]['message']['content'].strip()
                    else:
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={api_key}"
                        req = urllib.request.Request(url, method="POST")
                        req.add_header("Content-Type", "application/json")
                        payload = {"contents": [{"parts": [{"text": custom_prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": img_str}}] }]}
                        data = json.dumps(payload).encode("utf-8")
                        response = urllib.request.urlopen(req, data=data, timeout=10)
                        result = json.loads(response.read().decode('utf-8'))
                        ai_message = result['candidates'][0]['content']['parts'][0]['text'].strip()
                    
                    if self.stop_event.is_set(): break
                    self.send_log(LogLevel.INFO, f"AI Vision Generated: {ai_message}")
                    self.chat_queue.put(ai_message)
                    fail_count = 0
                    
                except urllib.error.HTTPError as e:
                    fail_count += 1
                    if e.code == 429 or e.code >= 500: self.send_log(LogLevel.WARNING, f"AI Server Busy ({e.code}). Rate limit active. Backing off (x{fail_count})...")
                    else: self.send_log(LogLevel.ERROR, f"AI Vision Auth/HTTP Error: {e.code}")
                except Exception as e:
                    fail_count += 1; self.send_log(LogLevel.ERROR, f"AI Vision Network/Parse Error: {e}")
                finally:
                    if 'img_str' in locals(): del img_str
                    gc.collect()
                    
                if fail_count > 0: wait_time = min(120.0, 15.0 * (2 ** (fail_count - 1)))
                else:
                    mi, ma = safe_get_float(self.get_min(), 30.0), safe_get_float(self.get_max(), 60.0)
                    if mi > ma: mi, ma = ma, mi
                    wait_time = random.uniform(mi, ma)
                self.stop_event.wait(wait_time)
        except Exception as e:
            self.send_log(LogLevel.ERROR, f"Fatal AI Crash: {e}"); self.trigger_error()