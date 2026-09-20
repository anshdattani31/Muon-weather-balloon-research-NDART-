"""Tkinter settings editor: settings are saved separately from program code."""

import json
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import FIELDS, defaults, load, save, validate
from .engine import Engine


class App:
    def __init__(self, root):
        self.root = root
        root.title("N-Dart • Configurable acquisition and telemetry")
        root.geometry("1050x830")
        root.minsize(850, 620)
        self.messages = queue.Queue(maxsize=2000)
        self.engine = self.worker = None
        self.variables = {}
        self.closing = False
        self.current_file = None
        self.settings_widgets = []
        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="N-Dart configuration", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Choose settings, save a profile, then start. Settings apply to the next run.").pack(anchor="w", pady=(2, 8))
        ttk.Label(outer, text="Hardware note: Serial JSONL / Serial bridge require compatible device firmware. Simulator fields are placeholders.", wraplength=980).pack(anchor="w", pady=(0, 10))
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 8))
        for title, command in (("Load profile…", self.load_profile), ("Save profile…", self.save_profile), ("Restore defaults", self.restore)):
            button = ttk.Button(toolbar, text=title, command=command)
            button.pack(side="left", padx=(0, 6))
            self.settings_widgets.append(button)
        self.start_button = ttk.Button(toolbar, text="Start", command=self.start)
        self.start_button.pack(side="right", padx=(6, 0))
        self.stop_button = ttk.Button(toolbar, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="right")
        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True)
        for group in dict.fromkeys(spec[0] for spec in FIELDS.values()):
            host = ttk.Frame(tabs)
            tabs.add(host, text=group)
            canvas = tk.Canvas(host, highlightthickness=0)
            scrollbar = ttk.Scrollbar(host, orient="vertical", command=canvas.yview)
            scrollbar.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            canvas.configure(yscrollcommand=scrollbar.set)
            frame = ttk.Frame(canvas, padding=12)
            item = canvas.create_window((0, 0), window=frame, anchor="nw")
            frame.bind("<Configure>", lambda event, c=canvas: c.configure(scrollregion=c.bbox("all")))
            canvas.bind("<Configure>", lambda event, c=canvas, i=item: c.itemconfigure(i, width=event.width))
            frame.columnconfigure(1, weight=1)
            for row, (key, (_, label, default, choices)) in enumerate((k, v) for k, v in FIELDS.items() if v[0] == group):
                ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=5)
                variable = tk.StringVar(value=str(default))
                self.variables[key] = variable
                if choices is not None:
                    widget = ttk.Combobox(frame, textvariable=variable, values=[str(v) for v in choices], state="readonly", width=38)
                else:
                    widget = ttk.Entry(frame, textvariable=variable, width=40)
                widget.grid(row=row, column=1, sticky="ew", pady=5)
                self.settings_widgets.append(widget)
                if key in ("output_dir", "replay_file"):
                    button = ttk.Button(frame, text="Browse…", command=lambda k=key: self.browse(k))
                    button.grid(row=row, column=2, padx=(6, 0))
                    self.settings_widgets.append(button)
        advanced = ttk.Frame(tabs, padding=12)
        tabs.add(advanced, text="Field mapping")
        ttk.Label(advanced, text="Map our four common fields to top-level keys in your device JSON.\nAll additional fields are preserved in raw. Simulator ignores this mapping.\nCoincidence must already be reported as true/false; software does not calculate physical coincidence.", wraplength=880).pack(anchor="w", pady=(0, 10))
        self.mapping = tk.Text(advanced, height=10, width=70, font=("Consolas", 11))
        self.mapping.pack(fill="both", expand=True)
        self.mapping.insert("1.0", json.dumps(defaults()["field_mapping"], indent=2))
        self.settings_widgets.append(self.mapping)
        self.status = tk.StringVar(value="Ready — default profile uses simulated detectors and radio")
        ttk.Label(outer, textvariable=self.status, wraplength=980).pack(fill="x", pady=8)
        log_frame = ttk.Frame(outer)
        log_frame.pack(fill="x")
        self.log = tk.Text(log_frame, height=7, state="disabled", font=("Consolas", 9), wrap="word")
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

    def browse(self, key):
        path = filedialog.askdirectory(parent=self.root) if key == "output_dir" else filedialog.askopenfilename(parent=self.root, filetypes=[("JSON Lines", "*.jsonl"), ("All files", "*.*")])
        if path:
            self.variables[key].set(path)

    def values(self):
        values = {}
        for key, (_, label, default, _) in FIELDS.items():
            value = self.variables[key].get()
            try:
                if type(default) is bool:
                    values[key] = {"True": True, "False": False}[value]
                else:
                    values[key] = type(default)(value)
            except (ValueError, KeyError) as exc:
                raise ValueError(f"{label}: enter a valid {type(default).__name__}") from exc
        values["field_mapping"] = json.loads(self.mapping.get("1.0", "end"))
        return validate(values)

    def apply(self, config):
        for key in FIELDS:
            self.variables[key].set(str(config[key]))
        self.mapping.delete("1.0", "end")
        self.mapping.insert("1.0", json.dumps(config["field_mapping"], indent=2))

    def restore(self):
        self.apply(defaults())
        self.current_file = None

    def load_profile(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("N-Dart profile", "*.json")])
        if path:
            try:
                self.apply(load(path))
                self.current_file = path
                self.append_log(f"Loaded {path}")
            except (OSError, ValueError) as exc:
                messagebox.showerror("Profile could not be loaded", str(exc), parent=self.root)

    def save_profile(self):
        try:
            config = self.values()
            path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json", initialfile="ndart-profile.json", filetypes=[("N-Dart profile", "*.json")])
            if path:
                save(path, config)
                self.current_file = path
                self.append_log(f"Saved {path}")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Profile could not be saved", str(exc), parent=self.root)

    def emit(self, event):
        try:
            self.messages.put_nowait(event)
        except queue.Full:
            # Full diagnostics also remain in system.jsonl; never block acquisition on UI.
            if event["kind"] == "finished":
                self.messages.get_nowait()
                self.messages.put_nowait(event)

    def start(self):
        if self.closing or (self.worker and self.worker.is_alive()):
            return
        try:
            self.engine = Engine(self.values(), self.emit)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Check settings", str(exc), parent=self.root)
            return
        self.set_running(True)
        self.status.set("Starting…")
        self.worker = threading.Thread(target=self.engine.run, name="ndart-acquisition", daemon=False)
        self.worker.start()

    def set_running(self, running):
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        for widget in self.settings_widgets:
            state = "disabled" if running else ("readonly" if isinstance(widget, ttk.Combobox) else "normal")
            widget.configure(state=state)

    def stop(self):
        if self.engine:
            self.engine.stop()
            self.stop_button.configure(state="disabled")
            self.status.set("Stopping and closing logs…")

    def append_log(self, message):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 500:
            self.log.delete("1.0", "100.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def poll(self):
        for _ in range(200):
            try:
                event = self.messages.get_nowait()
            except queue.Empty:
                break
            if event["kind"] == "status":
                self.status.set("Collected: {collected}   Ground records: {received}   Fragments sent: {sent_fragments}   ACKs: {acked}\nPending: {pending}   Retries: {retries}   Dropped transmissions: {dropped_transmissions}   Errors: {errors}\nLast ground record: {last_received}".format(**event))
            elif event["kind"] == "finished":
                self.set_running(False)
                self.status.set(("Stopped with error: " + event["failure"]) if event["failure"] else f"Stopped — collected {event['collected']}, received {event['received']}, pending {event['pending']}")
            else:
                self.append_log(event.get("message", ""))
        if self.closing and (not self.worker or not self.worker.is_alive()):
            self.root.destroy()
            return
        self.root.after(100, self.poll)

    def close(self):
        self.closing = True
        self.stop()


def launch():
    root = tk.Tk()
    App(root)
    root.mainloop()
