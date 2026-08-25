import os
import sys
import json
import queue
import threading
import logging

from PIL import Image
import pystray
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from config import BOT_CONFIG, APP_NAME, BotState
from utils import (
    enable_high_dpi_awareness, create_system_logger, is_admin, 
    get_app_icon_path, get_autosave_path, NativeHotkeyManager
)
from bot_instance import BotInstance


class MultiBotManager:
    def __init__(self, root: tk.Tk, sys_logger: logging.Logger):
        self.root = root
        self.sys_logger = sys_logger
        self.root.title(APP_NAME)
        self.root.geometry("820x870")
        self.root.configure(bg="#02040a")
        self.is_light_mode = True

        icon_path = get_app_icon_path()
        if icon_path:
            try:
                if icon_path.lower().endswith(".ico"):
                    self.root.iconbitmap(icon_path)
                else:
                    self.root.iconphoto(True, tk.PhotoImage(file=icon_path))
            except Exception as e:
                self.sys_logger.warning(f"Could not load application icon: {e}")

        self.bot_count = 0
        self.bots = {}
        self.global_event_queue = queue.Queue(maxsize=100)
        self.is_hidden_to_tray = False
        self.tray_icon = None

        self.sys_logger.info(f"{APP_NAME} Boot Complete.")

        # Header Frame
        frame_header = tk.Frame(self.root, bg="#02040a")
        frame_header.pack(fill="x", padx=10, pady=10)

        tk.Label(frame_header, text="STREAM BOT", font=("Consolas", 11, "bold"), bg="#02040a", fg="#00ffcc").pack(side="left", padx=5)
        tk.Button(frame_header, text="SAVE", font=("Consolas", 9, "bold"), bg="#3a0ca3", fg="white", bd=0, padx=8, command=self.save_profiles).pack(side="left", padx=3)
        tk.Button(frame_header, text="LOAD", font=("Consolas", 9, "bold"), bg="#4361ee", fg="white", bd=0, padx=8, command=self.load_profiles).pack(side="left", padx=3)
        tk.Button(frame_header, text="TUTORIAL", font=("Consolas", 9, "bold"), bg="#7209b7", fg="white", bd=0, padx=8, command=self.show_tutorial).pack(side="left", padx=3)
        tk.Button(frame_header, text="ABOUT", font=("Consolas", 9, "bold"), bg="#480ca8", fg="white", bd=0, padx=8, command=self.show_about).pack(side="left", padx=3)
        
        self.btn_theme = tk.Button(frame_header, text="THEME", font=("Consolas", 9, "bold"), bg="#ffb703", fg="black", bd=0, padx=8, command=self.toggle_theme)
        self.btn_theme.pack(side="left", padx=3)
        
        tk.Button(frame_header, text="+ NEW CORE", font=("Consolas", 10, "bold"), bg="#4cc9f0", fg="black", bd=0, padx=10, command=self.add_bot_tab).pack(side="right", padx=3)

        self.check_admin()

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=5)

        hk_cfg = BOT_CONFIG.get("hotkeys", {})
        hk_clk = hk_cfg.get("target_click", "F8")
        hk_cht = hk_cfg.get("target_chat", "F10")
        hk_tgl = hk_cfg.get("toggle_core", "F6")
        hk_bos = hk_cfg.get("boss_key", "F7")
        hk_kil = hk_cfg.get("kill_all", "F4")

        lbl_txt = f"[{hk_clk}] Target Click   [{hk_cht}] Target Chat   [{hk_tgl}] Start/Stop   [{hk_bos}] Boss Key   [{hk_kil}] KILL ALL"
        tk.Label(self.root, text=lbl_txt, font=("Consolas", 9, "bold"), bg="#02040a", fg="#fca311").pack(pady=10)

        self.hotkey_thread = NativeHotkeyManager(self.global_event_queue, self.sys_logger)
        self.hotkey_thread.start()

        self.auto_load_profiles()
        if self.bot_count == 0:
            self.add_bot_tab()

        self.process_global_events()
        self.toggle_theme() # Start in dark mode

    def toggle_theme(self):
        self.is_light_mode = not self.is_light_mode
        self.btn_theme.config(text="THEME")

        if self.is_light_mode:
            t = {"bg_m": "#f0f2f5", "bg_s": "#ffffff", "fg_m": "#000000", "fg_s": "#333333", "acc": "#0052cc", "txt": "#e2e8f0"}
        else:
            t = {"bg_m": "#02040a", "bg_s": "#0a1128", "fg_m": "white", "fg_s": "gray", "acc": "#00ffcc", "txt": "#1a1a2e"}

        self.root.configure(bg=t["bg_m"])

        def apply_colors(parent):
            for widget in parent.winfo_children():
                wtype = widget.winfo_class()
                try:
                    if wtype in ("Frame", "LabelFrame", "PanedWindow"):
                        widget.configure(bg=t["bg_s"])
                    elif wtype in ("Label", "Checkbutton", "Radiobutton"):
                        widget.configure(bg=t["bg_s"], fg=t["fg_m"])
                        if wtype != "Label":
                            widget.configure(selectcolor=t["bg_m"], activebackground=t["bg_s"], activeforeground=t["fg_m"])
                    elif wtype in ("Text", "Entry"):
                        widget.configure(bg=t["txt"], fg=t["acc"], insertbackground=t["acc"])
                    elif wtype == "Button":
                        widget.configure(activebackground=t["bg_s"], activeforeground=t["fg_m"])
                except Exception:
                    pass
                apply_colors(widget)

        apply_colors(self.root)

        style = ttk.Style()
        try: style.theme_use("default")
        except Exception: pass

        style.configure("TNotebook", background=t["bg_m"], borderwidth=0)
        style.configure("TNotebook.Tab", background=t["bg_s"], foreground=t["fg_s"])
        style.map("TNotebook.Tab", background=[("selected", t["bg_m"])], foreground=[("selected", t["acc"])])
        
        style.configure("Treeview", background=t["bg_s"], fieldbackground=t["bg_s"], foreground=t["fg_m"], borderwidth=0)
        style.configure("Treeview.Heading", background=t["bg_m"], foreground=t["acc"])
        style.map("Treeview", background=[("selected", t["acc"])], foreground=[("selected", t["bg_s"])])

    def auto_save_profiles(self):
        try:
            profiles = {f"Core_{i}": bot.get_profile_data() for i, bot in enumerate(self.bots.values(), start=1)}
            with open(get_autosave_path(), "w", encoding="utf-8") as file:
                json.dump(profiles, file, indent=4)
        except Exception as e:
            self.sys_logger.error(f"Auto-save failed: {e}")

    def auto_load_profiles(self):
        try:
            save_path = get_autosave_path()
            if not os.path.exists(save_path): return

            with open(save_path, "r", encoding="utf-8") as file:
                profiles = json.load(file)

            if profiles:
                for data in profiles.values():
                    self.add_bot_tab()
                    last_bot_id = list(self.bots.keys())[-1]
                    self.bots[last_bot_id].load_profile_data(data)
                self.sys_logger.info("Previous session auto-loaded successfully.")
        except Exception as e:
            self.sys_logger.error(f"Auto-load failed: {e}")

    def show_about(self):
        messagebox.showinfo(
            "About", "Stream Bot\n\nFor business & inquiries:\n• Email: adonis.ploae@yahoo.com\n• Instagram: @adn.sw"
        )

    def show_tutorial(self):
        tut_win = tk.Toplevel(self.root)
        tut_win.title("Guide & Spintax")
        tut_win.geometry("720x650")

        t_bg, t_fg, t_txt, t_txt_fg = ("#f0f2f5", "#000000", "#ffffff", "#333333") if self.is_light_mode else ("#050a15", "#00ffcc", "#0a1128", "white")
        tut_win.configure(bg=t_bg)

        tk.Label(tut_win, text="STREAM BOT - USER GUIDE & PRO FEATURES", font=("Consolas", 11, "bold"), bg=t_bg, fg=t_fg).pack(pady=10)
        txt_frame = tk.Frame(tut_win, bg=t_txt, bd=1, relief="solid")
        txt_frame.pack(fill="both", expand=True, padx=15, pady=5)

        scroll = tk.Scrollbar(txt_frame)
        scroll.pack(side="right", fill="y")
        
        txt = tk.Text(txt_frame, font=("Consolas", 9), bg=t_txt, fg=t_txt_fg, bd=0, padx=10, pady=10, wrap="word", yscrollcommand=scroll.set, insertbackground=t_fg)
        txt.pack(fill="both", expand=True)
        scroll.config(command=txt.yview)

        hk_cfg = BOT_CONFIG.get("hotkeys", {})
        guide = f"""
1. WHAT IS SPINTAX & HOW TO WRITE MESSAGES
Spintax allows you to create dynamic, randomized chat messages.
Syntax Format: {{option1|option2|option3}}

2. MULTI-INSTANCE AUTOMATION
Click the NEW CORE button at the top right to create multiple independent bot cores.

3. HOTKEY QUICK REFERENCE
- [{hk_cfg.get("target_click", "F8")}] Lock Click Target - any window button/area.
- [{hk_cfg.get("target_chat", "F10")}] Lock Chat Target - any chat text input box.
- [{hk_cfg.get("toggle_core", "F6")}] QUICK START / TOGGLE.
- [{hk_cfg.get("stop_core", "F9")}] STOP ACTIVE CORE.
- [{hk_cfg.get("kill_all", "F4")}] KILL ALL - PANIC.
- [{hk_cfg.get("boss_key", "F7")}] BOSS KEY - Hide to Tray.

4. TIMED / SCHEDULED MESSAGES
Type messages in the table. For example: 10.5 mins.

5. PANIC TRIGGER / FAILSAFE
Automatically monitors the Target Window title for Captchas or account bans.

6. AI VISION BOT
When enabled, takes a screenshot of the live stream and generates a realistic reaction message.

7. STEALTH MODE / BACKGROUND OPERATION
Sends clicks and text directly into the application's background code.
"""
        txt.insert("1.0", guide)
        txt.config(state=tk.DISABLED)

        tk.Button(tut_win, text="CLOSE GUIDE", font=("Consolas", 9, "bold"), bg="#4cc9f0", fg="black", bd=0, padx=15, pady=5, command=tut_win.destroy).pack(pady=10)

    def process_global_events(self):
        while not self.global_event_queue.empty():
            try:
                event = self.global_event_queue.get_nowait()
                if event.get("type") == "hotkey":
                    self.route_hotkey(event.get("action"))
            except queue.Empty:
                break
            except Exception as e:
                self.sys_logger.error(f"Global event error: {e}")
        self.root.after(100, self.process_global_events)

    def check_admin(self):
        if not is_admin():
            notice_frame = tk.Frame(self.root, bg="#780000")
            notice_frame.pack(fill="x", padx=10, pady=(0, 5))
            tk.Label(notice_frame, text="NOTICE: Run this app as Administrator if games/windows block the bot's inputs.", font=("Consolas", 9, "bold"), bg="#780000", fg="#ffffff").pack(side="left", padx=10, pady=4)
            tk.Button(notice_frame, text="DISMISS", font=("Consolas", 8, "bold"), bg="#ff4444", fg="white", bd=0, padx=10, command=notice_frame.destroy).pack(side="right", padx=10, pady=2)

    def add_bot_tab(self):
        self.bot_count += 1
        new_frame = tk.Frame(self.notebook, bg="#050a15")
        tab_id = str(new_frame)
        tab_name = f"CORE {self.bot_count}"

        self.notebook.add(new_frame, text=tab_name)
        self.bots[tab_id] = BotInstance(new_frame, tab_id, tab_name, self.delete_tab, self.root, self.sys_logger)
        self.notebook.select(new_frame)
        self.toggle_theme()

    def delete_tab(self, tab_id: str):
        try: self.notebook.forget(tab_id)
        except Exception: pass

        if tab_id in self.bots:
            del self.bots[tab_id]
        if not self.bots:
            self.add_bot_tab()

    def get_active_bot(self):
        return self.bots.get(self.notebook.select())

    def save_profiles(self):
        try:
            profiles = {f"Core_{i}": bot.get_profile_data() for i, bot in enumerate(self.bots.values(), start=1)}
            file_path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON Profile", "*.json")])
            if file_path:
                with open(file_path, "w", encoding="utf-8") as file:
                    json.dump(profiles, file, indent=4)
                self.sys_logger.info(f"Profiles saved to {file_path}")
        except Exception as e:
            messagebox.showerror("Save Error", str(e))

    def load_profiles(self):
        try:
            file_path = filedialog.askopenfilename(filetypes=[("JSON Profile", "*.json")])
            if not file_path: return

            with open(file_path, "r", encoding="utf-8") as file:
                profiles = json.load(file)

            for tab_id in list(self.bots.keys()):
                try: self.bots[tab_id].stop_bot()
                except Exception: pass
                try: self.notebook.forget(tab_id)
                except Exception: pass

            self.bots.clear()
            self.bot_count = 0

            for data in profiles.values():
                self.add_bot_tab()
                last_bot_id = list(self.bots.keys())[-1]
                self.bots[last_bot_id].load_profile_data(data)

            self.sys_logger.info(f"Profiles loaded from {file_path}")
        except Exception as e:
            messagebox.showerror("Load Error", str(e))

    def toggle_boss_key(self):
        if not self.is_hidden_to_tray:
            self.root.withdraw()
            self.is_hidden_to_tray = True

            if not self.tray_icon:
                icon_path = get_app_icon_path()
                try:
                    img = Image.open(icon_path) if icon_path else Image.new("RGB", (64, 64), color=(0, 255, 204))
                except Exception:
                    img = Image.new("RGB", (64, 64), color=(0, 255, 204))

                menu = pystray.Menu(
                    pystray.MenuItem("Show StreamBot", self.restore_from_tray),
                    pystray.MenuItem("Exit", self.exit_from_tray),
                )
                self.tray_icon = pystray.Icon("StreamBot", img, "StreamBot running...", menu)
                threading.Thread(target=self.tray_icon.run, daemon=True).start()
        else:
            self.restore_from_tray(None, None)

    def restore_from_tray(self, icon, item):
        self.root.after(0, self._do_restore)

    def _do_restore(self):
        self.root.deiconify()
        self.is_hidden_to_tray = False
        if self.tray_icon:
            try: self.tray_icon.stop()
            except Exception: pass
            self.tray_icon = None

    def exit_from_tray(self, icon, item):
        if self.tray_icon:
            try: self.tray_icon.stop()
            except Exception: pass
        self.root.after(0, self.on_closing)

    def route_hotkey(self, action: str):
        if action == "boss_key": return self.toggle_boss_key()
        if action == "kill_all": return self.on_closing()
        
        bot = self.get_active_bot()
        if not bot: return

        if action == "target_click": bot.set_click_target()
        elif action == "target_chat": bot.set_type_target()
        elif action == "stop_core": bot.stop_bot()
        elif action == "toggle_core":
            bot.start_bot() if bot.state in (BotState.IDLE, BotState.ERROR) else bot.stop_bot()
        elif action == "pause_click": bot.toggle_click_pause()

    def on_closing(self):
        self.sys_logger.info("Graceful Shutdown Initiated.")
        try: self.auto_save_profiles()
        except Exception as e: self.sys_logger.error(f"Auto-save during shutdown failed: {e}")

        for bot in self.bots.values():
            try: bot.stop_bot()
            except Exception as e: self.sys_logger.error(f"Failed to stop bot: {e}")

        try: self.hotkey_thread.stop()
        except Exception: pass

        if self.tray_icon:
            try: self.tray_icon.stop()
            except Exception: pass

        self.root.destroy()
        sys.exit()

if __name__ == "__main__":
    enable_high_dpi_awareness()
    logger = create_system_logger()
    root = tk.Tk()
    app = MultiBotManager(root, logger)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
