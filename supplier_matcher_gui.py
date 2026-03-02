#!/usr/bin/env python3
"""
Supplier Name Matching Tool - GUI

A tkinter GUI wrapper around the supplier_matcher backend.
Provides file selection, column mapping, matching execution, and results preview.
"""

import math
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Ensure the script's directory is on the import path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import MatchResult, MatchMethod
import config
from csv_handler import (
    load_csv,
    extract_supplier_records,
    extract_lookup_entries,
    write_output_csv,
)
from exact_matcher import build_lookup_index, exact_match
from llm_matcher import create_client, llm_match_batch


# ---------------------------------------------------------------------------
# Color / style constants
# ---------------------------------------------------------------------------
BG = "#1e1e2e"
BG_CARD = "#2a2a3d"
BG_INPUT = "#363650"
FG = "#cdd6f4"
FG_DIM = "#7f849c"
FG_BRIGHT = "#ffffff"
ACCENT = "#89b4fa"
ACCENT_HOVER = "#74a8fc"
GREEN = "#a6e3a1"
RED = "#f38ba8"
YELLOW = "#f9e2af"
BORDER = "#45475a"
FONT = ("SF Pro Display", 13)
FONT_BOLD = ("SF Pro Display", 13, "bold")
FONT_SMALL = ("SF Pro Display", 11)
FONT_TITLE = ("SF Pro Display", 20, "bold")
FONT_MONO = ("SF Mono", 12)


class SupplierMatcherGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Supplier Name Matcher")
        self.root.configure(bg=BG)
        self.root.minsize(820, 700)

        # State
        self.csv1_path = ""
        self.csv1_headers: list = []
        self.csv1_rows: list = []
        self.csv2_path = ""
        self.csv2_headers: list = []
        self.csv2_rows: list = []
        self.results: list = []
        self.is_running = False

        # Style
        self._setup_styles()

        # Layout: scrollable main frame
        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(root, orient="vertical", command=self.canvas.yview)
        self.main_frame = tk.Frame(self.canvas, bg=BG)

        self.main_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas_window = self.canvas.create_window((0, 0), window=self.main_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        # Make canvas window resize with the canvas
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # Mouse wheel scrolling
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)

        # Build UI
        self._build_ui()

    # -------------------------------------------------------------------
    # Style setup
    # -------------------------------------------------------------------
    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=BG_INPUT, background=BG_INPUT,
                         foreground=FG, selectbackground=ACCENT, selectforeground=BG,
                         arrowcolor=FG)
        style.map("TCombobox",
                   fieldbackground=[("readonly", BG_INPUT)],
                   foreground=[("readonly", FG)])
        style.configure("TScrollbar", background=BG_CARD, troughcolor=BG,
                         arrowcolor=FG_DIM)
        style.configure("Horizontal.TProgressbar", troughcolor=BG_INPUT,
                         background=ACCENT, thickness=8)

    # -------------------------------------------------------------------
    # Canvas helpers
    # -------------------------------------------------------------------
    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(-1 * (event.delta // 120 or event.delta), "units")

    # -------------------------------------------------------------------
    # Widget helpers
    # -------------------------------------------------------------------
    def _card(self, parent, **kw) -> tk.Frame:
        f = tk.Frame(parent, bg=BG_CARD, highlightbackground=BORDER,
                     highlightthickness=1, **kw)
        f.pack(fill="x", padx=20, pady=(0, 14))
        return f

    def _label(self, parent, text, font=FONT, fg=FG, **kw) -> tk.Label:
        return tk.Label(parent, text=text, font=font, fg=fg, bg=parent["bg"], **kw)

    def _section_title(self, parent, text):
        self._label(parent, text, font=FONT_BOLD, fg=ACCENT).pack(anchor="w", padx=16, pady=(14, 4))

    def _row_frame(self, parent) -> tk.Frame:
        f = tk.Frame(parent, bg=parent["bg"])
        f.pack(fill="x", padx=16, pady=4)
        return f

    def _combo(self, parent, values=(), width=30) -> ttk.Combobox:
        cb = ttk.Combobox(parent, values=values, width=width, state="readonly", font=FONT_SMALL)
        return cb

    def _button(self, parent, text, command, accent=False) -> tk.Button:
        bg = ACCENT if accent else BG_INPUT
        fg_c = BG if accent else FG
        hover = ACCENT_HOVER if accent else BORDER
        btn = tk.Button(parent, text=text, command=command, font=FONT_BOLD,
                        fg=fg_c, bg=bg, activeforeground=fg_c, activebackground=hover,
                        relief="flat", cursor="hand2", padx=16, pady=6)
        btn.bind("<Enter>", lambda e: btn.configure(bg=hover))
        btn.bind("<Leave>", lambda e: btn.configure(bg=bg))
        return btn

    # -------------------------------------------------------------------
    # Build UI
    # -------------------------------------------------------------------
    def _build_ui(self):
        pad = tk.Frame(self.main_frame, bg=BG, height=10)
        pad.pack()

        # Title
        self._label(self.main_frame, "Supplier Name Matcher", font=FONT_TITLE, fg=FG_BRIGHT).pack(pady=(10, 2))
        self._label(self.main_frame, "Match supplier names across CSVs using exact + LLM fuzzy matching",
                     font=FONT_SMALL, fg=FG_DIM).pack(pady=(0, 16))

        # --- Card 1: Source CSV ---
        card1 = self._card(self.main_frame)
        self._section_title(card1, "1. Source CSV (contains supplier names to match)")

        row = self._row_frame(card1)
        self.csv1_label = self._label(row, "No file selected", font=FONT_SMALL, fg=FG_DIM)
        self.csv1_label.pack(side="left", fill="x", expand=True)
        self._button(row, "Browse...", self._browse_csv1).pack(side="right")

        row2 = self._row_frame(card1)
        self._label(row2, "Unique ID column:", font=FONT_SMALL).pack(side="left")
        self.combo_id = self._combo(row2)
        self.combo_id.pack(side="right", padx=(8, 0))

        row3 = self._row_frame(card1)
        self._label(row3, "Supplier Name column:", font=FONT_SMALL).pack(side="left")
        self.combo_name_src = self._combo(row3)
        self.combo_name_src.pack(side="right", padx=(8, 0))

        # spacer
        tk.Frame(card1, bg=BG_CARD, height=8).pack()

        # --- Card 2: Lookup CSV ---
        card2 = self._card(self.main_frame)
        self._section_title(card2, "2. Lookup CSV (contains supplier codes)")

        row = self._row_frame(card2)
        self.csv2_label = self._label(row, "No file selected", font=FONT_SMALL, fg=FG_DIM)
        self.csv2_label.pack(side="left", fill="x", expand=True)
        self._button(row, "Browse...", self._browse_csv2).pack(side="right")

        row2 = self._row_frame(card2)
        self._label(row2, "Supplier Name column:", font=FONT_SMALL).pack(side="left")
        self.combo_name_lookup = self._combo(row2)
        self.combo_name_lookup.pack(side="right", padx=(8, 0))

        row3 = self._row_frame(card2)
        self._label(row3, "Supplier Code column:", font=FONT_SMALL).pack(side="left")
        self.combo_code = self._combo(row3)
        self.combo_code.pack(side="right", padx=(8, 0))

        tk.Frame(card2, bg=BG_CARD, height=8).pack()

        # --- Card 3: API Key ---
        card3 = self._card(self.main_frame)
        self._section_title(card3, "3. Anthropic API Key (for LLM fuzzy matching)")

        row = self._row_frame(card3)
        self.api_key_var = tk.StringVar(value=os.environ.get(config.ANTHROPIC_API_KEY_ENV, ""))
        self.api_entry = tk.Entry(row, textvariable=self.api_key_var, show="*",
                                   font=FONT_SMALL, bg=BG_INPUT, fg=FG, insertbackground=FG,
                                   relief="flat", highlightthickness=1, highlightcolor=ACCENT,
                                   highlightbackground=BORDER)
        self.api_entry.pack(fill="x", ipady=4)

        hint_row = self._row_frame(card3)
        self._label(hint_row, "Leave blank to run Phase 1 (exact matching) only", font=FONT_SMALL, fg=FG_DIM).pack(anchor="w")

        tk.Frame(card3, bg=BG_CARD, height=8).pack()

        # --- Card 4: Output ---
        card4 = self._card(self.main_frame)
        self._section_title(card4, "4. Output")

        row = self._row_frame(card4)
        self.output_var = tk.StringVar(value="")
        self.output_entry = tk.Entry(row, textvariable=self.output_var, font=FONT_SMALL,
                                      bg=BG_INPUT, fg=FG, insertbackground=FG,
                                      relief="flat", highlightthickness=1, highlightcolor=ACCENT,
                                      highlightbackground=BORDER)
        self.output_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self._button(row, "Save As...", self._browse_output).pack(side="right", padx=(8, 0))

        tk.Frame(card4, bg=BG_CARD, height=8).pack()

        # --- Run button ---
        run_frame = tk.Frame(self.main_frame, bg=BG)
        run_frame.pack(fill="x", padx=20, pady=(4, 8))
        self.run_btn = self._button(run_frame, "  Run Matching  ", self._run_matching, accent=True)
        self.run_btn.pack(anchor="center")

        # --- Progress ---
        prog_frame = tk.Frame(self.main_frame, bg=BG)
        prog_frame.pack(fill="x", padx=20, pady=(0, 4))
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(prog_frame, variable=self.progress_var,
                                             maximum=100, style="Horizontal.TProgressbar")
        self.progress_bar.pack(fill="x")
        self.status_label = self._label(prog_frame, "", font=FONT_SMALL, fg=FG_DIM)
        self.status_label.pack(anchor="w", pady=(4, 0))

        # --- Card 5: Results preview ---
        card5 = self._card(self.main_frame)
        self._section_title(card5, "Results")

        # Summary labels
        self.summary_frame = tk.Frame(card5, bg=BG_CARD)
        self.summary_frame.pack(fill="x", padx=16, pady=(4, 8))

        self.lbl_total = self._label(self.summary_frame, "", font=FONT_SMALL, fg=FG_DIM)
        self.lbl_total.pack(anchor="w")
        self.lbl_exact = self._label(self.summary_frame, "", font=FONT_SMALL, fg=GREEN)
        self.lbl_exact.pack(anchor="w")
        self.lbl_llm = self._label(self.summary_frame, "", font=FONT_SMALL, fg=ACCENT)
        self.lbl_llm.pack(anchor="w")
        self.lbl_none = self._label(self.summary_frame, "", font=FONT_SMALL, fg=RED)
        self.lbl_none.pack(anchor="w")

        # Table
        table_frame = tk.Frame(card5, bg=BG_CARD)
        table_frame.pack(fill="both", expand=True, padx=16, pady=(0, 14))

        cols = ("uid", "supplier", "matched", "code", "method", "confidence")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=12)

        # Configure treeview style
        style = ttk.Style()
        style.configure("Treeview", background=BG_INPUT, foreground=FG,
                         fieldbackground=BG_INPUT, font=FONT_SMALL, rowheight=24)
        style.configure("Treeview.Heading", background=BG_CARD, foreground=ACCENT,
                         font=FONT_BOLD)
        style.map("Treeview", background=[("selected", ACCENT)],
                   foreground=[("selected", BG)])

        headings = {
            "uid": ("Unique ID", 90),
            "supplier": ("Supplier Name", 170),
            "matched": ("Matched Name", 170),
            "code": ("Code", 90),
            "method": ("Method", 80),
            "confidence": ("Conf.", 60),
        }
        for col_id, (heading, width) in headings.items():
            self.tree.heading(col_id, text=heading)
            self.tree.column(col_id, width=width, minwidth=50)

        tree_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        # Bottom padding
        tk.Frame(self.main_frame, bg=BG, height=20).pack()

    # -------------------------------------------------------------------
    # File browsing
    # -------------------------------------------------------------------
    def _browse_csv1(self):
        path = filedialog.askopenfilename(
            title="Select Source CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            headers, rows = load_csv(path)
        except (ValueError, FileNotFoundError) as e:
            messagebox.showerror("Error", str(e))
            return

        self.csv1_path = path
        self.csv1_headers = list(headers)
        self.csv1_rows = rows
        self.csv1_label.configure(text=f"{os.path.basename(path)}  ({len(rows):,} rows)", fg=FG)

        self.combo_id["values"] = self.csv1_headers
        self.combo_name_src["values"] = self.csv1_headers
        # Auto-select likely columns
        self._auto_select(self.combo_id, self.csv1_headers, ["id", "uid", "unique", "invoice", "key", "identifier"])
        self._auto_select(self.combo_name_src, self.csv1_headers, ["supplier", "vendor", "name", "company"])

        # Set default output path
        if not self.output_var.get():
            default_out = os.path.join(os.path.dirname(path), "matched_output.csv")
            self.output_var.set(default_out)

    def _browse_csv2(self):
        path = filedialog.askopenfilename(
            title="Select Lookup CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            headers, rows = load_csv(path)
        except (ValueError, FileNotFoundError) as e:
            messagebox.showerror("Error", str(e))
            return

        self.csv2_path = path
        self.csv2_headers = list(headers)
        self.csv2_rows = rows
        self.csv2_label.configure(text=f"{os.path.basename(path)}  ({len(rows):,} rows)", fg=FG)

        self.combo_name_lookup["values"] = self.csv2_headers
        self.combo_code["values"] = self.csv2_headers
        self._auto_select(self.combo_name_lookup, self.csv2_headers, ["supplier", "vendor", "name", "company"])
        self._auto_select(self.combo_code, self.csv2_headers, ["code", "id", "number", "key", "identifier"])

    def _browse_output(self):
        path = filedialog.asksaveasfilename(
            title="Save Output CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            self.output_var.set(path)

    def _auto_select(self, combo: ttk.Combobox, headers: list, keywords: list):
        """Try to auto-select a column that matches one of the keywords."""
        for kw in keywords:
            for i, h in enumerate(headers):
                if kw in h.lower():
                    combo.current(i)
                    return

    # -------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------
    def _validate(self) -> bool:
        if not self.csv1_path:
            messagebox.showwarning("Missing Input", "Please select a source CSV file.")
            return False
        if not self.csv2_path:
            messagebox.showwarning("Missing Input", "Please select a lookup CSV file.")
            return False
        if not self.combo_id.get():
            messagebox.showwarning("Missing Input", "Please select the Unique ID column for CSV1.")
            return False
        if not self.combo_name_src.get():
            messagebox.showwarning("Missing Input", "Please select the Supplier Name column for CSV1.")
            return False
        if not self.combo_name_lookup.get():
            messagebox.showwarning("Missing Input", "Please select the Supplier Name column for CSV2.")
            return False
        if not self.combo_code.get():
            messagebox.showwarning("Missing Input", "Please select the Supplier Code column for CSV2.")
            return False
        if not self.output_var.get().strip():
            messagebox.showwarning("Missing Input", "Please specify an output file path.")
            return False
        return True

    # -------------------------------------------------------------------
    # Run matching
    # -------------------------------------------------------------------
    def _run_matching(self):
        if self.is_running:
            return
        if not self._validate():
            return

        self.is_running = True
        self.run_btn.configure(state="disabled", bg=BORDER)
        self.progress_var.set(0)
        self._set_status("Starting...")
        self._clear_results()

        # Run in background thread to keep UI responsive
        thread = threading.Thread(target=self._matching_worker, daemon=True)
        thread.start()

    def _matching_worker(self):
        try:
            self._do_matching()
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
        finally:
            self.root.after(0, self._on_matching_done)

    def _on_matching_done(self):
        self.is_running = False
        self.run_btn.configure(state="normal", bg=ACCENT)

    def _do_matching(self):
        # Extract data
        self._set_status_safe("Extracting records...")
        id_col = self.combo_id.get()
        name_col_src = self.combo_name_src.get()
        name_col_lookup = self.combo_name_lookup.get()
        code_col = self.combo_code.get()
        output_path = self.output_var.get().strip()

        records = extract_supplier_records(self.csv1_rows, id_col, name_col_src)
        lookup_entries = extract_lookup_entries(self.csv2_rows, name_col_lookup, code_col)

        if not records:
            raise ValueError("No valid source records found. Check column selections.")
        if not lookup_entries:
            raise ValueError("No valid lookup entries found. Check column selections.")

        total = len(records)

        # Phase 1: Exact matching
        self._set_status_safe(f"Phase 1: Exact matching ({total:,} records)...")
        self._set_progress_safe(5)

        lookup_index = build_lookup_index(lookup_entries)
        exact_results, unmatched = exact_match(records, lookup_index)

        exact_pct = len(exact_results) / total * 100
        self._set_status_safe(
            f"Phase 1 complete: {len(exact_results):,} exact matches ({exact_pct:.1f}%), "
            f"{len(unmatched):,} unmatched"
        )
        self._set_progress_safe(30)

        # Phase 2: LLM matching
        llm_results = []
        api_key = self.api_key_var.get().strip()

        if unmatched and api_key:
            num_batches = math.ceil(len(unmatched) / config.LLM_BATCH_SIZE)
            self._set_status_safe(
                f"Phase 2: LLM matching ({len(unmatched):,} records, {num_batches} batches)..."
            )

            # Temporarily set the API key in environment for the client
            old_key = os.environ.get(config.ANTHROPIC_API_KEY_ENV)
            os.environ[config.ANTHROPIC_API_KEY_ENV] = api_key
            try:
                client = create_client()

                def on_progress(completed, total_batches):
                    pct = 30 + (completed / total_batches) * 65
                    self._set_progress_safe(pct)
                    self._set_status_safe(
                        f"Phase 2: LLM batch {completed}/{total_batches}..."
                    )

                llm_results = llm_match_batch(client, unmatched, lookup_entries, on_progress)
            finally:
                # Restore original key
                if old_key is not None:
                    os.environ[config.ANTHROPIC_API_KEY_ENV] = old_key
                elif config.ANTHROPIC_API_KEY_ENV in os.environ:
                    del os.environ[config.ANTHROPIC_API_KEY_ENV]
        elif unmatched:
            self._set_status_safe("No API key provided. Skipping Phase 2.")
            for rec in unmatched:
                llm_results.append(
                    MatchResult(
                        unique_id=rec.unique_id,
                        supplier_name=rec.supplier_name,
                        matched_supplier_name=None,
                        supplier_code=None,
                        match_method=MatchMethod.NONE,
                        confidence=0.0,
                    )
                )

        # Combine results preserving original order
        result_map = {}
        for r in exact_results:
            result_map[r.unique_id] = r
        for r in llm_results:
            result_map[r.unique_id] = r

        all_results = [result_map[rec.unique_id] for rec in records if rec.unique_id in result_map]
        self.results = all_results

        # Write output
        self._set_status_safe("Writing output CSV...")
        write_output_csv(all_results, output_path)

        self._set_progress_safe(100)

        # Update UI
        exact_count = sum(1 for r in all_results if r.match_method == MatchMethod.EXACT)
        llm_count = sum(1 for r in all_results if r.match_method == MatchMethod.LLM)
        no_match_count = sum(1 for r in all_results if r.match_method == MatchMethod.NONE)
        llm_avg = 0.0
        if llm_count > 0:
            llm_avg = sum(r.confidence for r in all_results if r.match_method == MatchMethod.LLM) / llm_count

        self.root.after(0, lambda: self._show_results(
            all_results, total, exact_count, llm_count, no_match_count, llm_avg, output_path
        ))

    # -------------------------------------------------------------------
    # UI updates from worker thread
    # -------------------------------------------------------------------
    def _set_status_safe(self, text):
        self.root.after(0, lambda: self._set_status(text))

    def _set_progress_safe(self, value):
        self.root.after(0, lambda: self.progress_var.set(value))

    def _set_status(self, text):
        self.status_label.configure(text=text)

    def _clear_results(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.lbl_total.configure(text="")
        self.lbl_exact.configure(text="")
        self.lbl_llm.configure(text="")
        self.lbl_none.configure(text="")

    def _show_results(self, results, total, exact_count, llm_count, no_match_count, llm_avg, output_path):
        self._set_status(f"Done! Output saved to {output_path}")

        # Summary
        self.lbl_total.configure(text=f"Total records:  {total:,}")
        self.lbl_exact.configure(text=f"Exact matches:  {exact_count:,}  ({exact_count / total * 100:.1f}%)")
        if llm_count > 0:
            self.lbl_llm.configure(text=f"LLM matches:    {llm_count:,}  ({llm_count / total * 100:.1f}%)  avg conf: {llm_avg:.2f}")
        else:
            self.lbl_llm.configure(text=f"LLM matches:    0")
        self.lbl_none.configure(text=f"No match:       {no_match_count:,}  ({no_match_count / total * 100:.1f}%)")

        # Populate table
        for item in self.tree.get_children():
            self.tree.delete(item)

        for r in results:
            tag = r.match_method.value
            self.tree.insert("", "end", values=(
                r.unique_id,
                r.supplier_name,
                r.matched_supplier_name or "",
                r.supplier_code or "",
                r.match_method.value,
                f"{r.confidence:.2f}",
            ), tags=(tag,))

        self.tree.tag_configure("exact", foreground=GREEN)
        self.tree.tag_configure("llm", foreground=ACCENT)
        self.tree.tag_configure("no_match", foreground=RED)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    root = tk.Tk()

    # Center window on screen
    w, h = 880, 820
    x = (root.winfo_screenwidth() // 2) - (w // 2)
    y = (root.winfo_screenheight() // 2) - (h // 2)
    root.geometry(f"{w}x{h}+{x}+{y}")

    SupplierMatcherGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
