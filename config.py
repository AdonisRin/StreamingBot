import os
import json
import copy
from enum import Enum, auto
from dataclasses import dataclass
from typing import Dict, Any
import win32con

APP_NAME = "Stream Bot"
APP_AUTHOR = "Adonis"

DEFAULT_CONFIG: Dict[str, Any] = {
    "click_min": 0.5, "click_max": 1.2,
    "type_min": 15.0, "type_max": 25.0,
    "type_char_delay_min": 0.03, "type_char_delay_max": 0.09,
    "type_pause_chance": 0.08, "type_pause_min": 0.10, "type_pause_max": 0.25,
    "type_space_min": 0.07, "type_space_max": 0.16,
    "recovery_backoff_steps": [1, 3, 10],
    "max_log_lines": 300,
    "ui_max_events_per_tick": 100,
    "max_queue_size": 2000,
    "search_window_depth": 7,
    "global_engine_cooldown": 0.8,
    "action_pause_wait": 0.5,
    "render_wait_delay": 0.3,
    "post_type_delay": 0.25,
    "hw_key_duration": 0.02,
    "stealth_pre_action_min": 0.05, "stealth_pre_action_max": 0.15,
    "ai_provider": "Gemini",
    "ai_interval_min": 30.0, "ai_interval_max": 60.0,
    "ai_prompt": "Roleplay as a live stream viewer in chat. CRITICAL RULES: Output ONLY ONE short casual reaction (maximum 6-8 words). NEVER describe the screen, NEVER write bullet points, list items, or explanations. NO Markdown formatting (do NOT use asterisks *, hash #, quotes, or bold). Just send a quick gamer reaction like you are typing fast in a live chat.",
    "panic_words": "verify you're human\ncaptcha\naccount suspended\nlogin required\nsecurity check\nverify your account\naction blocked\npuzzel\nrobot",
    "hotkeys": {
        "target_click": "F8", "target_chat": "F10", "stop_core": "F9",
        "toggle_core": "F6", "pause_click": "F11", "boss_key": "F7", "kill_all": "F4"
    },
    "presets": {
        "Twitch": {
            "click_min": 0.8, "click_max": 1.5, "type_min": 10.0, "type_max": 15.0,
            "messages": "{POG|GG|Awesome stream|Lets go} {bro|streamer}!\n{Sub|Follow} for more hype!"
        },
        "YouTube Live": {
            "click_min": 1.0, "click_max": 2.0, "type_min": 15.0, "type_max": 25.0,
            "messages": "{Great content|Awesome stream|Loving this}!\n{Like|Subscribe} to support the channel!"
        },
        "TikTok Live": {
            "click_min": 0.1, "click_max": 0.3, "type_min": 8.0, "type_max": 12.0,
            "messages": "{Tap tap tap!|Rose for the host!|Lets push the goal!}\n{Follow|Share} the live!"
        }
    }
}

VK_MAP = {
    "F1": win32con.VK_F1, "F2": win32con.VK_F2, "F3": win32con.VK_F3,
    "F4": win32con.VK_F4, "F5": win32con.VK_F5, "F6": win32con.VK_F6,
    "F7": win32con.VK_F7, "F8": win32con.VK_F8, "F9": win32con.VK_F9,
    "F10": win32con.VK_F10, "F11": win32con.VK_F11, "F12": win32con.VK_F12,
    "HOME": win32con.VK_HOME, "END": win32con.VK_END, 
    "INSERT": win32con.VK_INSERT, "DELETE": win32con.VK_DELETE,
    "PAGEUP": win32con.VK_PRIOR, "PAGEDOWN": win32con.VK_NEXT,
    "TAB": win32con.VK_TAB, "PAUSE": win32con.VK_PAUSE
}

def deep_merge(default: dict, custom: dict) -> dict:
    for key, value in custom.items():
        if isinstance(value, dict) and key in default and isinstance(default[key], dict):
            deep_merge(default[key], value)
        else:
            default[key] = value
    return default

def load_config() -> dict:
    config_path = "config.json"
    if not os.path.exists(config_path):
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4)
        except Exception: pass
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
            return deep_merge(copy.deepcopy(DEFAULT_CONFIG), user_config)
    except Exception:
        return copy.deepcopy(DEFAULT_CONFIG)

BOT_CONFIG = load_config()

class LogLevel(Enum):
    INFO, WARNING, ERROR, DEBUG = "INFO", "WARNING", "ERROR", "DEBUG"

class BotState(Enum):
    IDLE = auto(); RUNNING = auto(); PAUSED = auto(); ON_BREAK = auto(); ERROR = auto()

@dataclass
class TargetData:
    hwnd: int = 0; pid: int = 0; exe_name: str = ""; title: str = ""; class_name: str = ""
    pct_x: float = 0.5; pct_y: float = 0.9; offset_x: int = 0; offset_y: int = 0
    anc_x: str = "LEFT"; anc_y: str = "TOP"