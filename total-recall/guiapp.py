import sqlite3
import tkinter as tk
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

from tkcalendar import DateEntry

from config_mgr import config
from lsl_metadata import LslMetadata
from lsl_replay_publisher import LslReplayPublisher
from playback_feed_summary import PlaybackFeedSummary

sqlite_database_file = config["sqlite_database_file"]
current_working_directory_path = Path.cwd()
sqlite_database_file_full_path = current_working_directory_path.joinpath(sqlite_database_file)

start_date_days_delta_from_now = config.get("start_date_days_delta_from_now", 90)
timeline_update_interval_seconds = config.get("timeline_update_interval_seconds", 1.0)

sql_query_metadata = config["sql_query_metadata"]


class LslGuiApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("LSL Replay GUI")
        self.geometry("1200x800")

        self.selected_metadata: List[LslMetadata] = []
        self.lsl_metadata_list: List[LslMetadata] = []
        self.feed_summaries: List[PlaybackFeedSummary] = []
        self.playback_active: bool = False
        self.playback_start_utc: Optional[datetime] = None
        self.playback_end_utc: Optional[datetime] = None
        self.playback_current_utc: Optional[datetime] = None
        self.playback_start_local: Optional[datetime] = None
        self.playback_end_local: Optional[datetime] = None
        self.lsl_replay_publishers: List[LslReplayPublisher] = []

        self.sqlite_file_full_path: Optional[str] = sqlite_database_file_full_path

        self.create_widgets()
        self.disable_playback_controls()

    def create_widgets(self) -> None:
        # Date/time input
        input_frame: ttk.LabelFrame = ttk.LabelFrame(self, text="Date/Time Selection")
        input_frame.pack(fill="x", padx=10, pady=5)

        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone()
        start_default = now_local - timedelta(days=start_date_days_delta_from_now)

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
        self.end_date.set_date(now_local)
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
        self.sqlite_file_var: tk.StringVar = tk.StringVar(value=str(self.sqlite_file_full_path))
        self.sqlite_file_label: ttk.Label = ttk.Label(file_picker_frame, textvariable=self.sqlite_file_var, width=60)
        self.sqlite_file_label.pack(side="left", padx=(0, 5))
        self.sqlite_file_btn: ttk.Button = ttk.Button(
            file_picker_frame, text="Browse...", command=self.browse_sqlite_file
        )
        self.sqlite_file_btn.pack(side="left")

        # Metadata table with checkboxes and horizontal scroll
        table_frame: ttk.LabelFrame = ttk.LabelFrame(self, text="Available LSL Metadata")
        table_frame.pack(fill="both", expand=True, padx=10, pady=5)

        # Select all / none buttons
        select_btn_frame: ttk.Frame = ttk.Frame(table_frame)
        select_btn_frame.pack(fill="x", padx=0, pady=(0, 5))
        self.select_all_btn: ttk.Button = ttk.Button(
            select_btn_frame, text="Select All", command=self.select_all_checkboxes, state="disabled"
        )
        self.select_all_btn.pack(side="left", padx=(0, 5))
        self.select_none_btn: ttk.Button = ttk.Button(
            select_btn_frame, text="Select None", command=self.select_none_checkboxes, state="disabled"
        )
        self.select_none_btn.pack(side="left")

        self.table_canvas: tk.Canvas = tk.Canvas(table_frame)
        self.table_canvas.pack(side="left", fill="both", expand=True)
        self.v_scrollbar: ttk.Scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table_canvas.yview)
        self.v_scrollbar.pack(side="right", fill="y")
        self.h_scrollbar: ttk.Scrollbar = ttk.Scrollbar(
            table_frame, orient="horizontal", command=self.table_canvas.xview
        )
        self.h_scrollbar.pack(side="bottom", fill="x")
        self.table_canvas.configure(yscrollcommand=self.v_scrollbar.set, xscrollcommand=self.h_scrollbar.set)
        self.table_inner: ttk.Frame = ttk.Frame(self.table_canvas)
        self.table_canvas.create_window((0, 0), window=self.table_inner, anchor="nw")
        self.table_inner.bind(
            "<Configure>", lambda e: self.table_canvas.configure(scrollregion=self.table_canvas.bbox("all"))
        )

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

        # Add vertical and horizontal scrollbars for the summary table
        summary_tree_container = ttk.Frame(summary_frame)
        summary_tree_container.pack(fill="both", expand=True)

        self.summary_tree: ttk.Treeview = ttk.Treeview(
            summary_tree_container,
            columns=("id", "name", "active", "published", "rate", "created", "ended"),
            show="headings",
        )
        self.summary_tree.heading("id", text="ID", anchor="center")
        self.summary_tree.heading("name", text="Name", anchor="center")
        self.summary_tree.heading("active", text="Active", anchor="center")
        self.summary_tree.heading("published", text="Total Published", anchor="center")
        self.summary_tree.heading("rate", text="Rate (rec/s)", anchor="center")
        self.summary_tree.heading("created", text="Outlet Creation Time", anchor="center")
        self.summary_tree.heading("ended", text="Outlet End Time", anchor="center")

        self.summary_tree.column("id", width=40, anchor="center", stretch=False)
        self.summary_tree.column("name", width=180, anchor="center", stretch=True)
        self.summary_tree.column("active", width=40, anchor="center", stretch=False)
        self.summary_tree.column("published", width=85, anchor="center", stretch=True)
        self.summary_tree.column("rate", width=60, anchor="center", stretch=True)
        self.summary_tree.column("created", width=100, anchor="center", stretch=True)
        self.summary_tree.column("ended", width=100, anchor="center", stretch=True)

        yscroll = ttk.Scrollbar(summary_tree_container, orient="vertical", command=self.summary_tree.yview)
        xscroll = ttk.Scrollbar(summary_tree_container, orient="horizontal", command=self.summary_tree.xview)
        self.summary_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.summary_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        summary_tree_container.rowconfigure(0, weight=1)
        summary_tree_container.columnconfigure(0, weight=1)

    def select_all_checkboxes(self) -> None:
        for var in self.check_vars:
            var.set(True)

    def select_none_checkboxes(self) -> None:
        for var in self.check_vars:
            var.set(False)

    def browse_sqlite_file(self) -> None:
        file_path: str = filedialog.askopenfilename(
            title="Select SQLite Database File",
            filetypes=[("SQLite Database Files", "*.db *.sqlite *.sqlite3"), ("All Files", "*.*")],
        )
        if file_path:
            self.sqlite_file_full_path = file_path
            self.sqlite_file_var.set(file_path)
        else:
            pass
            # self.sqlite_file = None
            # self.sqlite_file_var.set("No file selected")

    def disable_playback_controls(self) -> None:
        self.play_btn["state"] = "disabled"
        self.stop_btn["state"] = "disabled"
        self.select_all_btn["state"] = "disabled"
        self.select_none_btn["state"] = "disabled"

    def enable_playback_controls(self) -> None:
        self.play_btn["state"] = "normal"
        self.stop_btn["state"] = "disabled"
        if self.lsl_metadata_list:
            self.select_all_btn["state"] = "normal"
            self.select_none_btn["state"] = "normal"
        else:
            self.select_all_btn["state"] = "disabled"
            self.select_none_btn["state"] = "disabled"

    def query_metadata(self) -> None:
        # Convert local to UTC
        try:
            start_dt: datetime = datetime.strptime(
                f"{self.start_date.get()} {self.start_time.get()}", "%m/%d/%y %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            end_dt: datetime = datetime.strptime(
                f"{self.end_date.get()} {self.end_time.get()}", "%m/%d/%y %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            start_utc: datetime = start_dt.astimezone(timezone.utc)
            end_utc: datetime = end_dt.astimezone(timezone.utc)
        except Exception as e:
            messagebox.showerror("Invalid Date/Time", str(e))
            return

        conn = sqlite3.connect(self.sqlite_file_full_path)
        start_date = start_utc
        end_date = end_utc
        cursor = conn.cursor()
        cursor.execute(sql_query_metadata, (start_date, end_date))
        rows = cursor.fetchall()
        conn.close()
        lsl_metadata_list = list[LslMetadata]()
        for row in rows:
            channel_format = row[10]
            try:
                channel_format_int = int(channel_format)
                channel_format_str = LslMetadata.get_channel_format(channel_format_int)
            except ValueError:
                channel_format_str = row[10]

            lsl_metadata = LslMetadata(
                lsl_metadata_id=row[0],
                datetime_utc=row[1],
                datetime_local=row[2],
                unix_timestamp_seconds=row[3],
                target_table_name=row[4],
                name=row[5],
                type=row[6],
                channels=row[7],
                sample_rate_hz=row[8],
                source_id=row[9],
                channel_format=channel_format_str,
                session_id=row[11],
                hostname=row[12],
                desc=row[13],
            )
            lsl_metadata_list.append(lsl_metadata)
            self.lsl_metadata_list = lsl_metadata_list
        self.populate_metadata_table()
        self.enable_playback_controls()
        self.playback_start_utc = start_utc
        self.playback_end_utc = end_utc
        self.playback_start_local = start_dt
        self.playback_end_local = end_dt
        self.update_timeline(0.0, start_utc, end_utc, start_utc)

    def populate_metadata_table(self, desc_truncate: int = 40) -> None:
        for widget in self.table_inner.winfo_children():
            widget.destroy()
        self.check_vars.clear()

        headers: List[str] = [f.name for f in LslMetadata.__dataclass_fields__.values()]
        for col, header in enumerate(["Select"] + headers):
            ttk.Label(self.table_inner, text=header, borderwidth=1, relief="solid").grid(
                row=0, column=col, sticky="nsew"
            )

        for row_idx, meta in enumerate(self.lsl_metadata_list, start=1):
            var: tk.BooleanVar = tk.BooleanVar()
            var.trace_add("write", lambda *args, idx=row_idx - 1: self.update_timeline_bounds_from_selection())
            chk: ttk.Checkbutton = ttk.Checkbutton(self.table_inner, variable=var)
            chk.grid(row=row_idx, column=0)
            self.check_vars.append(var)
            for col_idx, field in enumerate(headers, start=1):
                val = getattr(meta, field)
                if field == "desc":
                    val = str(val)
                    if len(val) > desc_truncate:
                        val = val[:desc_truncate] + "..."
                ttk.Label(self.table_inner, text=str(val), borderwidth=1, relief="solid").grid(
                    row=row_idx, column=col_idx, sticky="nsew"
                )

        # Enable select all/none buttons if table is populated and not during playback
        if self.lsl_metadata_list and not self.playback_active:
            self.select_all_btn["state"] = "normal"
            self.select_none_btn["state"] = "normal"
        else:
            self.select_all_btn["state"] = "disabled"
            self.select_none_btn["state"] = "disabled"

        # Update timeline bounds initially
        self.update_timeline_bounds_from_selection()

    def update_timeline_bounds_from_selection(self) -> None:
        # Find selected feeds
        lsl_metadata_list_selected = [meta for meta, var in zip(self.lsl_metadata_list, self.check_vars) if var.get()]

        start_dt: datetime = datetime.strptime(
            f"{self.start_date.get()} {self.start_time.get()}", "%m/%d/%y %H:%M:%S"
        ).replace(tzinfo=timezone.utc)

        start_utc: datetime = start_dt.astimezone(timezone.utc)

        self.update_lsl_replay_publishers(lsl_metadata_list_selected)

        if not lsl_metadata_list_selected:
            # If none selected, use the default start/end from the date/time pickers
            try:
                end_dt: datetime = datetime.strptime(
                    f"{self.end_date.get()} {self.end_time.get()}", "%m/%d/%y %H:%M:%S"
                ).replace(tzinfo=timezone.utc)
                end_utc: datetime = end_dt.astimezone(timezone.utc)
            except Exception:
                return
        else:
            # Use the min date from all the selected feeds
            start_utc = min(meta.datetime_utc for meta in lsl_metadata_list_selected)
            start_utc = datetime.fromisoformat(start_utc)
            try:
                end_utc = max(publisher.max_sample_datetime_utc for publisher in self.lsl_replay_publishers)
                # end_utc = datetime.fromisoformat(end_utc)
            except Exception:
                return
        # Set timeline to new bounds, with current at start
        self.update_timeline(0.0, start_utc, end_utc, start_utc)
        self.playback_start_utc = start_utc
        self.playback_end_utc = end_utc
        self.playback_current_utc = start_utc

    def update_lsl_replay_publishers(self, lsl_metadata_list_selected: list[LslMetadata]) -> None:
        # Add new publishers for selected feeds
        for lsl_metadata in lsl_metadata_list_selected:
            if self.lsl_replay_publishers and any(
                p.lsl_metadata.lsl_metadata_id == lsl_metadata.lsl_metadata_id for p in self.lsl_replay_publishers
            ):
                continue  # Skip if already created
            publisher = LslReplayPublisher(
                lsl_metadata=lsl_metadata,
                sqlite_file=self.sqlite_file_full_path,
                start_datetime=self.playback_start_utc,
                end_datetime=self.playback_end_utc,
                time_delta=datetime.now(tz=timezone.utc) - self.playback_start_utc,
            )
            publisher.fetch_sql_records()
            self.lsl_replay_publishers.append(publisher)
            print(f"Added publisher for feed ID {publisher.lsl_metadata.lsl_metadata_id}")
        # Remove publishers for feeds that are no longer selected
        for publisher in self.lsl_replay_publishers[:]:
            if not any(
                publisher.lsl_metadata.lsl_metadata_id == meta.lsl_metadata_id for meta in lsl_metadata_list_selected
            ):
                self.lsl_replay_publishers.remove(publisher)
                print(f"Removed publisher for feed ID {publisher.lsl_metadata.lsl_metadata_id}")

    def start_playback(self) -> None:
        self.selected_metadata = [meta for meta, var in zip(self.lsl_metadata_list, self.check_vars) if var.get()]
        if not self.selected_metadata:
            messagebox.showwarning("No Selection", "Please select at least one feed.")
            return
        self.disable_playback_controls()
        self.stop_btn["state"] = "normal"  # Enable stop button when playing
        self.playback_active = True
        self.select_all_btn["state"] = "disabled"
        self.select_none_btn["state"] = "disabled"
        self.feed_summaries = [PlaybackFeedSummary(meta) for meta in self.selected_metadata]
        now: datetime = datetime.now(timezone.utc)
        for fs in self.feed_summaries:
            fs.outlet_active = True
            fs.outlet_creation_time = now
            fs.records_published = 0
            fs.publishing_rate = 0.0
            fs.outlet_end_time = None
        self.playback_current_utc = self.playback_start_utc
        self.update_summary_table()
        self.update_timeline(0.0, self.playback_start_utc, self.playback_end_utc, self.playback_current_utc)
        self.after(int(timeline_update_interval_seconds * 1000), self.update_playback_progress)

        current_utc_time = datetime.now(timezone.utc)
        time_delta = current_utc_time - self.playback_start_utc

        for i, replay_publisher in enumerate(self.lsl_replay_publishers):
            replay_publisher.time_delta = time_delta
            # TRod: if the publisher was previously started, recreate it to that is can restarted
            try:
                replay_publisher.start()
            except RuntimeError:
                new_publisher = replay_publisher.create_restart()
                self.lsl_replay_publishers[i] = new_publisher
                new_publisher.start()

    def stop_playback(self) -> None:
        self.playback_active = False
        now: datetime = datetime.now(timezone.utc)
        for fs in self.feed_summaries:
            fs.outlet_active = False
            fs.outlet_end_time = now
        self.update_summary_table()
        self.enable_playback_controls()
        self.play_btn["state"] = "normal"  # Enable play button when stopped
        self.stop_btn["state"] = "disabled"  # Disable stop button when stopped
        self.update_timeline(0.0, self.playback_start_utc, self.playback_end_utc, self.playback_start_utc)

    def update_playback_progress(self) -> None:
        if not self.playback_active:
            return
        # Simulate playback progress
        total_seconds: float = (self.playback_end_utc - self.playback_start_utc).total_seconds()
        elapsed: float = (self.playback_current_utc - self.playback_start_utc).total_seconds()
        progress: float = min(elapsed / total_seconds, 1.0) if total_seconds > 0 else 0.0

        self.update_summary_table()
        self.update_timeline(progress, self.playback_start_utc, self.playback_end_utc, self.playback_current_utc)

        # Advance time
        if self.playback_current_utc < self.playback_end_utc:
            # TODO parameterize the time step
            self.playback_current_utc += timedelta(seconds=timeline_update_interval_seconds)
            self.after(int(timeline_update_interval_seconds * 1000), self.update_playback_progress)
        else:
            self.stop_playback()

    def update_timeline(self, progress: float, start_utc: datetime, end_utc: datetime, current_utc: datetime) -> None:
        self.timeline_canvas.delete("all")
        width: int = self.timeline_canvas.winfo_width() or 800
        height: int = self.timeline_canvas.winfo_height() or 60
        margin: int = 40
        bar_y: int = height // 2
        bar_start: int = margin
        bar_end: int = width - margin
        # Draw timeline bar
        self.timeline_canvas.create_line(bar_start, bar_y, bar_end, bar_y, width=6, fill="#cccccc")
        # Draw progress
        pos: int = bar_start + int((bar_end - bar_start) * progress)
        self.timeline_canvas.create_line(bar_start, bar_y, pos, bar_y, width=6, fill="#4caf50")
        # Draw markers
        self.timeline_canvas.create_oval(bar_start - 5, bar_y - 5, bar_start + 5, bar_y + 5, fill="blue")
        self.timeline_canvas.create_oval(bar_end - 5, bar_y - 5, bar_end + 5, bar_y + 5, fill="red")
        self.timeline_canvas.create_oval(pos - 7, bar_y - 7, pos + 7, bar_y + 7, fill="orange")
        # Draw labels
        self.timeline_canvas.create_text(bar_start, bar_y + 20, text="Start", anchor="w")
        self.timeline_canvas.create_text(bar_end, bar_y + 20, text="End", anchor="e")
        self.timeline_canvas.create_text(pos, bar_y - 20, text="Now", anchor="center")
        # Convert UTC times to local time for display
        start_local = start_utc.astimezone()
        end_local = end_utc.astimezone()
        current_local = current_utc.astimezone()
        # Show times in local time
        self.timeline_label.config(
            text=f"Start: {start_local.strftime('%Y-%m-%d %H:%M:%S %Z')} | "
            f"Current: {current_local.strftime('%Y-%m-%d %H:%M:%S %Z')} | "
            f"End: {end_local.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        )

    def update_summary_table(self) -> None:
        self.summary_tree.delete(*self.summary_tree.get_children())
        self.feed_summaries = self.get_summaries_from_publishers()
        for fs in self.feed_summaries:
            self.summary_tree.insert(
                "",
                "end",
                values=(
                    fs.lsl_metadata.lsl_metadata_id,
                    fs.lsl_metadata.name,
                    "Yes" if fs.outlet_active else "No",
                    fs.records_published,
                    fs.get_publishing_rate_str(),
                    fs.outlet_creation_time.strftime("%Y-%m-%d %H:%M:%S") if fs.outlet_creation_time else "------",
                    fs.outlet_end_time.strftime("%Y-%m-%d %H:%M:%S") if fs.outlet_end_time else "------",
                ),
                tags=(fs.lsl_metadata.name,),
            )

    # TODO move this code into lsl_replay_publisher.py
    def get_summaries_from_publishers(self) -> list[PlaybackFeedSummary]:
        playback_feed_summaries = list[PlaybackFeedSummary]()
        for publisher in self.lsl_replay_publishers:
            playback_feed_summary = PlaybackFeedSummary(publisher.lsl_metadata)
            playback_feed_summary.outlet_active = publisher.is_outlet_active()
            playback_feed_summary.records_published = publisher.current_replay_sample_index
            playback_feed_summary.publishing_rate = publisher.get_publish_rate_per_second()
            playback_feed_summary.outlet_creation_time = publisher.init_outlet_datetime
            playback_feed_summary.outlet_end_time = (
                publisher.max_sample_datetime_utc.astimezone() + publisher.time_delta
            )
            playback_feed_summaries.append(playback_feed_summary)
        return playback_feed_summaries


if __name__ == "__main__":
    app = LslGuiApp()
    app.mainloop()
