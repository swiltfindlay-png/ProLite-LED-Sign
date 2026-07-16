#!/usr/bin/env python3
"""
sign_gui.py
===========
A desktop GUI for the Pro-Lite/TruColor II sign, built on top of
prolite_sign.py (must be in the same folder - this file imports it directly
rather than duplicating the protocol logic).

Run with:
    python sign_gui.py

Needs: pyserial (pip install pyserial). Uses only the Python standard
library otherwise (tkinter ships with Python on Windows).
"""

import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from prolite_sign import (
    ProLiteSign, COLORS, FUNCTIONS, SIZES, GRAPHICS,
)

try:
    import serial.tools.list_ports
except ImportError:
    raise SystemExit("This tool needs pyserial. Install it with:\n    pip install pyserial")


PAGE_LETTERS = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
WEEKDAYS = [("Every day", "*"), ("Sun", "0"), ("Mon", "1"), ("Tue", "2"),
            ("Wed", "3"), ("Thu", "4"), ("Fri", "5"), ("Sat", "6")]
GRID_COLORS = {"B": "#111111", "R": "#e02020", "Y": "#e0c020", "G": "#20c040"}
GRID_CYCLE = ["B", "R", "Y", "G"]


def display_list(code_map):
    """['A - dim red', 'B - red', ...] for a code->name dict, sorted by letter."""
    return [f"{k} - {v.replace('_', ' ')}" for k, v in sorted(code_map.items())]


def code_from_display(s):
    """'A - dim red' -> 'A'"""
    return s.split(" - ", 1)[0]


class SignApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Pro-Lite / TruColor II Sign Controller")
        self.geometry("780x600")
        self.sign = None  # ProLiteSign instance once connected
        self._lock = threading.Lock()  # serialize access to self.sign across threads
        self._playlist_stop_event = threading.Event()
        self._playlist_running = False

        self._build_connection_bar()
        self._build_tabs()
        self._log("Not connected.")

    # ---------------------------------------------------------------
    # Connection bar
    # ---------------------------------------------------------------

    def _build_connection_bar(self):
        bar = ttk.Frame(self, padding=8)
        bar.pack(side="top", fill="x")

        ttk.Label(bar, text="Port:").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(bar, textvariable=self.port_var, width=12,
                                        state="readonly")
        self.port_combo.pack(side="left", padx=(2, 8))
        self._refresh_ports()

        ttk.Button(bar, text="Refresh", command=self._refresh_ports).pack(side="left")

        ttk.Label(bar, text="  Baud:").pack(side="left")
        self.baud_var = tk.StringVar(value="9600")
        ttk.Combobox(bar, textvariable=self.baud_var, width=7, state="readonly",
                     values=["300", "600", "1200", "2400", "4800", "9600"]
                     ).pack(side="left", padx=(2, 8))

        ttk.Label(bar, text="  Sign ID:").pack(side="left")
        self.id_var = tk.StringVar(value="1")
        ttk.Entry(bar, textvariable=self.id_var, width=4).pack(side="left", padx=(2, 8))

        self.bridge_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Arduino bridge", variable=self.bridge_var
                         ).pack(side="left", padx=(2, 8))

        self.connect_btn = ttk.Button(bar, text="Connect", command=self._toggle_connect)
        self.connect_btn.pack(side="left", padx=(8, 0))

        self.status_var = tk.StringVar(value="Disconnected")
        ttk.Label(bar, textvariable=self.status_var, foreground="#a00"
                  ).pack(side="left", padx=(12, 0))

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def _toggle_connect(self):
        if self.sign is None:
            self._connect()
        else:
            self._disconnect()

    def _connect(self):
        port = self.port_var.get()
        if not port:
            messagebox.showerror("No port", "Select a serial port first.")
            return
        try:
            sign_id = int(self.id_var.get())
            baud = int(self.baud_var.get())
        except ValueError:
            messagebox.showerror("Bad value", "Sign ID and baud must be numbers.")
            return

        def work():
            try:
                sign = ProLiteSign(port, sign_id=sign_id, baud=baud,
                                    bridge=self.bridge_var.get())
                reply = sign.wake()
            except Exception as e:
                msg = str(e)
                self.after(0, lambda msg=msg: self._connect_failed(msg))
                return
            self.after(0, lambda: self._connect_done(sign, reply))

        self.connect_btn.config(state="disabled")
        self.status_var.set("Connecting...")
        threading.Thread(target=work, daemon=True).start()

    def _connect_done(self, sign, reply):
        self.sign = sign
        self.connect_btn.config(text="Disconnect", state="normal")
        if reply:
            self.status_var.set(f"Connected - sign replied {reply!r}")
            self.status_var_color("#080")
        else:
            self.status_var.set("Connected, but sign did not reply to wake-up")
            self.status_var_color("#a60")
        self._log(f"Connected on {self.port_var.get()} @ {self.baud_var.get()} "
                   f"baud, ID {self.id_var.get()}. wake -> {reply!r}")

    def _connect_failed(self, err):
        self.connect_btn.config(state="normal")
        self.status_var.set("Connection failed")
        self.status_var_color("#a00")
        self._log(f"Connect failed: {err}")
        messagebox.showerror("Connection failed", err)

    def status_var_color(self, color):
        for child in self.winfo_children():
            pass  # status label styling kept simple - color set via label directly
        # (kept intentionally simple; ttk styling omitted for brevity)

    def _disconnect(self):
        self._playlist_stop_event.set()
        if self.sign:
            try:
                self.sign.close()
            except Exception:
                pass
        self.sign = None
        self.connect_btn.config(text="Connect")
        self.status_var.set("Disconnected")
        self._log("Disconnected.")

    # ---------------------------------------------------------------
    # Tabs
    # ---------------------------------------------------------------

    def _build_tabs(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._build_text_tab(nb)
        self._build_playlist_tab(nb)
        self._build_graphics_tab(nb)
        self._build_timers_tab(nb)
        self._build_trivia_tab(nb)
        self._build_console_tab(nb)

        # Log box shared by all tabs, docked at the bottom
        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="x", padx=8, pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=6, state="disabled", wrap="word")
        self.log_text.pack(fill="x")

    def _log(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"{time.strftime('%H:%M:%S')}  {msg}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _run_sign_call(self, fn, on_done=None):
        """Run a ProLiteSign call in a background thread so the UI never
        freezes while waiting on serial timeouts."""
        if self.sign is None:
            messagebox.showerror("Not connected", "Connect to the sign first.")
            return

        def work():
            with self._lock:
                try:
                    result = fn(self.sign)
                except Exception as e:
                    msg = str(e)
                    self.after(0, lambda msg=msg: self._log(f"Error: {msg}"))
                    return
            self.after(0, lambda: self._after_call(result, on_done))

        threading.Thread(target=work, daemon=True).start()

    def _after_call(self, result, on_done):
        self._log(f"-> {result!r}")
        if on_done:
            on_done(result)

    # -- Text / Pages tab -------------------------------------------------

    def _build_text_tab(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text="Text / Pages")

        row = 0
        ttk.Label(tab, text="Page:").grid(row=row, column=0, sticky="w")
        self.page_var = tk.StringVar(value="A")
        ttk.Combobox(tab, textvariable=self.page_var, values=PAGE_LETTERS,
                     width=4, state="readonly").grid(row=row, column=1, sticky="w")

        row += 1
        ttk.Label(tab, text="Message:").grid(row=row, column=0, sticky="nw", pady=(8, 0))
        self.msg_text = tk.Text(tab, height=4, width=50)
        self.msg_text.grid(row=row, column=1, columnspan=3, sticky="we", pady=(8, 0))

        row += 1
        ttk.Label(tab, text="Color:").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.color_var = tk.StringVar(value="")
        ttk.Combobox(tab, textvariable=self.color_var,
                     values=["(none)"] + display_list(COLORS), width=22,
                     state="readonly").grid(row=row, column=1, sticky="w", pady=(8, 0))

        ttk.Label(tab, text="Size:").grid(row=row, column=2, sticky="w", padx=(12, 0), pady=(8, 0))
        self.size_var = tk.StringVar(value="")
        ttk.Combobox(tab, textvariable=self.size_var,
                     values=["(none)"] + display_list(SIZES), width=22,
                     state="readonly").grid(row=row, column=3, sticky="w", pady=(8, 0))

        row += 1
        ttk.Label(tab, text="Entrance effect:").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.func_var = tk.StringVar(value="")
        ttk.Combobox(tab, textvariable=self.func_var,
                     values=["(none)"] + display_list(FUNCTIONS), width=28,
                     state="readonly").grid(row=row, column=1, sticky="w", pady=(8, 0))

        ttk.Label(tab, text="Exit effect:").grid(row=row, column=2, sticky="w", padx=(12, 0), pady=(8, 0))
        self.tail_func_var = tk.StringVar(value="")
        ttk.Combobox(tab, textvariable=self.tail_func_var,
                     values=["(none)"] + display_list(FUNCTIONS), width=28,
                     state="readonly").grid(row=row, column=3, sticky="w", pady=(8, 0))

        row += 1
        ttk.Label(tab, text="Embed graphic:").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.graphic_var = tk.StringVar(value="")
        ttk.Combobox(tab, textvariable=self.graphic_var,
                     values=["(none)"] + display_list(GRAPHICS), width=22,
                     state="readonly").grid(row=row, column=1, sticky="w", pady=(8, 0))

        row += 1
        btns = ttk.Frame(tab)
        btns.grid(row=row, column=0, columnspan=4, sticky="w", pady=(14, 0))
        ttk.Button(btns, text="Send to page", command=self._send_text).pack(side="left")
        ttk.Button(btns, text="Send + Run now", command=lambda: self._send_text(run=True)
                   ).pack(side="left", padx=6)
        ttk.Button(btns, text="Run page", command=self._run_page).pack(side="left", padx=6)
        ttk.Button(btns, text="Delete page", command=self._delete_page).pack(side="left", padx=6)

        row += 1
        ttk.Separator(tab).grid(row=row, column=0, columnspan=4, sticky="we", pady=14)

        row += 1
        danger = ttk.Frame(tab)
        danger.grid(row=row, column=0, columnspan=4, sticky="w")
        ttk.Button(danger, text="Delete ALL pages", command=self._delete_all_pages
                   ).pack(side="left")
        ttk.Button(danger, text="Delete EVERYTHING (pages+timers+graphics)",
                   command=self._delete_all).pack(side="left", padx=6)
        ttk.Button(danger, text="Set sign clock to now", command=self._set_clock
                   ).pack(side="left", padx=6)
        ttk.Button(danger, text="Show time on display", command=self._show_clock
                   ).pack(side="left", padx=6)
        ttk.Button(danger, text="Reset sign", command=self._reset_sign
                   ).pack(side="left", padx=6)

        for c in range(4):
            tab.columnconfigure(c, weight=1)

    def _picked(self, var):
        v = var.get()
        if not v or v == "(none)":
            return None
        return code_from_display(v)

    def _send_text(self, run=False):
        page = self.page_var.get()
        text = self.msg_text.get("1.0", "end").rstrip("\n")
        color = self._picked(self.color_var)
        size = self._picked(self.size_var)
        function = self._picked(self.func_var)
        tail_function = self._picked(self.tail_func_var)
        graphic = self._picked(self.graphic_var)

        def call(sign):
            r = sign.set_page(page, text, color=color, size=size, function=function,
                               tail_function=tail_function, graphic=graphic)
            if run:
                r = sign.run_page(page)
            return r

        self._run_sign_call(call)

    def _run_page(self):
        page = self.page_var.get()
        self._run_sign_call(lambda sign: sign.run_page(page))

    def _delete_page(self):
        page = self.page_var.get()
        self._run_sign_call(lambda sign: sign.delete_page(page))

    def _delete_all_pages(self):
        if messagebox.askyesno("Confirm", "Delete ALL pages?"):
            self._run_sign_call(lambda sign: sign.delete_all_pages())

    def _delete_all(self):
        if messagebox.askyesno("Confirm",
                                "Delete ALL pages, timers, and restore default "
                                "graphics? This cannot be undone."):
            self._run_sign_call(lambda sign: sign.delete_all())

    def _set_clock(self):
        self._run_sign_call(lambda sign: sign.set_clock() or "clock set")

    def _show_clock(self):
        """Uses the <FT> TIME/DATE function to make the sign display its own
        internal clock - the only way to actually see what time it thinks it
        is, since the protocol has no way to read the clock back over serial."""
        page = self.page_var.get()

        def call(sign):
            sign.set_page(page, "", function="time_and_date")
            return sign.run_page(page)

        self._run_sign_call(call)
        self._log(f"Showing sign's internal clock on page {page} - check the physical display.")

    def _reset_sign(self):
        if messagebox.askyesno(
                "Confirm",
                "This sends <RST>, which is UNVERIFIED for this sign - one third-"
                "party source reports it resets the unit, but it's not in the "
                "primary protocol docs. If your sign doesn't recognize it, it will "
                "instead show the literal text \"<RST>\" on page A (fixable by "
                "deleting/overwriting page A afterward). Continue?"):
            self._run_sign_call(lambda sign: sign.reset())

    # -- Playlist tab (run pages back to back from the PC) -----------------

    def _build_playlist_tab(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text="Playlist")

        note = ("Runs pages back to back by issuing Run Page commands from your PC "
                 "in sequence - separate from the sign's own built-in Timers, and "
                 "doesn't need the clock set. Stops if you disconnect or click Stop.")
        ttk.Label(tab, text=note, wraplength=700, justify="left").pack(anchor="w")

        add_row = ttk.Frame(tab)
        add_row.pack(anchor="w", pady=(10, 4))
        ttk.Label(add_row, text="Item:").pack(side="left")
        self.playlist_add_var = tk.StringVar(value="A")
        ttk.Combobox(add_row, textvariable=self.playlist_add_var,
                     values=PAGE_LETTERS + ["TIME"],
                     width=6, state="readonly").pack(side="left", padx=(4, 8))
        ttk.Button(add_row, text="Add to sequence", command=self._playlist_add
                   ).pack(side="left")
        ttk.Button(add_row, text="Remove selected", command=self._playlist_remove
                   ).pack(side="left", padx=6)
        ttk.Button(add_row, text="Clear", command=self._playlist_clear
                   ).pack(side="left")

        time_row = ttk.Frame(tab)
        time_row.pack(anchor="w", pady=(0, 4))
        ttk.Label(time_row, text="'TIME' uses page:").pack(side="left")
        self.playlist_time_page_var = tk.StringVar(value="Z")
        ttk.Combobox(time_row, textvariable=self.playlist_time_page_var,
                     values=PAGE_LETTERS, width=4, state="readonly"
                     ).pack(side="left", padx=(4, 8))
        ttk.Label(time_row, text="(pick a page you're not using for anything else - "
                                  "it gets overwritten each time TIME runs)",
                  foreground="#555").pack(side="left")

        self.playlist_listbox = tk.Listbox(tab, height=8, width=10)
        self.playlist_listbox.pack(anchor="w", pady=(4, 8))

        opts_row = ttk.Frame(tab)
        opts_row.pack(anchor="w", pady=(4, 0))
        ttk.Label(opts_row, text="Seconds per page:").pack(side="left")
        self.playlist_dwell_var = tk.StringVar(value="5")
        ttk.Entry(opts_row, textvariable=self.playlist_dwell_var, width=6
                  ).pack(side="left", padx=(4, 16))
        self.playlist_loop_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts_row, text="Loop continuously", variable=self.playlist_loop_var
                         ).pack(side="left")

        btn_row = ttk.Frame(tab)
        btn_row.pack(anchor="w", pady=(12, 0))
        self.playlist_start_btn = ttk.Button(btn_row, text="Start", command=self._playlist_start)
        self.playlist_start_btn.pack(side="left")
        self.playlist_stop_btn = ttk.Button(btn_row, text="Stop", command=self._playlist_stop,
                                             state="disabled")
        self.playlist_stop_btn.pack(side="left", padx=6)

        self.playlist_status_var = tk.StringVar(value="Not running")
        ttk.Label(tab, textvariable=self.playlist_status_var).pack(anchor="w", pady=(10, 0))

    def _playlist_add(self):
        self.playlist_listbox.insert("end", self.playlist_add_var.get())

    def _playlist_remove(self):
        sel = self.playlist_listbox.curselection()
        if sel:
            self.playlist_listbox.delete(sel[0])

    def _playlist_clear(self):
        self.playlist_listbox.delete(0, "end")

    def _playlist_start(self):
        if self.sign is None:
            messagebox.showerror("Not connected", "Connect to the sign first.")
            return
        pages = list(self.playlist_listbox.get(0, "end"))
        if not pages:
            messagebox.showerror("Empty sequence", "Add at least one item first.")
            return
        try:
            dwell = float(self.playlist_dwell_var.get())
            assert dwell > 0
        except (ValueError, AssertionError):
            messagebox.showerror("Bad value", "Seconds per page must be a positive number.")
            return
        loop = self.playlist_loop_var.get()
        time_page = self.playlist_time_page_var.get()

        self._playlist_stop_event.clear()
        self._playlist_running = True
        self.playlist_start_btn.config(state="disabled")
        self.playlist_stop_btn.config(state="normal")
        self.playlist_status_var.set("Running...")

        threading.Thread(target=self._playlist_worker, args=(pages, dwell, loop, time_page),
                          daemon=True).start()

    def _playlist_worker(self, pages, dwell, loop, time_page):
        first_pass = True
        while first_pass or loop:
            first_pass = False
            for item in pages:
                if self._playlist_stop_event.is_set() or self.sign is None:
                    self.after(0, self._playlist_finished)
                    return
                with self._lock:
                    try:
                        if item == "TIME":
                            self.sign.set_page(time_page, "", function="time_and_date")
                            reply = self.sign.run_page(time_page)
                        else:
                            reply = self.sign.run_page(item)
                    except Exception as e:
                        self.after(0, lambda e=e: self._log(f"Playlist error: {e}"))
                        self.after(0, self._playlist_finished)
                        return
                self.after(0, lambda p=item, r=reply: self._log(f"Playlist: showed {p} -> {r!r}"))
                self.after(0, lambda p=item: self.playlist_status_var.set(f"Showing {p}"))
                # sleep in small increments so Stop feels responsive
                waited = 0.0
                while waited < dwell:
                    if self._playlist_stop_event.is_set():
                        self.after(0, self._playlist_finished)
                        return
                    time.sleep(min(0.2, dwell - waited))
                    waited += 0.2
        self.after(0, self._playlist_finished)

    def _playlist_stop(self):
        self._playlist_stop_event.set()

    def _playlist_finished(self):
        self._playlist_running = False
        self.playlist_start_btn.config(state="normal")
        self.playlist_stop_btn.config(state="disabled")
        self.playlist_status_var.set("Stopped")

    # -- Graphics tab -------------------------------------------------

    def _build_graphics_tab(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text="Graphics Editor")

        top = ttk.Frame(tab)
        top.pack(fill="x")
        ttk.Label(top, text="Reprogram block:").pack(side="left")
        self.grid_block_var = tk.StringVar(value="A")
        ttk.Combobox(top, textvariable=self.grid_block_var, values=PAGE_LETTERS,
                     width=4, state="readonly").pack(side="left", padx=(4, 12))
        ttk.Label(top, text="Click a cell to cycle Black -> Red -> Yellow -> Green").pack(side="left")

        # 18 wide x 7 tall grid of clickable cells
        self.grid_state = [["B"] * 18 for _ in range(7)]
        canvas_frame = ttk.Frame(tab)
        canvas_frame.pack(pady=10)
        cell = 22
        self.grid_canvas = tk.Canvas(canvas_frame, width=18 * cell, height=7 * cell,
                                      bg="#333333", highlightthickness=1,
                                      highlightbackground="#888")
        self.grid_canvas.pack()
        self._cell_size = cell
        self._grid_rects = [[None] * 18 for _ in range(7)]
        for r in range(7):
            for c in range(18):
                x0, y0 = c * cell, r * cell
                rect = self.grid_canvas.create_rectangle(
                    x0 + 1, y0 + 1, x0 + cell - 1, y0 + cell - 1,
                    fill=GRID_COLORS["B"], outline="#555")
                self._grid_rects[r][c] = rect
                self.grid_canvas.tag_bind(
                    rect, "<Button-1>",
                    lambda e, r=r, c=c: self._cycle_cell(r, c))

        btns = ttk.Frame(tab)
        btns.pack(pady=6)
        ttk.Button(btns, text="Clear grid", command=self._clear_grid).pack(side="left")
        ttk.Button(btns, text="Upload to sign", command=self._upload_graphic
                   ).pack(side="left", padx=6)
        ttk.Button(btns, text="Delete this block (restore default)",
                   command=self._delete_graphic).pack(side="left", padx=6)
        ttk.Button(btns, text="Delete ALL graphics (restore defaults)",
                   command=self._delete_all_graphics).pack(side="left", padx=6)

        ref = ttk.LabelFrame(tab, text="Pre-made graphics you can embed in text "
                                        "(Text/Pages tab -> 'Embed graphic')")
        ref.pack(fill="x", pady=(10, 0))
        names = ", ".join(display_list(GRAPHICS))
        ttk.Label(ref, text=names, wraplength=700, justify="left").pack(padx=6, pady=6)

    def _cycle_cell(self, r, c):
        cur = self.grid_state[r][c]
        nxt = GRID_CYCLE[(GRID_CYCLE.index(cur) + 1) % len(GRID_CYCLE)]
        self.grid_state[r][c] = nxt
        self.grid_canvas.itemconfig(self._grid_rects[r][c], fill=GRID_COLORS[nxt])

    def _clear_grid(self):
        for r in range(7):
            for c in range(18):
                self.grid_state[r][c] = "B"
                self.grid_canvas.itemconfig(self._grid_rects[r][c], fill=GRID_COLORS["B"])

    def _upload_graphic(self):
        block = self.grid_block_var.get()
        rows = ["".join(row) for row in self.grid_state]
        self._run_sign_call(lambda sign: sign.set_graphic(block, rows))

    def _delete_graphic(self):
        block = self.grid_block_var.get()
        self._run_sign_call(lambda sign: sign.delete_graphic(block))

    def _delete_all_graphics(self):
        if messagebox.askyesno("Confirm", "Restore ALL graphic blocks to defaults?"):
            self._run_sign_call(lambda sign: sign.delete_all_graphics())

    # -- Timers tab -----------------------------------------------------

    def _build_timers_tab(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text="Timers")

        ttk.Label(tab, text="Timer letter (A-J):").grid(row=0, column=0, sticky="w")
        self.timer_letter_var = tk.StringVar(value="A")
        ttk.Combobox(tab, textvariable=self.timer_letter_var, values=list("ABCDEFGHIJ"),
                     width=4, state="readonly").grid(row=0, column=1, sticky="w")

        ttk.Label(tab, text="Day:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.timer_day_var = tk.StringVar(value="Every day")
        ttk.Combobox(tab, textvariable=self.timer_day_var,
                     values=[d[0] for d in WEEKDAYS], width=10,
                     state="readonly").grid(row=1, column=1, sticky="w", pady=(8, 0))

        ttk.Label(tab, text="Hour (00-23 or **):").grid(row=1, column=2, sticky="w", padx=(12, 0), pady=(8, 0))
        self.timer_hour_var = tk.StringVar(value="**")
        ttk.Entry(tab, textvariable=self.timer_hour_var, width=6
                  ).grid(row=1, column=3, sticky="w", pady=(8, 0))

        ttk.Label(tab, text="Minute (00-59 or **):").grid(row=1, column=4, sticky="w", padx=(12, 0), pady=(8, 0))
        self.timer_min_var = tk.StringVar(value="00")
        ttk.Entry(tab, textvariable=self.timer_min_var, width=6
                  ).grid(row=1, column=5, sticky="w", pady=(8, 0))

        ttk.Label(tab, text="Page sequence (e.g. ABC or AAD):").grid(
            row=2, column=0, sticky="w", pady=(8, 0))
        self.timer_pages_var = tk.StringVar(value="A")
        ttk.Entry(tab, textvariable=self.timer_pages_var, width=34
                  ).grid(row=2, column=1, columnspan=3, sticky="w", pady=(8, 0))

        btns = ttk.Frame(tab)
        btns.grid(row=3, column=0, columnspan=6, sticky="w", pady=(14, 0))
        ttk.Button(btns, text="Set timer", command=self._set_timer).pack(side="left")
        ttk.Button(btns, text="Delete this timer", command=self._delete_timer
                   ).pack(side="left", padx=6)
        ttk.Button(btns, text="Delete ALL timers", command=self._delete_all_timers
                   ).pack(side="left", padx=6)

        note = ("Timers are checked once a minute and require the sign's clock to be "
                "set (Text/Pages tab -> 'Set sign clock to now') to work correctly.")
        ttk.Label(tab, text=note, wraplength=700, foreground="#555"
                  ).grid(row=4, column=0, columnspan=6, sticky="w", pady=(16, 0))

    def _set_timer(self):
        letter = self.timer_letter_var.get()
        day_label = self.timer_day_var.get()
        weekday_code = next(code for label, code in WEEKDAYS if label == day_label)
        hour = self.timer_hour_var.get().strip()
        minute = self.timer_min_var.get().strip()
        pages = self.timer_pages_var.get().strip().upper()
        if not pages:
            messagebox.showerror("Missing pages", "Enter at least one page letter.")
            return
        self._run_sign_call(lambda sign: sign.set_timer(letter, weekday_code, hour, minute, pages))

    def _delete_timer(self):
        letter = self.timer_letter_var.get()
        self._run_sign_call(lambda sign: sign.delete_timer(letter))

    def _delete_all_timers(self):
        if messagebox.askyesno("Confirm", "Delete ALL timers?"):
            self._run_sign_call(lambda sign: sign.delete_all_timers())

    # -- Trivia tab -------------------------------------------------------

    def _build_trivia_tab(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text="Trivia")

        note = ("Trivia cycles: question, your message, answer, your message, next "
                "question... It uses a separate 16K memory bank from your pages. "
                "Enter one Q&A pair per pair of lines below (question line, then "
                "answer line).")
        ttk.Label(tab, text=note, wraplength=700, justify="left").pack(anchor="w")

        ttk.Label(tab, text="Question:").pack(anchor="w", pady=(10, 0))
        self.trivia_q_text = tk.Text(tab, height=8, width=70)
        self.trivia_q_text.pack(fill="x")
        ttk.Label(tab, text="Matching answer (same number of lines, in the same order):"
                  ).pack(anchor="w", pady=(8, 0))
        self.trivia_a_text = tk.Text(tab, height=8, width=70)
        self.trivia_a_text.pack(fill="x")

        btns = ttk.Frame(tab)
        btns.pack(anchor="w", pady=(10, 0))
        ttk.Button(btns, text="Upload trivia", command=self._upload_trivia).pack(side="left")
        ttk.Button(btns, text="Clear trivia (frees memory)", command=self._clear_trivia
                   ).pack(side="left", padx=6)

    def _upload_trivia(self):
        questions = [l for l in self.trivia_q_text.get("1.0", "end").splitlines() if l.strip()]
        answers = [l for l in self.trivia_a_text.get("1.0", "end").splitlines() if l.strip()]
        if not questions or len(questions) != len(answers):
            messagebox.showerror("Mismatch",
                                  "Enter the same number of question and answer lines.")
            return
        pairs = list(zip(questions, answers))
        self._run_sign_call(lambda sign: sign.set_trivia(pairs))

    def _clear_trivia(self):
        self._run_sign_call(lambda sign: sign.clear_trivia())

    # -- Raw console tab ---------------------------------------------------

    def _build_console_tab(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text="Raw Console")

        ttk.Label(tab, text="Raw command body (sent after <IDxx> automatically):"
                  ).pack(anchor="w")
        self.raw_var = tk.StringVar()
        entry = ttk.Entry(tab, textvariable=self.raw_var, width=70)
        entry.pack(fill="x", pady=(4, 8))
        entry.bind("<Return>", lambda e: self._send_raw())
        ttk.Button(tab, text="Send", command=self._send_raw).pack(anchor="w")

        example = ("Examples: <PA>Hello  |  <RPA>  |  <DP*>  |  <FX>  (halts scrolling "
                    "with no text)")
        ttk.Label(tab, text=example, foreground="#555").pack(anchor="w", pady=(10, 0))

    def _send_raw(self):
        body = self.raw_var.get()
        if not body:
            return
        self._run_sign_call(lambda sign: sign.send(body))


if __name__ == "__main__":
    app = SignApp()
    app.mainloop()
