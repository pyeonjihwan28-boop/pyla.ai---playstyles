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
import app_state


_POLL_INTERVAL_MS = 100


class TabbedApp:
    """Owns the single ctk.CTk root, the BotController, and the tab widgets."""

    def __init__(self, brawlers_data_provider: Callable[[], list], pyla_version: str = ""):
        self.brawlers_data_provider = brawlers_data_provider
        self.controller = BotController()
        self._roster: list = []
        self._roster_sort = "trophies"  # 'name' | 'trophies' | 'winstreak'

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
            parent, text="Setup — your roster",
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w", padx=20, pady=(20, 4))

        controls = ctk.CTkFrame(parent, fg_color="transparent")
        controls.pack(fill="x", padx=20, pady=(0, 8))
        self.refresh_roster_btn = ctk.CTkButton(
            controls, text="Refresh from API", width=160, command=self._on_refresh_roster
        )
        self.refresh_roster_btn.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(controls, text="Sort:").pack(side="left", padx=(8, 4))
        self.sort_choice = ctk.CTkOptionMenu(
            controls,
            values=["trophies", "name", "winstreak"],
            width=140,
            command=self._on_sort_changed,
        )
        self.sort_choice.set(self._roster_sort)
        self.sort_choice.pack(side="left")

        self.roster_status_label = ctk.CTkLabel(parent, text="", anchor="w", text_color="#9aa0a6")
        self.roster_status_label.pack(anchor="w", padx=20, pady=(0, 4), fill="x")

        # API-disabled banner
        self.api_disabled_banner = ctk.CTkLabel(
            parent,
            text="Configure your API token in the Settings tab before refreshing.",
            anchor="w",
            text_color="#e0a93a",
        )
        self._refresh_api_disabled_banner()

        self.roster_container = ctk.CTkScrollableFrame(parent, height=480)
        self.roster_container.pack(fill="both", expand=True, padx=20, pady=(4, 12))

    def _refresh_api_disabled_banner(self):
        token = (load_toml_as_dict("cfg/login.toml").get("bs_api_token") or "").strip()
        if not token:
            self.api_disabled_banner.pack(anchor="w", padx=20, pady=(0, 6), fill="x")
            self.refresh_roster_btn.configure(state="disabled")
        else:
            self.api_disabled_banner.pack_forget()
            self.refresh_roster_btn.configure(state="normal")

    def _on_sort_changed(self, value: str):
        self._roster_sort = value
        self._render_roster_rows()

    def _on_refresh_roster(self):
        login_cfg = load_toml_as_dict("cfg/login.toml")
        tag = (login_cfg.get("player_tag") or "").strip()
        if not tag:
            self._set_roster_status("Set player tag in Settings tab first.", color="#e57373")
            return
        self._set_roster_status("Loading…", color="#9aa0a6")

        def _worker():
            try:
                brawlers = bs_api.get_client().get_brawlers(tag)
            except bs_api.BSApiDisabled:
                self.root.after(0, lambda: self._set_roster_status("API disabled — set token in Settings.", color="#e57373"))
                return
            except Exception as e:
                self.root.after(0, lambda: self._set_roster_status(f"error: {e!r}", color="#e57373"))
                return
            self.root.after(0, lambda: self._on_roster_loaded(brawlers))

        threading.Thread(target=_worker, name="roster-refresh", daemon=True).start()

    def _on_roster_loaded(self, brawlers: list):
        self._roster = brawlers or []
        self._set_roster_status(f"{len(self._roster)} brawlers loaded.", color="#7ed47a")
        self._render_roster_rows()

    def _set_roster_status(self, msg: str, color: str = "#9aa0a6"):
        self.roster_status_label.configure(text=msg, text_color=color)

    def _sorted_roster(self):
        if self._roster_sort == "name":
            return sorted(self._roster, key=lambda b: b.get("name", ""))
        if self._roster_sort == "winstreak":
            return sorted(self._roster, key=lambda b: -b.get("currentWinStreak", 0))
        return sorted(self._roster, key=lambda b: -b.get("trophies", 0))

    def _render_roster_rows(self):
        for child in self.roster_container.winfo_children():
            child.destroy()
        for brawler in self._sorted_roster():
            row = ctk.CTkFrame(self.roster_container)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=brawler.get("name", "?"), width=180, anchor="w").pack(side="left", padx=8, pady=6)
            ctk.CTkLabel(row, text=f"{brawler.get('trophies', 0)} 🏆", width=120, anchor="w").pack(side="left", padx=4)
            ctk.CTkLabel(row, text=f"WS {brawler.get('currentWinStreak', 0)}", width=80, anchor="w").pack(side="left", padx=4)
            ctk.CTkLabel(row, text=f"P{brawler.get('power', 0)} R{brawler.get('rank', 0)}", width=90, anchor="w").pack(side="left", padx=4)
            ctk.CTkButton(
                row, text="Add to queue ▶", width=140,
                command=lambda b=brawler: self._open_add_to_queue_dialog(b),
            ).pack(side="right", padx=8)

    def _open_add_to_queue_dialog(self, brawler: dict):
        top = ctk.CTkToplevel(self.root)
        top.title(f"Add {brawler.get('name', '?')} to queue")
        top.geometry("420x300")
        top.transient(self.root)
        top.grab_set()

        ctk.CTkLabel(top, text=f"Brawler: {brawler.get('name', '?')}", font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=16, pady=(16, 6))
        ctk.CTkLabel(top, text=f"Current trophies: {brawler.get('trophies', 0)} | Win streak: {brawler.get('currentWinStreak', 0)}").pack(anchor="w", padx=16)

        type_var = ctk.StringVar(value="trophies")
        type_row = ctk.CTkFrame(top, fg_color="transparent")
        type_row.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(type_row, text="Target type:", width=110, anchor="w").pack(side="left")
        ctk.CTkRadioButton(type_row, text="Trophies", variable=type_var, value="trophies").pack(side="left", padx=(0, 12))
        ctk.CTkRadioButton(type_row, text="Wins", variable=type_var, value="wins").pack(side="left")

        target_default = brawler.get("trophies", 0) + 100
        target_row = ctk.CTkFrame(top, fg_color="transparent")
        target_row.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(target_row, text="Target value:", width=110, anchor="w").pack(side="left")
        target_entry = ctk.CTkEntry(target_row, width=140)
        target_entry.insert(0, str(target_default))
        target_entry.pack(side="left")

        auto_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(top, text="Auto-pick this brawler when its turn comes", variable=auto_var).pack(anchor="w", padx=16, pady=(12, 4))

        button_row = ctk.CTkFrame(top, fg_color="transparent")
        button_row.pack(fill="x", padx=16, pady=(12, 16))

        def _confirm():
            try:
                target_value = int(target_entry.get().strip())
            except ValueError:
                target_value = target_default
            entry = app_state.QueueEntry(
                brawler_name=brawler.get("name", "?"),
                target_type=type_var.get(),
                target_value=target_value,
                current_trophies=int(brawler.get("trophies", 0)),
                current_wins=0,
                win_streak=int(brawler.get("currentWinStreak", 0)),
                automatically_pick=bool(auto_var.get()),
            )
            existing = app_state.load_queue()
            existing.append(entry)
            app_state.save_queue(existing)
            self._set_roster_status(f"Added {entry.brawler_name} to queue.", color="#7ed47a")
            self._refresh_queue_rows()
            top.destroy()

        ctk.CTkButton(button_row, text="Add", command=_confirm, width=120).pack(side="left", padx=(0, 8))
        ctk.CTkButton(button_row, text="Cancel", command=top.destroy, width=120).pack(side="left")

    def _refresh_queue_rows(self):
        if not hasattr(self, "queue_container"):
            return  # called before Queue tab built
        for child in self.queue_container.winfo_children():
            child.destroy()
        entries = app_state.load_queue()
        if not entries:
            ctk.CTkLabel(
                self.queue_container,
                text="Queue is empty. Add brawlers from the Setup tab.",
                text_color="#9aa0a6",
            ).pack(anchor="w", pady=8, padx=8)
            self.queue_status_label.configure(text="0 entries")
            return
        for idx, entry in enumerate(entries):
            row = ctk.CTkFrame(self.queue_container)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=f"#{idx + 1}", width=40).pack(side="left", padx=(8, 4), pady=6)
            ctk.CTkLabel(row, text=entry.brawler_name, width=170, anchor="w", font=("Segoe UI", 13, "bold")).pack(side="left")
            current = entry.current_trophies if entry.target_type == "trophies" else entry.current_wins
            ctk.CTkLabel(row, text=f"{current} → {entry.target_value} {entry.target_type}", width=220, anchor="w").pack(side="left", padx=8)
            ctk.CTkLabel(row, text=f"WS {entry.win_streak}", width=70, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text="auto" if entry.automatically_pick else "manual", width=70, anchor="w", text_color="#9aa0a6").pack(side="left")

            ctk.CTkButton(row, text="↑", width=36, command=lambda i=idx: self._queue_move(i, -1)).pack(side="right", padx=2)
            ctk.CTkButton(row, text="↓", width=36, command=lambda i=idx: self._queue_move(i, 1)).pack(side="right", padx=2)
            ctk.CTkButton(row, text="Remove", width=80, fg_color="#a04040",
                          command=lambda i=idx: self._queue_remove(i)).pack(side="right", padx=(8, 4))
        self.queue_status_label.configure(text=f"{len(entries)} entries")

    def _queue_move(self, idx: int, delta: int):
        entries = app_state.load_queue()
        new_idx = idx + delta
        if not (0 <= new_idx < len(entries)):
            return
        entries[idx], entries[new_idx] = entries[new_idx], entries[idx]
        app_state.save_queue(entries)
        self._refresh_queue_rows()

    def _queue_remove(self, idx: int):
        entries = app_state.load_queue()
        if 0 <= idx < len(entries):
            entries.pop(idx)
            app_state.save_queue(entries)
            self._refresh_queue_rows()

    def _on_clear_queue(self):
        # Simple inline confirmation via the status label — avoids spawning
        # an extra dialog. Two-step: first click flags pending, second click
        # within 5s commits the clear.
        import time as _time
        now = _time.time()
        if getattr(self, "_clear_pending_at", 0) and now - self._clear_pending_at < 5:
            app_state.save_queue([])
            self._clear_pending_at = 0
            self._refresh_queue_rows()
            self.queue_status_label.configure(text="Cleared.")
            return
        self._clear_pending_at = now
        self.queue_status_label.configure(text="Click 'Clear all' again within 5s to confirm.", text_color="#e0a93a")

    def _build_queue_tab(self, parent):
        ctk.CTkLabel(
            parent, text="Push queue",
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w", padx=20, pady=(20, 4))

        controls = ctk.CTkFrame(parent, fg_color="transparent")
        controls.pack(fill="x", padx=20, pady=(0, 8))
        ctk.CTkButton(controls, text="Refresh", width=100, command=self._refresh_queue_rows).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            controls, text="Clear all", width=100, fg_color="#a04040",
            command=self._on_clear_queue,
        ).pack(side="left")
        self.queue_status_label = ctk.CTkLabel(controls, text="", anchor="w", text_color="#9aa0a6")
        self.queue_status_label.pack(side="left", padx=12)

        self.queue_container = ctk.CTkScrollableFrame(parent, height=520)
        self.queue_container.pack(fill="both", expand=True, padx=20, pady=(4, 12))
        self._refresh_queue_rows()

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
