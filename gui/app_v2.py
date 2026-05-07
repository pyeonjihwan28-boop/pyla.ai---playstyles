"""Single-window tabbed App (Stage 1 MVP).

The whole UI lives under one ctk.CTk() root with a CTkTabview holding
five tabs: Setup, Queue, Live, History, Settings. Subsequent stages
fill out the Setup/Queue/History/Settings tabs; Stage 1 wires Live tab
controls (Start / Pause / Stop) to a shared BotController and renders
status updates drained from BotController.update_queue.

Bot thread NEVER touches widgets — UI thread polls update_queue via
root.after() and applies state diffs to the Live tab labels.
"""
import queue
import threading
from typing import Callable

import customtkinter as ctk

from bot_controller import BotController
from bot_state import (
    STATUS_COMPLETED,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_PAUSED,
    STATUS_RUNNING,
    STATUS_STOPPED,
)
from logger import log
from utils import load_toml_as_dict, save_dict_as_toml
import bs_api


_POLL_INTERVAL_MS = 100


class TabbedApp:
    """Owns the single ctk.CTk root, the BotController, and the tab widgets."""

    def __init__(self, brawlers_data_provider: Callable[[], list], pyla_version: str = ""):
        self.brawlers_data_provider = brawlers_data_provider
        self.controller = BotController()

        self.root = ctk.CTk()
        self.root.title(f"PylaAI {pyla_version}")
        self.root.geometry("1100x700")

        self.tabs = ctk.CTkTabview(self.root)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=12)

        for name in ("Setup", "Queue", "Live", "History", "Settings"):
            self.tabs.add(name)

        self._build_setup_tab(self.tabs.tab("Setup"))
        self._build_queue_tab(self.tabs.tab("Queue"))
        self._build_live_tab(self.tabs.tab("Live"))
        self._build_history_tab(self.tabs.tab("History"))
        self._build_settings_tab(self.tabs.tab("Settings"))

        self.tabs.set("Live")
        self.root.after(_POLL_INTERVAL_MS, self._poll_bot_queue)

    # --- Tab builders -----------------------------------------------------

    def _build_setup_tab(self, parent):
        ctk.CTkLabel(
            parent, text="Setup",
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w", padx=20, pady=(20, 6))
        ctk.CTkLabel(
            parent,
            text=("Configure brawlers, push targets, and wins/trophies here.\n"
                  "Stage 2 will fetch your roster from the Brawl Stars API.\n"
                  "For now, use the legacy wizard or import existing data."),
            justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 12))

    def _build_queue_tab(self, parent):
        ctk.CTkLabel(
            parent, text="Push queue",
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w", padx=20, pady=(20, 6))
        ctk.CTkLabel(
            parent,
            text="Stage 3: ordered list of brawlers to push, with reorder/add/remove.",
            justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 12))

    def _build_live_tab(self, parent):
        header = ctk.CTkLabel(parent, text="Live", font=("Segoe UI", 22, "bold"))
        header.pack(anchor="w", padx=20, pady=(20, 6))

        controls = ctk.CTkFrame(parent)
        controls.pack(anchor="w", padx=20, pady=(0, 12))

        self.start_button = ctk.CTkButton(controls, text="Start", width=120, command=self._on_start)
        self.start_button.pack(side="left", padx=(0, 8))
        self.pause_button = ctk.CTkButton(controls, text="Pause", width=120, command=self._on_pause, state="disabled")
        self.pause_button.pack(side="left", padx=(0, 8))
        self.stop_button = ctk.CTkButton(controls, text="Stop", width=120, command=self._on_stop, state="disabled")
        self.stop_button.pack(side="left", padx=(0, 8))

        status_frame = ctk.CTkFrame(parent)
        status_frame.pack(anchor="w", padx=20, pady=(8, 12), fill="x")
        ctk.CTkLabel(status_frame, text="Status:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.status_label = ctk.CTkLabel(status_frame, text="idle", font=("Segoe UI", 14, "bold"))
        self.status_label.grid(row=0, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(status_frame, text="Brawler:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.brawler_label = ctk.CTkLabel(status_frame, text="—")
        self.brawler_label.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(status_frame, text="Trophies (computed / API):").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        self.trophies_label = ctk.CTkLabel(status_frame, text="0 / —")
        self.trophies_label.grid(row=2, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(status_frame, text="FPS:").grid(row=3, column=0, sticky="w", padx=8, pady=4)
        self.fps_label = ctk.CTkLabel(status_frame, text="—")
        self.fps_label.grid(row=3, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(status_frame, text="Message:").grid(row=4, column=0, sticky="nw", padx=8, pady=4)
        self.message_label = ctk.CTkLabel(status_frame, text="", wraplength=700, justify="left")
        self.message_label.grid(row=4, column=1, sticky="w", padx=8, pady=4)

    def _build_history_tab(self, parent):
        ctk.CTkLabel(
            parent, text="History",
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w", padx=20, pady=(20, 6))
        ctk.CTkLabel(
            parent, text="Stage 5: SQLite-backed match/session history.",
            justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 12))

    def _build_settings_tab(self, parent):
        ctk.CTkLabel(
            parent, text="Settings",
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w", padx=20, pady=(20, 6))

        bs_section = ctk.CTkFrame(parent)
        bs_section.pack(fill="x", padx=20, pady=(0, 12))
        ctk.CTkLabel(
            bs_section, text="Brawl Stars API",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            bs_section,
            text=("Token tied to your public IP. Get one at developer.brawlstars.com\n"
                  "→ Create New Key → enter your current IP. Token works only from that IP."),
            justify="left",
            text_color="#9aa0a6",
        ).pack(anchor="w", padx=12, pady=(0, 8))

        login_cfg = load_toml_as_dict("cfg/login.toml")
        token_initial = login_cfg.get("bs_api_token", "")
        tag_initial = login_cfg.get("player_tag", "")

        token_row = ctk.CTkFrame(bs_section, fg_color="transparent")
        token_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(token_row, text="API token:", width=110, anchor="w").pack(side="left")
        self.token_entry = ctk.CTkEntry(token_row, show="*", width=480)
        self.token_entry.pack(side="left", padx=(0, 8))
        self.token_entry.insert(0, token_initial)
        self._token_visible = False

        def _toggle_token_visibility():
            self._token_visible = not self._token_visible
            self.token_entry.configure(show="" if self._token_visible else "*")
            show_btn.configure(text="Hide" if self._token_visible else "Show")

        show_btn = ctk.CTkButton(token_row, text="Show", width=70, command=_toggle_token_visibility)
        show_btn.pack(side="left")

        tag_row = ctk.CTkFrame(bs_section, fg_color="transparent")
        tag_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(tag_row, text="Player tag:", width=110, anchor="w").pack(side="left")
        self.tag_entry = ctk.CTkEntry(tag_row, width=480)
        self.tag_entry.pack(side="left")
        self.tag_entry.insert(0, tag_initial)

        button_row = ctk.CTkFrame(bs_section, fg_color="transparent")
        button_row.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkButton(button_row, text="Save", width=120, command=self._on_save_bs_api).pack(side="left", padx=(0, 8))
        ctk.CTkButton(button_row, text="Test connection", width=160, command=self._on_test_bs_api).pack(side="left")

        self.bs_status_label = ctk.CTkLabel(bs_section, text="", anchor="w", wraplength=700, justify="left")
        self.bs_status_label.pack(anchor="w", fill="x", padx=12, pady=(4, 12))

    def _on_save_bs_api(self):
        # Preserve existing fields (e.g. login key) and only update our two.
        cfg = dict(load_toml_as_dict("cfg/login.toml"))
        cfg["bs_api_token"] = self.token_entry.get().strip()
        cfg["player_tag"] = self.tag_entry.get().strip()
        save_dict_as_toml(cfg, "cfg/login.toml")
        self._set_bs_status("Saved.", color="#7ed47a")

    def _on_test_bs_api(self):
        tag = self.tag_entry.get().strip()
        # Re-save first so the freshly-typed token is what get_client() reads.
        self._on_save_bs_api()
        self._set_bs_status("Testing…", color="#9aa0a6")

        def _worker():
            try:
                ok, msg = bs_api.get_client().test_connection(tag)
            except Exception as e:
                ok, msg = False, f"unexpected error: {e!r}"
            self.root.after(0, lambda: self._set_bs_status(msg, color="#7ed47a" if ok else "#e57373"))

        threading.Thread(target=_worker, name="bs-api-test", daemon=True).start()

    def _set_bs_status(self, msg: str, color: str = "#9aa0a6"):
        self.bs_status_label.configure(text=msg, text_color=color)

    # --- Button handlers --------------------------------------------------

    def _on_start(self):
        try:
            data = self.brawlers_data_provider()
        except Exception as e:
            log.exception("brawlers_data_provider failed")
            self.message_label.configure(text=f"start failed: {e!r}")
            return
        if not data:
            self.message_label.configure(text="no brawlers configured")
            return
        self.controller.start(data)
        self.start_button.configure(state="disabled")
        self.pause_button.configure(state="normal", text="Pause")
        self.stop_button.configure(state="normal")

    def _on_pause(self):
        if self.controller.state.status == STATUS_PAUSED:
            self.controller.resume()
            self.pause_button.configure(text="Pause")
        else:
            self.controller.pause()
            self.pause_button.configure(text="Resume")

    def _on_stop(self):
        self.controller.stop()
        self.stop_button.configure(state="disabled")

    # --- Polling loop -----------------------------------------------------

    def _poll_bot_queue(self):
        try:
            while True:
                event = self.controller.update_queue.get_nowait()
                self._apply_event(event)
        except queue.Empty:
            pass
        finally:
            self.root.after(_POLL_INTERVAL_MS, self._poll_bot_queue)

    def _apply_event(self, event: dict):
        status = event.get("status")
        self.status_label.configure(text=status or "—")
        self.brawler_label.configure(text=event.get("current_brawler") or "—")
        comp = event.get("computed_trophies")
        api = event.get("api_trophies")
        self.trophies_label.configure(
            text=f"{comp if comp is not None else '—'} / {api if api is not None else '—'}"
        )
        fps = event.get("fps")
        self.fps_label.configure(text=f"{fps:.1f}" if isinstance(fps, (int, float)) and fps else "—")
        self.message_label.configure(text=event.get("message") or "")

        if status in (STATUS_COMPLETED, STATUS_STOPPED, STATUS_ERROR, STATUS_IDLE):
            self.start_button.configure(state="normal")
            self.pause_button.configure(state="disabled", text="Pause")
            self.stop_button.configure(state="disabled")
        elif status == STATUS_RUNNING:
            self.start_button.configure(state="disabled")
            self.pause_button.configure(state="normal", text="Pause")
            self.stop_button.configure(state="normal")

    def start(self):
        self.root.mainloop()
