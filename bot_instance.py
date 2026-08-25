import time
import random
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import re
import csv
from datetime import datetime
from dataclasses import asdict
import logging
from typing import Callable

from config import BOT_CONFIG, BotState, LogLevel, TargetData, APP_NAME
from utils import safe_get_float, play_audio_cue, send_discord_webhook, obfuscate_secret, deobfuscate_secret
from core_engine import WindowTracker
from action_engines import ClickEngine, TypingEngine, AntiAfkEngine
from vision_engines import PanicEngine, AIVisionEngine

class BotInstance:
    def __init__(self, parent_frame: tk.Frame, tab_id: str, tab_name: str, delete_callback: Callable, root: tk.Tk, sys_logger: logging.Logger):
        self.frame = parent_frame
        self.tab_id = tab_id
        self.tab_name = tab_name
        self.delete_callback = delete_callback
        self.root = root
        self.sys_logger = sys_logger
        
        self.click_tracker = WindowTracker()
        self.type_tracker = WindowTracker()
        self.event_queue = queue.Queue(maxsize=BOT_CONFIG["max_queue_size"])
        self.ai_message_queue = queue.Queue()
        self.state = BotState.IDLE
        self.action_lock = threading.Lock()
        
        self.click_engine = None
        self.type_engine = None
        self.anti_afk_engine = None
        self.panic_engine = None
        self.ai_engine = None
        
        self.stats_clicks = 0
        self.stats_msgs = 0
        self.start_time = 0
        
        self.uptime_str = tk.StringVar(value="UPTIME: 00:00:00")
        self.clicks_str = tk.StringVar(value="CLICKS: 0")
        self.msgs_str = tk.StringVar(value="MESSAGES: 0")

        self.stealth_mode = tk.BooleanVar(value=True)
        self.anti_afk_mode = tk.BooleanVar(value=False)

        self.enable_spintax = tk.BooleanVar(value=True)
        self.enable_timed = tk.BooleanVar(value=True)

        self.pro_audio = tk.BooleanVar(value=True)
        self.pro_breaks = tk.BooleanVar(value=False)
        self.pro_autostop = tk.BooleanVar(value=False)
        self.pro_panic = tk.BooleanVar(value=True)
        self.pro_ai_vision = tk.BooleanVar(value=False)
        
        self.next_break_time = 0
        self.break_end_time = 0
        self.auto_stop_target = 0
        
        self.session_log = [] 
        self.scheduled_sent = set() 

        self.setup_ui()
        self.process_all_events()
        try: self.event_queue.put_nowait({"type": "log", "level": LogLevel.INFO, "msg": f"[System] {APP_NAME} Instance initialized."})
        except queue.Full: pass

    def setup_ui(self):
        font_title = ("Consolas", 10, "bold")
        font_normal = ("Consolas", 9)
        bg_main, bg_sec, accent = "#050a15", "#0a1128", "#00ffcc"
        
        self.frame.configure(bg=bg_main)
        frame_top = tk.Frame(self.frame, bg=bg_main)
        frame_top.pack(fill="x", padx=10, pady=5)

        self.lbl_main_state = tk.Label(frame_top, text="STATE: IDLE", font=("Consolas", 12, "bold"), bg=bg_main, fg="gray")
        self.lbl_main_state.pack(side="left")

        chk_stealth = tk.Checkbutton(frame_top, text="STEALTH", variable=self.stealth_mode, font=("Consolas", 9, "bold"), bg=bg_main, fg="#fca311", selectcolor="#000000", activebackground=bg_main, activeforeground="#fca311", command=self.update_stealth_label)
        chk_stealth.pack(side="left", padx=10)
        
        self.lbl_stealth_status = tk.Label(frame_top, text="ACTIVE", font=("Consolas", 9, "bold"), bg=bg_main, fg="#00ffcc")
        self.lbl_stealth_status.pack(side="left")

        tk.Checkbutton(frame_top, text="ANTI-AFK", variable=self.anti_afk_mode, font=("Consolas", 9, "bold"), bg=bg_main, fg="#fca311", selectcolor="#000000", activebackground=bg_main, activeforeground="#fca311").pack(side="left", padx=(10, 5))

        tk.Button(frame_top, text="CLOSE", font=("Consolas", 8, "bold"), bg="#ff003c", fg="white", bd=0, padx=10, command=self.delete_self).pack(side="right")
        self.btn_overlay = tk.Button(frame_top, text="WIDGET / OVERLAY", font=("Consolas", 8, "bold"), bg="#2b2d42", fg="gray", bd=0, padx=10, command=self.toggle_overlay)
        self.btn_overlay.pack(side="right", padx=5)

        pane_main = tk.PanedWindow(self.frame, orient=tk.HORIZONTAL, bg=bg_sec, bd=0)
        pane_main.pack(fill="both", expand=True, padx=10, pady=5)

        frame_left = tk.Frame(pane_main, bg=bg_sec)
        pane_main.add(frame_left, width=380)

        notebook_left = ttk.Notebook(frame_left)
        notebook_left.pack(fill="both", expand=True)

        tab_main = tk.Frame(notebook_left, bg=bg_sec)
        tab_pro = tk.Frame(notebook_left, bg=bg_sec)
        tab_ai = tk.Frame(notebook_left, bg=bg_sec)
        
        notebook_left.add(tab_main, text=" MAIN ")
        notebook_left.add(tab_pro, text=" PRO TOOLS ")
        notebook_left.add(tab_ai, text=" AI VISION ")

        # --- TAB MAIN CONTENT ---
        f_presets = tk.Frame(tab_main, bg=bg_sec)
        f_presets.pack(fill="x", padx=10, pady=(10, 5))
        tk.Label(f_presets, text="PRESETS:", font=font_title, bg=bg_sec, fg="#7209b7").pack(side="left")
        self.combo_preset = ttk.Combobox(f_presets, values=list(BOT_CONFIG.get("presets", {}).keys()), state="readonly", width=15)
        self.combo_preset.pack(side="right", padx=5)
        self.combo_preset.bind("<<ComboboxSelected>>", self.apply_preset)

        tk.Label(tab_main, text="HUMANIZED TIMING (MIN/MAX SECS)", font=font_title, bg=bg_sec, fg="#7209b7").pack(pady=(10, 2))
        f_speeds = tk.Frame(tab_main, bg=bg_sec)
        f_speeds.pack(fill="x", padx=10)

        tk.Label(f_speeds, text="Click Delay:", font=font_normal, bg=bg_sec, fg="white").grid(row=0, column=0, sticky="w", pady=4)
        self.entry_clk_min = tk.Entry(f_speeds, width=6, bg="#1a1a2e", fg=accent, bd=1, relief="solid", insertbackground=accent)
        self.entry_clk_min.insert(0, str(BOT_CONFIG["click_min"]))
        self.entry_clk_min.grid(row=0, column=1, padx=(5,0))
        tk.Label(f_speeds, text=" - ", font=font_normal, bg=bg_sec, fg="white").grid(row=0, column=2)
        self.entry_clk_max = tk.Entry(f_speeds, width=6, bg="#1a1a2e", fg=accent, bd=1, relief="solid", insertbackground=accent)
        self.entry_clk_max.insert(0, str(BOT_CONFIG["click_max"]))
        self.entry_clk_max.grid(row=0, column=3)

        tk.Label(f_speeds, text="Chat Delay:", font=font_normal, bg=bg_sec, fg="white").grid(row=1, column=0, sticky="w", pady=4)
        self.entry_typ_min = tk.Entry(f_speeds, width=6, bg="#1a1a2e", fg=accent, bd=1, relief="solid", insertbackground=accent)
        self.entry_typ_min.insert(0, str(BOT_CONFIG["type_min"]))
        self.entry_typ_min.grid(row=1, column=1, padx=(5,0))
        tk.Label(f_speeds, text=" - ", font=font_normal, bg=bg_sec, fg="white").grid(row=1, column=2)
        self.entry_typ_max = tk.Entry(f_speeds, width=6, bg="#1a1a2e", fg=accent, bd=1, relief="solid", insertbackground=accent)
        self.entry_typ_max.insert(0, str(BOT_CONFIG["type_max"]))
        self.entry_typ_max.grid(row=1, column=3)

        # SPINTAX
        f_spin_hdr = tk.Frame(tab_main, bg=bg_sec)
        f_spin_hdr.pack(fill="x", padx=10, pady=(15, 2))
        tk.Checkbutton(f_spin_hdr, text="ENABLE SPINTAX CHAT", variable=self.enable_spintax, font=font_title, bg=bg_sec, fg="#7209b7", selectcolor="#000000", activebackground=bg_sec).pack(side="left")
        self.text_msgs = tk.Text(tab_main, height=3, font=("Consolas", 9), bg="#050a15", fg=accent, bd=1, relief="solid", insertbackground=accent)
        self.text_msgs.pack(fill="both", expand=True, padx=10, pady=(0, 5))
        self.text_msgs.insert(tk.END, "{Awesome|Great|POG} {stream|live}!\n{Sub|Follow} for more hype!\n")

        # TIMED MESSAGES
        f_timed_hdr = tk.Frame(tab_main, bg=bg_sec)
        f_timed_hdr.pack(fill="x", padx=10, pady=(10, 0))
        tk.Checkbutton(f_timed_hdr, text="ENABLE TIMED MESSAGES", variable=self.enable_timed, font=font_title, bg=bg_sec, fg="#fca311", selectcolor="#000000", activebackground=bg_sec).pack(side="left")

        f_timed_add = tk.Frame(tab_main, bg=bg_sec)
        f_timed_add.pack(fill="x", padx=10, pady=2)
        tk.Label(f_timed_add, text="Mins:", bg=bg_sec, fg="white", font=font_normal).pack(side="left")
        self.ent_t_min = tk.Entry(f_timed_add, width=6, bg="#1a1a2e", fg=accent, bd=1, relief="solid", insertbackground=accent)
        self.ent_t_min.pack(side="left", padx=(0, 10))
        tk.Label(f_timed_add, text="Msg:", bg=bg_sec, fg="white", font=font_normal).pack(side="left")
        self.ent_t_msg = tk.Entry(f_timed_add, bg="#1a1a2e", fg=accent, bd=1, relief="solid", insertbackground=accent)
        self.ent_t_msg.pack(side="left", fill="x", expand=True, padx=(0, 10))
        tk.Button(f_timed_add, text="ADD", bg="#4cc9f0", fg="black", font=("Consolas", 8, "bold"), bd=0, command=self.add_timed_msg).pack(side="left")
        tk.Button(f_timed_add, text="DEL", bg="#ff003c", fg="white", font=("Consolas", 8, "bold"), bd=0, padx=5, command=self.del_timed_msg).pack(side="left", padx=(5,0))

        columns = ("min", "msg")
        self.tree_timed = ttk.Treeview(tab_main, columns=columns, show="headings", height=3)
        self.tree_timed.heading("min", text="Time (Mins)")
        self.tree_timed.column("min", width=80, anchor="center")
        self.tree_timed.heading("msg", text="Message")
        self.tree_timed.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tree_timed.insert("", "end", values=("3.0", "Make sure to follow guys!"))
        self.tree_timed.insert("", "end", values=("15.5", "Anyone want to play next match?"))

        # --- TAB PRO TOOLS ---
        tk.Label(tab_pro, text="ADVANCED SECURITY & AUTOMATION", font=font_title, bg=bg_sec, fg="#fca311").pack(pady=(10, 5))
        f_panic = tk.Frame(tab_pro, bg=bg_sec)
        f_panic.pack(fill="x", padx=10, pady=2)
        tk.Checkbutton(f_panic, text="Panic Trigger / Auto FailSafe", variable=self.pro_panic, font=font_normal, bg=bg_sec, fg="#ff4444", selectcolor="#000000", activebackground=bg_sec).pack(anchor="w")

        f_p_words = tk.Frame(tab_pro, bg=bg_sec)
        f_p_words.pack(fill="x", padx=10, pady=(0, 5))
        tk.Label(f_p_words, text="Keywords (one per line):", font=font_normal, bg=bg_sec, fg="gray").pack(anchor="w")
        self.text_panic_words = tk.Text(f_p_words, height=4, font=("Consolas", 8), bg="#1a1a2e", fg="#ff4444", bd=1, relief="solid", insertbackground="#ff4444")
        self.text_panic_words.pack(fill="x", expand=True)
        self.text_panic_words.insert(tk.END, BOT_CONFIG.get("panic_words", ""))

        f_brk = tk.Frame(tab_pro, bg=bg_sec)
        f_brk.pack(fill="x", padx=10, pady=2)
        tk.Checkbutton(f_brk, text="Smart Breaks", variable=self.pro_breaks, font=font_normal, bg=bg_sec, fg="white", selectcolor="#000000", activebackground=bg_sec).grid(row=0, column=0, sticky="w", columnspan=2)
        tk.Label(f_brk, text="Every (mins):", font=font_normal, bg=bg_sec, fg="gray").grid(row=1, column=0, sticky="w", padx=(15,5))
        self.ent_brk_int = tk.Entry(f_brk, width=5, bg="#1a1a2e", fg="#fca311", bd=1, relief="solid", insertbackground="#fca311")
        self.ent_brk_int.insert(0, "30")
        self.ent_brk_int.grid(row=1, column=1)
        tk.Label(f_brk, text="For (mins):", font=font_normal, bg=bg_sec, fg="gray").grid(row=1, column=2, sticky="w", padx=(10,5))
        self.ent_brk_dur = tk.Entry(f_brk, width=5, bg="#1a1a2e", fg="#fca311", bd=1, relief="solid", insertbackground="#fca311")
        self.ent_brk_dur.insert(0, "2")
        self.ent_brk_dur.grid(row=1, column=3)

        f_stop = tk.Frame(tab_pro, bg=bg_sec)
        f_stop.pack(fill="x", padx=10, pady=2)
        tk.Checkbutton(f_stop, text="Auto-Stop Timer", variable=self.pro_autostop, font=font_normal, bg=bg_sec, fg="white", selectcolor="#000000", activebackground=bg_sec).grid(row=0, column=0, sticky="w", columnspan=2)
        tk.Label(f_stop, text="Stop after (mins):", font=font_normal, bg=bg_sec, fg="gray").grid(row=1, column=0, sticky="w", padx=(15,5))
        self.ent_stop_dur = tk.Entry(f_stop, width=5, bg="#1a1a2e", fg="#fca311", bd=1, relief="solid", insertbackground="#fca311")
        self.ent_stop_dur.insert(0, "120")
        self.ent_stop_dur.grid(row=1, column=1)

        tk.Label(tab_pro, text="INTEGRATIONS & EXPORTS", font=font_title, bg=bg_sec, fg="#fca311").pack(pady=(10, 5))
        f_disc = tk.Frame(tab_pro, bg=bg_sec)
        f_disc.pack(fill="x", padx=10)
        tk.Label(f_disc, text="Discord Webhook URL:", font=font_normal, bg=bg_sec, fg="white").pack(anchor="w")
        self.ent_webhook = tk.Entry(f_disc, bg="#1a1a2e", fg="#00ffcc", bd=1, relief="solid", insertbackground=accent)
        self.ent_webhook.pack(fill="x", pady=2)
        
        tk.Checkbutton(tab_pro, text="Enable Audio Alerts (Beeps)", variable=self.pro_audio, font=font_normal, bg=bg_sec, fg="white", selectcolor="#000000", activebackground=bg_sec).pack(anchor="w", padx=10, pady=2)
        tk.Button(tab_pro, text="EXPORT SESSION CSV", font=("Consolas", 8, "bold"), bg="#1a1a2e", fg="#00ffcc", bd=1, command=self.export_csv).pack(anchor="w", padx=10, pady=5)

        # --- TAB AI VISION ---
        tk.Label(tab_ai, text="OPENAI & GEMINI VISION", font=font_title, bg=bg_sec, fg="#00ffcc").pack(pady=(10, 5))
        tk.Checkbutton(tab_ai, text="Enable Smart AI Vision Bot", variable=self.pro_ai_vision, font=font_normal, bg=bg_sec, fg="white", selectcolor="#000000", activebackground=bg_sec).pack(anchor="w", padx=10)
        
        f_ai_prov = tk.Frame(tab_ai, bg=bg_sec)
        f_ai_prov.pack(fill="x", padx=10, pady=2)
        tk.Label(f_ai_prov, text="AI Provider:", font=font_normal, bg=bg_sec, fg="white").pack(side="left")
        self.combo_ai_provider = ttk.Combobox(f_ai_prov, values=["Gemini", "OpenAI"], state="readonly", width=15)
        self.combo_ai_provider.set(BOT_CONFIG.get("ai_provider", "Gemini"))
        self.combo_ai_provider.pack(side="left", padx=10)

        f_api = tk.Frame(tab_ai, bg=bg_sec)
        f_api.pack(fill="x", padx=10, pady=5)
        tk.Label(f_api, text="API Key:", font=font_normal, bg=bg_sec, fg="white").pack(anchor="w")
        self.ent_api_key = tk.Entry(f_api, bg="#1a1a2e", fg="#00ffcc", bd=1, relief="solid", show="*", insertbackground=accent)
        self.ent_api_key.pack(fill="x", pady=2)

        f_ai_spd = tk.Frame(tab_ai, bg=bg_sec)
        f_ai_spd.pack(fill="x", padx=10, pady=5)
        tk.Label(f_ai_spd, text="Check Interval (Min - Max secs):", font=font_normal, bg=bg_sec, fg="white").pack(anchor="w")
        f_ai_s_inputs = tk.Frame(f_ai_spd, bg=bg_sec)
        f_ai_s_inputs.pack(anchor="w", pady=2)
        self.ent_ai_min = tk.Entry(f_ai_s_inputs, width=6, bg="#1a1a2e", fg=accent, bd=1, relief="solid", insertbackground=accent)
        self.ent_ai_min.insert(0, str(BOT_CONFIG.get("ai_interval_min", 30.0)))
        self.ent_ai_min.pack(side="left")
        tk.Label(f_ai_s_inputs, text=" - ", bg=bg_sec, fg="white").pack(side="left")
        self.ent_ai_max = tk.Entry(f_ai_s_inputs, width=6, bg="#1a1a2e", fg=accent, bd=1, relief="solid", insertbackground=accent)
        self.ent_ai_max.insert(0, str(BOT_CONFIG.get("ai_interval_max", 60.0)))
        self.ent_ai_max.pack(side="left")

        tk.Label(tab_ai, text="Custom AI Instruction / Language (Prompt):", font=font_normal, bg=bg_sec, fg="white").pack(anchor="w", padx=10, pady=(5, 2))
        self.text_ai_prompt = tk.Text(tab_ai, height=3, font=("Consolas", 8), bg="#1a1a2e", fg=accent, bd=1, relief="solid", insertbackground=accent, wrap="word")
        self.text_ai_prompt.pack(fill="x", padx=10, pady=2)
        self.text_ai_prompt.insert(tk.END, BOT_CONFIG.get("ai_prompt", ""))

        # --- TELEMETRY ---
        frame_right = tk.Frame(pane_main, bg=bg_main)
        pane_main.add(frame_right)

        tk.Label(frame_right, text="TELEMETRY", font=font_title, bg=bg_main, fg="#fca311").pack(pady=(5, 2))
        f_stats = tk.Frame(frame_right, bg=bg_sec, bd=1, relief="solid")
        f_stats.pack(fill="x", padx=10, pady=2)
        tk.Label(f_stats, textvariable=self.uptime_str, font=("Consolas", 9, "bold"), bg=bg_sec, fg="white").pack(anchor="w", padx=5)
        tk.Label(f_stats, textvariable=self.clicks_str, font=("Consolas", 9, "bold"), bg=bg_sec, fg="#00ffcc").pack(anchor="w", padx=5)
        tk.Label(f_stats, textvariable=self.msgs_str, font=("Consolas", 9, "bold"), bg=bg_sec, fg="#ff003c").pack(anchor="w", padx=5)

        tk.Label(frame_right, text="TARGET LOCK", font=font_title, bg=bg_main, fg="#fca311").pack(pady=(10, 2))
        hk_config = BOT_CONFIG.get("hotkeys", {})
        hk_clk = hk_config.get("target_click", "F8")
        hk_cht = hk_config.get("target_chat", "F10")
        hk_stp = hk_config.get("stop_core", "F9")

        self.lbl_click = tk.Label(frame_right, text=f"[{hk_clk}]  Click: UNAVAILABLE", font=font_normal, bg=bg_main, fg="#ff4444")
        self.lbl_click.pack(anchor="w", padx=10)
        self.lbl_type = tk.Label(frame_right, text=f"[{hk_cht}] Chat : UNAVAILABLE", font=font_normal, bg=bg_main, fg="#ff4444")
        self.lbl_type.pack(anchor="w", padx=10, pady=5)

        # --- LOG ---
        f_log_ctrl = tk.Frame(self.frame, bg=bg_main)
        f_log_ctrl.pack(fill="x", padx=10)
        tk.Label(f_log_ctrl, text="LOG OUTPUT", font=("Consolas", 8, "bold"), bg=bg_main, fg="gray").pack(side="left")
        tk.Button(f_log_ctrl, text="CLEAR LOG", font=("Consolas", 7), bg="#2b2d42", fg="white", bd=0, command=self.clear_log).pack(side="right")

        f_term = tk.Frame(self.frame, bg="#000000", bd=2, relief="sunken")
        f_term.pack(fill="x", padx=10, pady=2)
        self.term_log = tk.Text(f_term, height=4, font=("Consolas", 8), bg="#000000", fg="white", state=tk.DISABLED, insertbackground="white")
        self.term_log.pack(fill="both", expand=True)
        self.term_log.tag_config("INFO", foreground="#00ffcc")
        self.term_log.tag_config("WARNING", foreground="yellow")
        self.term_log.tag_config("ERROR", foreground="red")
        self.term_log.tag_config("DEBUG", foreground="gray")

        f_btm = tk.Frame(self.frame, bg=bg_main)
        f_btm.pack(fill="x", padx=10, pady=5)
        self.btn_start = tk.Button(f_btm, text="START ENGINE", font=("Consolas", 12, "bold"), bg="#4cc9f0", fg="black", bd=0, command=self.start_bot)
        self.btn_start.pack(side="left", fill="x", expand=True, padx=5, ipady=5)
        self.btn_stop = tk.Button(f_btm, text=f"STOP ({hk_stp})", font=("Consolas", 12, "bold"), bg="#2b2d42", fg="white", bd=0, command=self.stop_bot, state=tk.DISABLED)
        self.btn_stop.pack(side="right", fill="x", expand=True, padx=5, ipady=5)

    def add_timed_msg(self):
        min_val, msg_val = self.ent_t_min.get().strip(), self.ent_t_msg.get().strip()
        if not min_val or not msg_val: return
        try:
            float(min_val)
            self.tree_timed.insert("", "end", values=(min_val, msg_val))
            self.ent_t_min.delete(0, tk.END); self.ent_t_msg.delete(0, tk.END)
            items = [(self.tree_timed.item(child)["values"][0], self.tree_timed.item(child)["values"][1], child) for child in self.tree_timed.get_children()]
            items.sort(key=lambda x: float(x[0]))
            for child in self.tree_timed.get_children(): self.tree_timed.delete(child)
            for min_v, msg_v, _ in items: self.tree_timed.insert("", "end", values=(min_v, msg_v))
        except ValueError: messagebox.showwarning("Invalid Time", "Minutes must be a number (e.g., 2.5).")

    def del_timed_msg(self):
        for s in self.tree_timed.selection(): self.tree_timed.delete(s)

    def export_csv(self):
        if not self.session_log: return messagebox.showinfo("Export CSV", "No activity recorded in this session yet.")
        initial_file = f"Session_{self.tab_name}_{datetime.now().strftime('%H%M%S')}.csv"
        f = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=initial_file, filetypes=[("CSV File", "*.csv")])
        if f:
            try:
                with open(f, 'w', newline='', encoding='utf-8') as file:
                    writer = csv.writer(file)
                    writer.writerow(["Time", "Action", "Data"])
                    writer.writerows(self.session_log)
                messagebox.showinfo("Export CSV", f"Session exported successfully to:\n{f}")
            except Exception as e: messagebox.showerror("Export CSV Error", str(e))

    def apply_preset(self, event=None):
        preset_name = self.combo_preset.get()
        presets = BOT_CONFIG.get("presets", {})
        if preset_name in presets:
            p = presets[preset_name]
            self.entry_clk_min.delete(0, tk.END); self.entry_clk_min.insert(0, str(p.get("click_min", 0.5)))
            self.entry_clk_max.delete(0, tk.END); self.entry_clk_max.insert(0, str(p.get("click_max", 1.2)))
            self.entry_typ_min.delete(0, tk.END); self.entry_typ_min.insert(0, str(p.get("type_min", 15.0)))
            self.entry_typ_max.delete(0, tk.END); self.entry_typ_max.insert(0, str(p.get("type_max", 25.0)))
            self.text_msgs.delete("1.0", tk.END); self.text_msgs.insert(tk.END, p.get("messages", ""))

    def toggle_overlay(self):
        import win32con, win32gui
        if hasattr(self, 'overlay_win') and self.overlay_win.winfo_exists():
            self.overlay_win.destroy()
            self.btn_overlay.config(fg="gray")
        else:
            self.overlay_win = tk.Toplevel(self.root)
            self.overlay_win.overrideredirect(True)
            self.overlay_win.attributes("-topmost", True)
            self.overlay_win.attributes("-alpha", 0.85)
            self.overlay_win.configure(bg="#02040a")
            
            core_match = re.search(r'\d+', self.tab_name)
            core_num = int(core_match.group()) if core_match else 1
            x_pos = self.root.winfo_screenwidth() - 320
            y_pos = 50 + ((core_num - 1) * 70)
            self.overlay_win.geometry(f"300x55+{x_pos}+{y_pos}")
            self.overlay_win.update()
            
            try:
                hwnd = int(self.overlay_win.wm_frame(), 16)
                ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT)
            except Exception: pass
                
            self.lbl_ov_title = tk.Label(self.overlay_win, text=f"{self.tab_name} OVERLAY", font=("Consolas", 9, "bold"), bg="#02040a", fg="#00ffcc")
            self.lbl_ov_title.pack(anchor="w", padx=10, pady=(5, 0))
            self.lbl_ov_status = tk.Label(self.overlay_win, text="IDLE | UP: 00:00:00", font=("Consolas", 9), bg="#02040a", fg="white")
            self.lbl_ov_status.pack(anchor="w", padx=10)
            
            self.btn_overlay.config(fg="#00ffcc")
            self.update_overlay_logic()

    def update_overlay_logic(self):
        if not hasattr(self, 'overlay_win') or not self.overlay_win.winfo_exists(): return
        st = self.state.name
        up = self.uptime_str.get().replace("UPTIME: ", "")
        stealth_txt = "STL:ON" if self.stealth_mode.get() else "STL:OFF"
        afk_txt = "AFK:ON" if self.anti_afk_mode.get() else "AFK:OFF"
        self.lbl_ov_status.config(text=f"[{st}] UP:{up} | {stealth_txt} | {afk_txt}")
        
        if self.state == BotState.RUNNING: color = "#00ffcc"
        elif self.state == BotState.ON_BREAK: color = "#ff003c"
        elif self.state == BotState.PAUSED: color = "#fca311"
        else: color = "gray"
            
        self.lbl_ov_title.config(fg=color)
        self.root.after(1000, self.update_overlay_logic)

    def clear_log(self):
        self.term_log.config(state=tk.NORMAL)
        self.term_log.delete("1.0", tk.END)
        self.term_log.config(state=tk.DISABLED)

    def update_stealth_label(self):
        if self.stealth_mode.get(): self.lbl_stealth_status.config(text="ACTIVE", fg="#00ffcc")
        else: self.lbl_stealth_status.config(text="DISABLED", fg="#ff4444")

    def process_all_events(self):
        processed_count = 0
        while not self.event_queue.empty() and processed_count < BOT_CONFIG["ui_max_events_per_tick"]:
            try:
                event = self.event_queue.get_nowait()
                e_type = event.get("type")

                if e_type == "stat":
                    timestamp = datetime.now().strftime('%H:%M:%S')
                    if event["metric"] == "click":
                        self.stats_clicks += 1
                        self.session_log.append([timestamp, "Click Sent", ""])
                    elif event["metric"] == "msg":
                        self.stats_msgs += 1
                        self.session_log.append([timestamp, "Message Sent", event.get("data", "")])
                    self.clicks_str.set(f"CLICKS: {self.stats_clicks:,}")
                    self.msgs_str.set(f"MESSAGES: {self.stats_msgs:,}")

                elif e_type == "log":
                    level = event["level"]
                    msg = event["msg"]
                    self.term_log.config(state=tk.NORMAL)
                    self.term_log.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] [{level.value}] {msg}\n", level.value)
                    lines = int(self.term_log.index('end-1c').split('.')[0])
                    if lines > BOT_CONFIG["max_log_lines"]:
                        self.term_log.delete("1.0", f"{lines - int(BOT_CONFIG['max_log_lines'] * 0.8)}.0")
                    self.term_log.see(tk.END)
                    self.term_log.config(state=tk.DISABLED)

                elif e_type == "state_change": self.change_state(event["new_state"])
                elif e_type == "panic_stop":
                    self.change_state(BotState.ERROR)
                    self.stop_bot()
                processed_count += 1
            except queue.Empty: break

        # TIMER AND BREAK LOGIC
        if self.state in [BotState.RUNNING, BotState.ON_BREAK]:
            current_time = time.time()
            if self.start_time > 0:
                elapsed = int(current_time - self.start_time)
                mins, secs = divmod(elapsed, 60)
                hours, mins = divmod(mins, 60)
                self.uptime_str.set(f"UPTIME: {hours:02d}:{mins:02d}:{secs:02d}")
            
            if self.pro_autostop.get() and self.auto_stop_target > 0:
                if current_time >= self.auto_stop_target:
                    self.event_queue.put_nowait({"type": "log", "level": LogLevel.INFO, "msg": "Auto-Stop timer reached. Stopping."})
                    send_discord_webhook(self.ent_webhook.get(), "Auto-Stop timer reached. Shutting down.", self.tab_name)
                    self.stop_bot()

            if self.pro_breaks.get():
                if self.state == BotState.RUNNING and current_time >= self.next_break_time:
                    self.change_state(BotState.ON_BREAK)
                    dur = safe_get_float(self.ent_brk_dur.get(), 2.0)
                    self.break_end_time = current_time + (dur * 60)
                    if self.click_engine: self.click_engine.pause_event.set()
                    if self.type_engine: self.type_engine.pause_event.set()
                    if self.anti_afk_engine: self.anti_afk_engine.pause_event.set()
                    if self.ai_engine: self.ai_engine.pause_event.set()
                    self.event_queue.put_nowait({"type": "log", "level": LogLevel.WARNING, "msg": f"Taking a smart break for {dur} mins."})
                    play_audio_cue("break", self.pro_audio.get())
                    send_discord_webhook(self.ent_webhook.get(), f"Taking a smart break for {dur} mins", self.tab_name)

                elif self.state == BotState.ON_BREAK and current_time >= self.break_end_time:
                    self.change_state(BotState.RUNNING)
                    inv = safe_get_float(self.ent_brk_int.get(), 30.0)
                    self.next_break_time = current_time + (inv * 60)
                    if self.click_engine: self.click_engine.pause_event.clear()
                    if self.type_engine: self.type_engine.pause_event.clear()
                    if self.anti_afk_engine: self.anti_afk_engine.pause_event.clear()
                    if self.ai_engine: self.ai_engine.pause_event.clear()
                    self.event_queue.put_nowait({"type": "log", "level": LogLevel.INFO, "msg": "Break ended. Resuming."})
                    play_audio_cue("start", self.pro_audio.get())
                    send_discord_webhook(self.ent_webhook.get(), "Break ended. Resuming engines", self.tab_name)

            if self.start_time > 0 and self.state == BotState.RUNNING and self.enable_timed.get():
                elapsed_mins = (current_time - self.start_time) / 60.0
                for child in self.tree_timed.get_children():
                    if child not in self.scheduled_sent:
                        vals = self.tree_timed.item(child, "values")
                        if len(vals) == 2:
                            try:
                                target_min = float(vals[0])
                                if elapsed_mins >= target_min:
                                    self.ai_message_queue.put(str(vals[1]))
                                    self.scheduled_sent.add(child)
                                    self.event_queue.put_nowait({"type": "log", "level": LogLevel.INFO, "msg": f"⏳ Timed Message Triggered: {vals[1]}"})
                            except ValueError: pass

        self.root.after(100, self.process_all_events)

    def change_state(self, new_state: BotState):
        self.state = new_state
        hk_stp = BOT_CONFIG.get("hotkeys", {}).get("stop_core", "F9")
        
        if new_state == BotState.IDLE:
            self.lbl_main_state.config(text="STATE: IDLE", fg="gray")
            self.btn_start.config(state=tk.NORMAL, bg="#4cc9f0")
            self.btn_stop.config(text=f"STOP ({hk_stp})", state=tk.DISABLED, bg="#2b2d42")
        elif new_state == BotState.RUNNING:
            self.lbl_main_state.config(text="STATE: RUNNING", fg="#00ffcc")
            self.btn_start.config(state=tk.DISABLED, bg="#3f3f3f")
            self.btn_stop.config(text=f"STOP ({hk_stp})", state=tk.NORMAL, bg="#ff003c")
        elif new_state == BotState.PAUSED:
            self.lbl_main_state.config(text="STATE: PAUSED", fg="#fca311")
        elif new_state == BotState.ON_BREAK:
            self.lbl_main_state.config(text="STATE: ON BREAK", fg="#ff003c")
        elif new_state == BotState.ERROR:
            self.lbl_main_state.config(text="STATE: ERROR / CAPTCHA", fg="red")
            self.btn_start.config(state=tk.NORMAL, bg="#4cc9f0")
            self.btn_stop.config(text=f"STOP ({hk_stp})", state=tk.DISABLED, bg="#2b2d42")
            play_audio_cue("error", self.pro_audio.get())
            send_discord_webhook(self.ent_webhook.get(), "Panic Trigger / Captcha Detected! Engines stopped.", self.tab_name)

    def _get_click_interval(self) -> float:
        mi, ma = safe_get_float(self.entry_clk_min.get(), BOT_CONFIG["click_min"]), safe_get_float(self.entry_clk_max.get(), BOT_CONFIG["click_max"])
        if mi > ma: mi, ma = ma, mi
        return random.uniform(mi, ma)

    def _get_type_interval(self) -> float:
        mi, ma = safe_get_float(self.entry_typ_min.get(), BOT_CONFIG["type_min"]), safe_get_float(self.entry_typ_max.get(), BOT_CONFIG["type_max"])
        if mi > ma: mi, ma = ma, mi
        return random.uniform(mi, ma)

    def _get_msgs(self) -> list:
        return [m.strip() for m in self.text_msgs.get("1.0", tk.END).strip().split('\n') if m.strip()]

    def set_click_target(self):
        if self.state in [BotState.RUNNING, BotState.PAUSED, BotState.ON_BREAK]: return
        if self.click_tracker.set_target_from_mouse():
            d = self.click_tracker.data
            name = d.title if d.title else d.exe_name
            short_title = name[:25] + "..." if len(name) > 25 else name
            hk_clk = BOT_CONFIG.get("hotkeys", {}).get("target_click", "F8")
            self.lbl_click.config(text=f"[{hk_clk}]  Click: LOCKED ({short_title})", fg="#00ffcc")
            try: self.event_queue.put_nowait({"type": "log", "level": LogLevel.INFO, "msg": f"Click Target: {d.exe_name}"})
            except queue.Full: pass

    def set_type_target(self):
        if self.state in [BotState.RUNNING, BotState.PAUSED, BotState.ON_BREAK]: return
        if self.type_tracker.set_target_from_mouse():
            d = self.type_tracker.data
            name = d.title if d.title else d.exe_name
            short_title = name[:25] + "..." if len(name) > 25 else name
            hk_cht = BOT_CONFIG.get("hotkeys", {}).get("target_chat", "F10")
            self.lbl_type.config(text=f"[{hk_cht}] Chat : LOCKED ({short_title})", fg="#00ffcc")
            try: self.event_queue.put_nowait({"type": "log", "level": LogLevel.INFO, "msg": f"Chat Target: {d.exe_name}"})
            except queue.Full: pass

    def toggle_type_pause(self):
        hk_cht = BOT_CONFIG.get("hotkeys", {}).get("target_chat", "F10")
        if self.type_engine and self.state in [BotState.RUNNING, BotState.PAUSED]:
            if self.type_engine.pause_event.is_set():
                self.type_engine.pause_event.clear()
                self.lbl_type.config(text=f"[{hk_cht}] Chat : ENGAGED", fg="#00ffcc")
                self.event_queue.put_nowait({"type": "log", "level": LogLevel.INFO, "msg": "Chat Engine: RESUMED"})
            else:
                self.type_engine.pause_event.set()
                self.lbl_type.config(text=f"[{hk_cht}] Chat : PAUSED", fg="#fca311")
                self.event_queue.put_nowait({"type": "log", "level": LogLevel.WARNING, "msg": "Chat Engine: PAUSED"})

    def toggle_click_pause(self):
        hk_clk = BOT_CONFIG.get("hotkeys", {}).get("target_click", "F8")
        if self.click_engine and self.state in [BotState.RUNNING, BotState.PAUSED]:
            if self.click_engine.pause_event.is_set():
                self.click_engine.pause_event.clear()
                self.lbl_click.config(text=f"[{hk_clk}]  Click: ENGAGED", fg="#00ffcc")
                self.event_queue.put_nowait({"type": "log", "level": LogLevel.INFO, "msg": "Click Engine: RESUMED"})
            else:
                self.click_engine.pause_event.set()
                self.lbl_click.config(text=f"[{hk_clk}]  Click: PAUSED", fg="#fca311")
                self.event_queue.put_nowait({"type": "log", "level": LogLevel.WARNING, "msg": "Click Engine: PAUSED"})

    def start_bot(self):
        if self.state in [BotState.RUNNING, BotState.ON_BREAK]: return
        if self.click_tracker.data.hwnd == 0 and self.type_tracker.data.hwnd == 0:
            return messagebox.showwarning("Target Warning", "Please target at least one window area using the click or chat hotkey first.")
            
        self.start_time = time.time()
        self.scheduled_sent.clear()
        
        self.change_state(BotState.RUNNING)
        self.event_queue.put_nowait({"type": "log", "level": LogLevel.INFO, "msg": "MAIN SEQUENCE INITIATED"})
        play_audio_cue("start", self.pro_audio.get())
        send_discord_webhook(self.ent_webhook.get(), "Engines Initiated!", self.tab_name)

        if self.pro_breaks.get(): self.next_break_time = self.start_time + (safe_get_float(self.ent_brk_int.get(), 30.0) * 60)
        if self.pro_autostop.get(): self.auto_stop_target = self.start_time + (safe_get_float(self.ent_stop_dur.get(), 120.0) * 60)

        if self.click_tracker.data.hwnd != 0:
            self.click_engine = ClickEngine("ClickEngine", self.click_tracker, self.event_queue, self._get_click_interval, self.action_lock, self.stealth_mode.get, self.sys_logger)
            self.click_engine.start()
            
        if self.type_tracker.data.hwnd != 0:
            self.type_engine = TypingEngine(
                "TypeEngine", self.type_tracker, self.event_queue, self._get_type_interval, self._get_msgs, 
                self.action_lock, self.stealth_mode.get, self.sys_logger, self.ai_message_queue, self.enable_spintax.get
            )
            self.type_engine.start()
            
            if self.pro_ai_vision.get():
                self.ai_engine = AIVisionEngine(
                    "AIEngine", self.type_tracker, self.event_queue, self.action_lock, self.sys_logger, 
                    self.ent_api_key.get, self.ai_message_queue, lambda: self.text_ai_prompt.get("1.0", tk.END).strip(),
                    self.ent_ai_min.get, self.ent_ai_max.get, self.combo_ai_provider.get
                )
                self.ai_engine.start()
                
        if self.pro_panic.get():
            active_tracker = self.type_tracker if self.type_tracker.data.hwnd != 0 else self.click_tracker
            if active_tracker.data.hwnd != 0:
                self.panic_engine = PanicEngine(
                    "PanicTrigger", active_tracker, self.event_queue, self.action_lock, self.sys_logger, self.stop_bot, 
                    lambda: [w.strip().lower() for w in self.text_panic_words.get("1.0", tk.END).split("\n") if w.strip()]
                )
                self.panic_engine.start()
            
        if self.anti_afk_mode.get():
            target = self.type_tracker if self.type_tracker.data.hwnd != 0 else self.click_tracker
            self.anti_afk_engine = AntiAfkEngine("AntiAFK", target, self.event_queue, lambda: 30, self.action_lock, self.stealth_mode.get, self.sys_logger)
            self.anti_afk_engine.start()

    def stop_bot(self):
        if self.state == BotState.IDLE: return
            
        if self.click_engine: self.click_engine.stop_event.set()
        if self.type_engine: self.type_engine.stop_event.set()
        if self.anti_afk_engine: self.anti_afk_engine.stop_event.set()
        if self.panic_engine: self.panic_engine.stop_event.set()
        if self.ai_engine: self.ai_engine.stop_event.set()

        if self.click_engine and self.click_engine.is_alive(): self.click_engine.join(timeout=0.5)
        if self.type_engine and self.type_engine.is_alive(): self.type_engine.join(timeout=0.5)
        if self.anti_afk_engine and self.anti_afk_engine.is_alive(): self.anti_afk_engine.join(timeout=0.5)
        if self.panic_engine and self.panic_engine.is_alive(): self.panic_engine.join(timeout=0.5)
        if self.ai_engine and self.ai_engine.is_alive(): self.ai_engine.join(timeout=0.5)

        self.click_engine = self.type_engine = self.anti_afk_engine = self.panic_engine = self.ai_engine = None
        
        while not self.ai_message_queue.empty():
            try: self.ai_message_queue.get_nowait()
            except queue.Empty: break
        
        if self.state != BotState.ERROR: self.change_state(BotState.IDLE)
        self.event_queue.put_nowait({"type": "log", "level": LogLevel.INFO, "msg": "MAIN SEQUENCE HALTED"})
        play_audio_cue("stop", self.pro_audio.get())
        send_discord_webhook(self.ent_webhook.get(), f"Engines Halted! \nClicks: {self.stats_clicks} | Messages: {self.stats_msgs}", self.tab_name)

    def delete_self(self):
        self.stop_bot()
        if hasattr(self, 'overlay_win') and self.overlay_win.winfo_exists(): self.overlay_win.destroy()
        self.delete_callback(self.tab_id)

    def get_profile_data(self):
        c_prof = asdict(self.click_tracker.data)
        t_prof = asdict(self.type_tracker.data)
        c_prof["hwnd"] = c_prof["pid"] = t_prof["hwnd"] = t_prof["pid"] = 0
        timed_list = [self.tree_timed.item(child, "values") for child in self.tree_timed.get_children()]
            
        return {
            "stealth": self.stealth_mode.get(),
            "afk": self.anti_afk_mode.get(),
            "c_min": self.entry_clk_min.get(),
            "c_max": self.entry_clk_max.get(),
            "t_min": self.entry_typ_min.get(),
            "t_max": self.entry_typ_max.get(),
            "en_spin": self.enable_spintax.get(),
            "en_timed": self.enable_timed.get(),
            "msgs": self.text_msgs.get("1.0", tk.END),
            "timed_msgs": timed_list,
            "t_clk": c_prof,
            "t_typ": t_prof,
            "wh": obfuscate_secret(self.ent_webhook.get()),
            "panic": self.pro_panic.get(),
            "p_words": self.text_panic_words.get("1.0", tk.END).strip(),
            "ai_vision": self.pro_ai_vision.get(),
            "ai_prov": self.combo_ai_provider.get(),
            "api_key": obfuscate_secret(self.ent_api_key.get()),
            "ai_min": self.ent_ai_min.get(),
            "ai_max": self.ent_ai_max.get(),
            "ai_prompt": self.text_ai_prompt.get("1.0", tk.END)
        }

    def load_profile_data(self, data):
        self.stealth_mode.set(data.get("stealth", True))
        self.anti_afk_mode.set(data.get("afk", False))
        self.enable_spintax.set(data.get("en_spin", True))
        self.enable_timed.set(data.get("en_timed", True))
        self.pro_panic.set(data.get("panic", True))
        self.pro_ai_vision.set(data.get("ai_vision", False))
        self.update_stealth_label()
        
        self.entry_clk_min.delete(0, tk.END); self.entry_clk_min.insert(0, data.get("c_min", str(BOT_CONFIG["click_min"])))
        self.entry_clk_max.delete(0, tk.END); self.entry_clk_max.insert(0, data.get("c_max", str(BOT_CONFIG["click_max"])))
        self.entry_typ_min.delete(0, tk.END); self.entry_typ_min.insert(0, data.get("t_min", str(BOT_CONFIG["type_min"])))
        self.entry_typ_max.delete(0, tk.END); self.entry_typ_max.insert(0, data.get("t_max", str(BOT_CONFIG["type_max"])))
        self.text_msgs.delete("1.0", tk.END); self.text_msgs.insert(tk.END, data.get("msgs", ""))
        
        if "timed_msgs" in data:
            self.tree_timed.delete(*self.tree_timed.get_children())
            for item in data["timed_msgs"]: self.tree_timed.insert("", "end", values=item)
        
        self.ent_webhook.delete(0, tk.END); self.ent_webhook.insert(0, deobfuscate_secret(data.get("wh", "")))
        self.text_panic_words.delete("1.0", tk.END); self.text_panic_words.insert(tk.END, data.get("p_words", BOT_CONFIG.get("panic_words", "")))
        
        self.combo_ai_provider.set(data.get("ai_prov", BOT_CONFIG.get("ai_provider", "Gemini")))
        self.ent_api_key.delete(0, tk.END); self.ent_api_key.insert(0, deobfuscate_secret(data.get("api_key", "")))
        self.ent_ai_min.delete(0, tk.END); self.ent_ai_min.insert(0, data.get("ai_min", str(BOT_CONFIG["ai_interval_min"])))
        self.ent_ai_max.delete(0, tk.END); self.ent_ai_max.insert(0, data.get("ai_max", str(BOT_CONFIG["ai_interval_max"])))
        self.text_ai_prompt.delete("1.0", tk.END); self.text_ai_prompt.insert(tk.END, data.get("ai_prompt", BOT_CONFIG["ai_prompt"]))
        
        if "t_clk" in data:
            self.click_tracker.data = TargetData(**data["t_clk"])
            if self.click_tracker.recover():
                name = self.click_tracker.data.title if self.click_tracker.data.title else self.click_tracker.data.exe_name
                short = name[:25] + "..." if len(name) > 25 else name
                hk_clk = BOT_CONFIG.get("hotkeys", {}).get("target_click", "F8")
                self.lbl_click.config(text=f"[{hk_clk}]  Click: RECOVERED ({short})", fg="#4cc9f0")
                
        if "t_typ" in data:
            self.type_tracker.data = TargetData(**data["t_typ"])
            if self.type_tracker.recover():
                name = self.type_tracker.data.title if self.type_tracker.data.title else self.type_tracker.data.exe_name
                short = name[:25] + "..." if len(name) > 25 else name
                hk_cht = BOT_CONFIG.get("hotkeys", {}).get("target_chat", "F10")
                self.lbl_type.config(text=f"[{hk_cht}] Chat : RECOVERED ({short})", fg="#4cc9f0")