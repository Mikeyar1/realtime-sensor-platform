import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta
from tkcalendar import DateEntry
from typing import List

# Assuming LslMetadata is defined elsewhere and imported
# from your_module import LslMetadata


class MetadataQueryApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LSL Metadata Query")
        self.geometry("800x600")

        self.lsl_metadata_list = []  # This should be populated with LslMetadata instances

        self.create_widgets()

    def create_widgets(self) -> None:
        # Date/time input
        input_frame: ttk.LabelFrame = ttk.LabelFrame(self, text="Date/Time Selection")
        input_frame.pack(fill="x", padx=10, pady=5)

        now = datetime.now()
        start_default = now - timedelta(days=90)

        ttk.Label(input_frame, text="Start Date:").grid(row=0, column=0, padx=5, pady=5)
        self.start_date: DateEntry = DateEntry(input_frame, width=12)
        self.start_date.set_date(start_default)
        self.start_date.grid(row=0, column=1, padx=5, pady=5)
        ttk.Label(input_frame, text="Start Time (HH:MM:SS):").grid(row=0, column=2)
        self.start_time: ttk.Entry = ttk.Entry(input_frame, width=10)
        self.start_time.grid(row=0, column=3, padx=5, pady=5)
        self.start_time.insert(0, "00:00:00")  # Default to 00:00:00

        ttk.Label(input_frame, text="End Date:").grid(row=1, column=0, padx=5, pady=5)
        self.end_date: DateEntry = DateEntry(input_frame, width=12)
        self.end_date.set_date(now)
        self.end_date.grid(row=1, column=1, padx=5, pady=5)
        ttk.Label(input_frame, text="End Time (HH:MM:SS):").grid(row=1, column=2)
        self.end_time: ttk.Entry = ttk.Entry(input_frame, width=10)
        self.end_time.grid(row=1, column=3, padx=5, pady=5)
        self.end_time.insert(0, "00:00:00")  # Default to 00:00:00

        self.query_btn: ttk.Button = ttk.Button(input_frame, text="Query", command=self.query_metadata)
        self.query_btn.grid(row=2, column=0, columnspan=4, pady=10)

        # File picker for SQLite database
        file_picker_frame: ttk.Frame = ttk.Frame(self)
        file_picker_frame.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(file_picker_frame, text="SQLite Database File:").pack(side="left", padx=(0, 5))
        self.sqlite_file_var: tk.StringVar = tk.StringVar(value="No file selected")
        self.sqlite_file_label: ttk.Label = ttk.Label(file_picker_frame, textvariable=self.sqlite_file_var, width=60)
        self.sqlite_file_label.pack(side="left", padx=(0, 5))
        self.sqlite_file_btn: ttk.Button = ttk.Button(file_picker_frame, text="Browse...", command=self.browse_sqlite_file)
        self.sqlite_file_btn.pack(side="left")

        # Metadata table with checkboxes and horizontal scroll
        table_frame: ttk.LabelFrame = ttk.LabelFrame(self, text="Available LSL Metadata")
        table_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.table_canvas: tk.Canvas = tk.Canvas(table_frame)
        self.table_canvas.pack(side="left", fill="both", expand=True)
        self.v_scrollbar: ttk.Scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table_canvas.yview)
        self.v_scrollbar.pack(side="right", fill="y")
        self.h_scrollbar: ttk.Scrollbar = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table_canvas.xview)
        self.h_scrollbar.pack(side="bottom", fill="x")
        self.table_canvas.configure(yscrollcommand=self.v_scrollbar.set, xscrollcommand=self.h_scrollbar.set)
        self.table_inner: ttk.Frame = ttk.Frame(self.table_canvas)
        self.table_canvas.create_window((0, 0), window=self.table_inner, anchor="nw")
        self.table_inner.bind("<Configure>", lambda e: self.table_canvas.configure(scrollregion=self.table_canvas.bbox("all")))

        self.check_vars: List[tk.BooleanVar] = []

        # Playback controls
        controls_frame: ttk.Frame = ttk.Frame(self)
        controls_frame.pack(fill="x", padx=10, pady=5)
        self.play_btn: ttk.Button = ttk.Button(controls_frame, text="Play", command=self.start_playback)
        self.play_btn.pack(side="left", padx=5)
        self.stop_btn: ttk.Button = ttk.Button(controls_frame, text="Stop", command=self.stop_playback)
        self.stop_btn.pack(side="left", padx=5)

        # Timeline
        timeline_frame: ttk.LabelFrame = ttk.LabelFrame(self, text="Timeline")
        timeline_frame.pack(fill="x", padx=10, pady=5)
        self.timeline_canvas: tk.Canvas = tk.Canvas(timeline_frame, height=60, bg="white")
        self.timeline_canvas.pack(fill="x", expand=True)
        self.timeline_label: ttk.Label = ttk.Label(timeline_frame, text="")
        self.timeline_label.pack()

        # Summary table
        summary_frame: ttk.LabelFrame = ttk.LabelFrame(self, text="Summary Table")
        summary_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self.summary_tree: ttk.Treeview = ttk.Treeview(
            summary_frame,
            columns=("active", "published", "rate", "created", "ended"),
            show="headings"
        )
        self.summary_tree.heading("active", text="Outlet Active")
        self.summary_tree.heading("published", text="Total Records Published")
        self.summary_tree.heading("rate", text="Publishing Rate (rec/s)")
        self.summary_tree.heading("created", text="Outlet Creation Time")
        self.summary_tree.heading("ended", text="Outlet End Time")
        self.summary_tree.pack(fill="both", expand=True)

    def populate_metadata_table(self, desc_truncate: int = 40) -> None:
        for widget in self.table_inner.winfo_children():
            widget.destroy()
        self.check_vars.clear()

        headers: List[str] = [f.name for f in LslMetadata.__dataclass_fields__.values()]
        for col, header in enumerate(["Select"] + headers):
            ttk.Label(self.table_inner, text=header, borderwidth=1, relief="solid").grid(row=0, column=col, sticky="nsew")

        for row_idx, meta in enumerate(self.lsl_metadata_list, start=1):
            var: tk.BooleanVar = tk.BooleanVar()
            chk: ttk.Checkbutton = ttk.Checkbutton(self.table_inner, variable=var)
            chk.grid(row=row_idx, column=0)
            self.check_vars.append(var)
            for col_idx, field in enumerate(headers, start=1):
                val = getattr(meta, field)
                if field == "desc":
                    val = str(val)
                    if len(val) > desc_truncate:
                        val = val[:desc_truncate] + "..."
                ttk.Label(self.table_inner, text=str(val), borderwidth=1, relief="solid").grid(row=row_idx, column=col_idx, sticky="nsew")

    def query_metadata(self):
        # Implement the query logic here
        pass

    def browse_sqlite_file(self):
        # Implement the file browsing logic here
        pass

    def start_playback(self):
        # Implement the playback start logic here
        pass

    def stop_playback(self):
        # Implement the playback stop logic here
        pass


if __name__ == "__main__":
    app = MetadataQueryApp()
    app.mainloop()