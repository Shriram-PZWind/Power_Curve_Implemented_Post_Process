# =============================================================================
# output_writer.py
# OpenFAST Postprocessing — Output File Writers
#
# All output uses fixed-width space-aligned text.
#
# Files written
# -------------
#   <stem>.sta          per-file extreme + DEL statistics
#   summary.sta         summary matrices (all files × all sensors)
#   <sensor>.rfc        1D RFC spectrum (range bins × cycle count)
#   <sensor>.markov     2D Markov matrix (min-level × max-level)
#   <sensor>.ldd        LDD (level bins × time + revolutions)
#   <sensor>.lrd        LRD (level bins × time + revolutions)
# =============================================================================
import os
import numpy as np
from extreme_stats import STAT_KEYS
from config import fmt_slope

# ─── Column width settings ────────────────────────────────────────────────────
_SENSOR_COL_W = 40  # width of sensor-name / file-name label column
_VAL_W = 16  # width of each numeric value column
_HDR_W = 16  # width of each header label

# ─── Formatting helpers ───────────────────────────────────────────────────────


def _hline(n_val_cols, label_width=None):
    w = label_width or _SENSOR_COL_W
    return "-" * (w + n_val_cols * _VAL_W) + "\n"


def _fmt(v):
    """Format a numeric value for fixed-width output."""
    return f"{v:{_VAL_W}.6g}"


def _hdr(s):
    """Format a header label."""
    return f"{str(s):>{_HDR_W}}"


def _dynamic_label_width(names):
    """Return label column width that fits all names."""
    return max(_SENSOR_COL_W, max((len(n) for n in names), default=0) + 3)


# =============================================================================
# Per-file .sta
# =============================================================================


def write_per_file_sta(output_path, sensor_cols, extreme_stats, del_stats, m_values):
    """
    Write per-file statistics file.

    Layout
    ------
    Rows    = sensor channels (with units)
    Columns = Max  Min  Mean  Stdev  Range  AbsMax  RMS  DEL_m3  DEL_m4  ...

    Parameters
    ----------
    output_path  : str — full path to output .sta file
    sensor_cols  : list of str — sensor column names in order
    extreme_stats: dict {sensor: {stat: float}}
    del_stats    : dict {sensor: {m: float}}
    m_values     : list of int/float
    """
    stat_headers = STAT_KEYS + [f"DEL_m{fmt_slope(m)}" for m in m_values]
    n_cols = len(stat_headers)
    label_w = _dynamic_label_width(sensor_cols)

    with open(output_path, "w", encoding="utf-8") as f:
        # Column header
        f.write(f"{'Sensor':<{label_w}}")
        for h in stat_headers:
            f.write(f"{str(h):>{_VAL_W}}")
        f.write("\n")
        f.write(_hline(n_cols, label_w))

        # One row per sensor
        for col in sensor_cols:
            f.write(f"{col:<{label_w}}")
            for stat in STAT_KEYS:
                f.write(_fmt(extreme_stats.get(col, {}).get(stat, 0.0)))
            for m in m_values:
                f.write(_fmt(del_stats.get(col, {}).get(m, 0.0)))
            f.write("\n")


# =============================================================================
# summary.sta  (15 matrices: 7 extreme + 8 DEL, one per m)
# =============================================================================


def write_summary_sta(
    output_path, file_names, sensor_cols, all_extreme, all_del, m_values, header=None
):
    """
    Write (or overwrite) a summary .sta file.

    Layout
    ------
    Optional header block followed by 15 matrices stacked vertically:
      Max | Min | Mean | Stdev | Range | AbsMax | RMS | DEL_m3 | ... | DEL_m25
    Each matrix:
      rows    = file/family names
      columns = sensor channels

    Parameters
    ----------
    output_path  : str
    file_names   : list of str — ordered list of row labels
    sensor_cols  : list of str
    all_extreme  : dict {fname: {sensor: {stat: float}}}
    all_del      : dict {fname: {sensor: {m: float}}}
    m_values     : list of int/float
    header       : list of str or None — header lines written at top of file
    """
    matrices = STAT_KEYS + [f"DEL_m{fmt_slope(m)}" for m in m_values]
    n_cols = len(sensor_cols)
    label_w = _dynamic_label_width(file_names + ["LIFETIME_DEL_20yr"])
    divider = "=" * (label_w + n_cols * _VAL_W)

    with open(output_path, "w", encoding="utf-8") as f:
        # Write optional header block
        if header:
            for line in header:
                f.write(line + "\n")
            f.write("\n")
        f.write("OpenFAST Postprocessing — Statistics Summary\n")
        f.write(divider + "\n")

        for matrix_name in matrices:
            f.write(f"\n{divider}\n")
            f.write(f"  {matrix_name}\n")
            f.write(f"{divider}\n")

            # Column header (sensor names)
            f.write(f"{'File':<{label_w}}")
            for col in sensor_cols:
                f.write(f"{str(col):>{_VAL_W}}")
            f.write("\n")
            f.write(_hline(n_cols, label_w))

            is_stat = matrix_name in STAT_KEYS
            is_del = matrix_name.startswith("DEL_m")

            # One row per processed file
            for fname in file_names:
                f.write(f"{fname:<{label_w}}")
                for col in sensor_cols:
                    if is_stat:
                        val = (
                            all_extreme.get(fname, {})
                            .get(col, {})
                            .get(matrix_name, 0.0)
                        )
                    else:
                        m = int(float(matrix_name.split("m")[1]))
                        val = all_del.get(fname, {}).get(col, {}).get(m, 0.0)
                    f.write(_fmt(val))

                f.write("\n")
                
            # Note: Lifetime DEL is written in the respective .rfc/.ldd/.lrd files
            # summary_Raw/Family/FamilyPLF.sta contain per-file/family values only
                    
        f.write(f"\n{divider}\n")
        
# def write_summary_sta( output_path, file_names, sensor_cols, all_extreme, all_del, m_values, header=None ):
#     """
#     Write (or overwrite) a summary .sta file.
#     Layout
#     ------
#     Optional header block followed by 15 matrices stacked vertically:
#     Max | Min | Mean | Stdev | Range | AbsMax | RMS | DEL_m3 | ... | DEL_m25
#     Each matrix: rows = file/family names columns = sensor channels
#     Parameters
#     ----------
#     output_path : str
#     file_names : list of str — ordered list of row labels
#     sensor_cols : list of str
#     all_extreme : dict {fname: {sensor: {stat: float}}}
#     all_del : dict {fname: {sensor: {m: float}}}
#     m_values : list of int/float
#     header : list of str or None — header lines written at top of file
#     """
#     matrices = STAT_KEYS + [f"DEL_m{fmt_slope(m)}" for m in m_values]
#     n_cols = len(sensor_cols)
#     label_w = _dynamic_label_width(file_names + ["LIFETIME_DEL_20yr"])
#     divider = "=" * (label_w + n_cols * _VAL_W)

#     with open(output_path, "w", encoding="utf-8") as f:
      
#         if header:
#             for line in header:
#                 f.write(line + "\n")
#                 print(line) 
#             f.write("\n")
#             print() 

#         f.write("OpenFAST Postprocessing — Statistics Summary\n")
#         print("OpenFAST Postprocessing — Statistics Summary") 
        
#         f.write(divider + "\n")
#         print(divider) # Print upper divider

#         for matrix_name in matrices:
#             f.write(f"\n{divider}\n")
#             print(f"\n{divider}") 
            
#             f.write(f" {matrix_name}\n")
#             print(f" {matrix_name}") 
            
#             f.write(f"{divider}\n")
#             print(divider) 

#             # Column header (sensor names)
#             col_header = f"{'File':<{label_w}}"
#             for col in sensor_cols:
#                 col_header += f"{str(col):>{_VAL_W}}"
#             f.write(col_header + "\n")
#             print(col_header) 

#             f.write(_hline(n_cols, label_w))
#             print(_hline(n_cols, label_w), end="") 

#             is_stat = matrix_name in STAT_KEYS
#             is_del = matrix_name.startswith("DEL_m")

#             # One row per processed file
#             for fname in file_names:
#                 row_str = f"{fname:<{label_w}}"
#                 for col in sensor_cols:
#                     if is_stat:
#                         val = (
#                             all_extreme.get(fname, {})
#                             .get(col, {})
#                             .get(matrix_name, 0.0)
#                         )
#                     else:
#                         m = int(float(matrix_name.split("m")[1]))
#                         val = all_del.get(fname, {}).get(col, {}).get(m, 0.0)
#                     row_str += _fmt(val)
                
#                 f.write(row_str + "\n")
#                 print(row_str) 
                
#             f.write("\n")
#             print() 

#         # Final file divider
#         f.write(f"\n{divider}\n")
#         print(f"\n{divider}")




# def extract_mean_to_txt(input_sta_path, output_txt_path):
#     """
#     Reads a .sta file, extracts the 'Mean' data block up until the next section,
#     and writes it directly into a clean .txt file.
#     """
#     # Verify the source file exists
#     if not os.path.exists(input_sta_path):
#         print(f"Error: The file {input_sta_path} does not exist.")
#         return

#     mean_lines = []
#     capture = False

#     with open(input_sta_path, "r", encoding="utf-8") as f:
#         lines = f.readlines()

#     for i, line in enumerate(lines):
#         clean_line = line.strip()

#         # 1. Detect when the Mean block begins
#         # It handles checking for the "Mean" header text bounded by dividers
#         if "Mean" in line and i > 0 and "===" in lines[i-1]:
#             capture = True
#             continue

#         # 2. Detect when the next section begins (e.g., Stdev, Range, or divider blocks)
#         # It stops capturing when it hits the next matrix header block
#         if capture and "===" in line and "Mean" not in lines[i-1] and i+1 < len(lines) and not any(k in lines[i+1] for k in ["File", "---"]):
#             # Check if the upcoming block looks like a new header category
#             if any(keyword in lines[i+1] for keyword in ["Stdev", "Range", "AbsMax", "RMS", "DEL"]):
#                 break

#         # 3. Collect lines belonging to the Mean data block
#         if capture:
#             # Skip empty lines or lone section-bounding lines right under the header
#             if clean_line and not (clean_line.startswith("====") and len(mean_lines) == 0):
#                 mean_lines.append(line)

#     # 4. Clean up any lingering trailing divider lines collected near the end boundary
#     while mean_lines and mean_lines[-1].strip().startswith("==="):
#         mean_lines.pop()

#     # 5. Write the extracted Mean rows straight into your new output file
#     os.makedirs(os.path.dirname(output_txt_path), exist_ok=True)
#     with open(output_txt_path, "w", encoding="utf-8") as out_f:
#         out_f.write("Extracted Mean Sensor Data Summary\n")
#         out_f.write("=" * 50 + "\n")
#         out_f.writelines(mean_lines)

#     print(f"Success! Filtered text file created at: {output_txt_path}")

# # --- Execution ---
# input_file = os.path.join("STA", "summary_Raw.sta")
# output_file = os.path.join("STA", "extracted_mean_data.txt")

# extract_mean_to_txt(input_file, output_file)


# =============================================================================
# Per-sensor fatigue matrix files
# =============================================================================


def write_rfc_file(
    output_path,
    bin_edges,
    counts_20yr,
    sensor_col,
    del_rfc=None,
    lifetime_years=None,
    neq=None,
    m_values=None,
):
    """
    Write 1D RFC spectrum file  (<sensor>.rfc).

    Header block (if del_rfc provided): lifetime, Neq, DEL for all m values.
    Columns: BinRange | BinCenter | CycleCount_lifetime
      Col 1 — BinRange            : bin min-max range as a string
      Col 2 — BinCenter           : bin centre value (numeric)
      Col 3 — CycleCount_lifetime : lifetime accumulated cycle count (numeric)

    Parameters
    ----------
    del_rfc        : dict {m: float} or None — lifetime DEL per Wöhler slope
    lifetime_years : float or None
    neq            : float or None — Neq used for DEL calculation
    m_values       : list or None
    """
    low = bin_edges[:-1]
    high = bin_edges[1:]
    center = 0.5 * (low + high)
    rw = 28  # width of BinRange string column
    cw = 20  # width of numeric columns

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# RFC Spectrum\n")
        f.write(f"# Sensor  : {sensor_col}\n")
        f.write(f"# Content : 1D range spectrum, lifetime accumulated cycle counts\n")
        f.write(f"# Bins    : {len(center)}\n")
        f.write("#\n")
        # DEL header block
        if del_rfc is not None and m_values is not None:
            f.write(f"# Lifetime : {lifetime_years:.0f} years\n")
            f.write(f"# Neq      : {neq:.3g}\n")
            f.write("#\n")
            # DEL row — horizontal, all m values
            m_hdr = "".join(f"{f'DEL_m{fmt_slope(m)}':>{cw}}" for m in m_values)
            m_val = "".join(f"{del_rfc.get(m, 0.0):>{cw}.6g}" for m in m_values)
            f.write(f"# {'Lifetime DEL':>{rw-2}}" + m_hdr + "\n")
            f.write(f"# {'[same units as sensor]':>{rw-2}}" + m_val + "\n")
            f.write("#\n")
        f.write(
            f"# {'BinRange (min - max)':>{rw}} {'BinCenter':>{cw}} "
            f"{'CycleCount_lifetime':>{cw}}\n"
        )
        f.write("# " + "-" * (rw + 2 * cw + 4) + "\n")
        for i in range(len(center)):
            bin_range_str = f"{low[i]:.6g} - {high[i]:.6g}"
            f.write(
                f"  {bin_range_str:>{rw}} {center[i]:>{cw}.6g} "
                f"{counts_20yr[i]:>{cw}.6g}\n"
            )


def write_markov_file(
    output_path, bin_edges_range, bin_edges_mean, matrix_lifetime, sensor_col
):
    """
    Write 2D Markov matrix file  (<sensor>.markov) — Convention 2: Range × Mean.

    Rows    = cycle range bin centres  (0 → global max RFC range)
    Columns = cycle mean bin centres   (global min → global max signal)
    Values  = lifetime accumulated cycle counts

    Parameters
    ----------
    bin_edges_range  : np.ndarray (n_bins+1,) — range bin edges
    bin_edges_mean   : np.ndarray (n_bins+1,) — mean bin edges
    matrix_lifetime  : np.ndarray (n_range, n_mean) — lifetime cycle counts
    sensor_col       : str
    """
    range_centers = 0.5 * (bin_edges_range[:-1] + bin_edges_range[1:])
    mean_centers = 0.5 * (bin_edges_mean[:-1] + bin_edges_mean[1:])
    n_range = len(range_centers)
    n_mean = len(mean_centers)
    cw = 14

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# Markov Matrix (Convention 2: Range x Mean)\n")
        f.write(f"# Sensor  : {sensor_col}\n")
        f.write(f"# Rows    : cycle range bin centres  (0 → global max RFC range)\n")
        f.write(f"# Columns : cycle mean bin centres   (global min → global max)\n")
        f.write(f"# Values  : lifetime accumulated cycle counts\n")
        f.write(f"# Bins    : {n_range} (range) x {n_mean} (mean)\n")
        f.write("#\n")

        # Column header (mean centres)
        # f.write(f"{'Range\\Mean':>{cw+2}}")
        label = "Min\\Max"
        f.write(f"{label:>{cw+2}}")
        for bc in mean_centers:
            f.write(f" {bc:>{cw}.6g}")
        f.write("\n")
        f.write("# " + "-" * ((n_mean + 1) * (cw + 1)) + "\n")

        # Data rows (range centres as row labels)
        for i in range(n_range):
            f.write(f"  {range_centers[i]:>{cw}.6g}")
            for j in range(n_mean):
                f.write(f" {matrix_lifetime[i, j]:>{cw}.6g}")
            f.write("\n")


def write_ldd_file(
    output_path,
    bin_edges_level,
    time_at_level,
    revs_at_level,
    sensor_col,
    del_ldd=None,
    lifetime_years=None,
    neq_time=None,
    m_values=None,
):
    """
    Write Level Duration Distribution file  (<sensor>.ldd).

    Header block (if del_ldd provided): lifetime, Neq_time, DEL for all m values.
    Columns:
      Col 1 — LevelBinRange (min - max) : bin range as string
      Col 2 — LevelBinMean              : bin centre value (numeric)
      Col 3 — CumulTime_s               : lifetime accumulated time (s)

    Parameters
    ----------
    del_ldd        : dict {m: float} or None — lifetime DEL per Wöhler slope
    lifetime_years : float or None
    neq_time       : float or None — full lifetime in seconds (Neq for LDD DEL)
    m_values       : list or None
    """
    low = bin_edges_level[:-1]
    high = bin_edges_level[1:]
    center = 0.5 * (low + high)
    rw = 28
    cw = 20

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# LDD (Level Duration Distribution)\n")
        f.write(f"# Sensor  : {sensor_col}\n")
        f.write("# Content : lifetime accumulated time at each load level\n")
        f.write(f"# Bins    : {len(center)}\n")
        f.write("#\n")
        # DEL header block
        if del_ldd is not None and m_values is not None:
            f.write(f"# Lifetime  : {lifetime_years:.0f} years\n")
            f.write(
                f"# Neq_time  : {neq_time:.3g} s  (full lifetime, 1Hz assumption)\n"
            )
            f.write("#\n")
            m_hdr = "".join(f"{f'DEL_m{fmt_slope(m)}':>{cw}}" for m in m_values)
            m_val = "".join(f"{del_ldd.get(m, 0.0):>{cw}.6g}" for m in m_values)
            f.write(f"# {'Lifetime DEL':>{rw-2}}" + m_hdr + "\n")
            f.write(f"# {'[same units as sensor]':>{rw-2}}" + m_val + "\n")
            f.write("#\n")
        f.write(
            f"# {'LevelBinRange (min - max)':>{rw}} {'LevelBinMean':>{cw}} "
            f"{'CumulTime_s':>{cw}}\n"
        )
        f.write("# " + "-" * (rw + 2 * cw + 4) + "\n")
        for i in range(len(center)):
            bin_range_str = f"{low[i]:.6g} - {high[i]:.6g}"
            f.write(
                f"  {bin_range_str:>{rw}} {center[i]:>{cw}.6g} "
                f"{time_at_level[i]:>{cw}.6g}\n"
            )


def write_lrd_file(
    output_path,
    bin_edges_level,
    time_at_level,
    revs_at_level,
    sensor_col,
    del_lrd=None,
    lifetime_years=None,
    neq_rev=None,
    m_values=None,
):
    """
    Write Level Revolution Distribution file  (<sensor>.lrd).

    Header block (if del_lrd provided): lifetime, Neq_rev, DEL for all m values.
    Columns:
      Col 1 — LevelBinRange (min - max) : bin range as string
      Col 2 — LevelBinMean              : bin centre value (numeric)
      Col 3 — CumulRevolutions          : lifetime accumulated revolutions

    Parameters
    ----------
    del_lrd        : dict {m: float} or None — lifetime DEL per Wöhler slope
    lifetime_years : float or None
    neq_rev        : float or None — reference revolution count (e.g. 1e8)
    m_values       : list or None
    """
    low = bin_edges_level[:-1]
    high = bin_edges_level[1:]
    center = 0.5 * (low + high)
    rw = 28
    cw = 20

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# LRD (Level Revolution Distribution)\n")
        f.write(f"# Sensor  : {sensor_col}\n")
        f.write("# Content : lifetime accumulated revolutions at each load level\n")
        f.write(f"# Bins    : {len(center)}\n")
        f.write("#\n")
        # DEL header block
        if del_lrd is not None and m_values is not None:
            f.write(f"# Lifetime  : {lifetime_years:.0f} years\n")
            f.write(f"# Neq_rev   : {neq_rev:.3g}  (reference revolutions)\n")
            f.write("#\n")
            m_hdr = "".join(f"{f'DEL_m{fmt_slope(m)}':>{cw}}" for m in m_values)
            m_val = "".join(f"{del_lrd.get(m, 0.0):>{cw}.6g}" for m in m_values)
            f.write(f"# {'Lifetime DEL':>{rw-2}}" + m_hdr + "\n")
            f.write(f"# {'[same units as sensor]':>{rw-2}}" + m_val + "\n")
            f.write("#\n")
        f.write(
            f"# {'LevelBinRange (min - max)':>{rw}} {'LevelBinMean':>{cw}} "
            f"{'CumulRevolutions':>{cw}}\n"
        )
        f.write("# " + "-" * (rw + 2 * cw + 4) + "\n")
        for i in range(len(center)):
            bin_range_str = f"{low[i]:.6g} - {high[i]:.6g}"
            f.write(
                f"  {bin_range_str:>{rw}} {center[i]:>{cw}.6g} "
                f"{revs_at_level[i]:>{cw}.6g}\n"
            )


# =============================================================================
# EXT ranking files  (.max, .min, .abs)
# =============================================================================


def write_ext_file(output_path, sensor_col, stat, ranked_plf, ranked_noplf):
    """
    Write EXT ranking file for one sensor and one statistic.

    Format
    ------
    Header summary block — sensor name, design extreme with and without PLF
    Table 1 — ranked with PLF    (3 cols: Value_PLF | PLF | FamilyName)
    Table 2 — ranked without PLF (3 cols: Value     | PLF | FamilyName)

    Parameters
    ----------
    output_path  : str
    sensor_col   : str — e.g. 'RootMyc1_[kN-m]'
    stat         : str — 'Max', 'Min', or 'AbsMax'
    ranked_plf   : list of (value_plf, plf, family_name) — pre-sorted
    ranked_noplf : list of (value_raw, family_name) — pre-sorted
    """
    # Extract unit from sensor name e.g. 'RootMyc1_[kN-m]' → 'kN-m'
    import re

    unit_match = re.search(r"\[(.+?)\]", sensor_col)
    unit = unit_match.group(1) if unit_match else "-"

    # Column widths
    w_rank = 6
    w_val = 20
    w_plf = 10
    w_fname = 20
    divider = "=" * (w_rank + w_val + w_plf + w_fname + 6)
    subdiv = "-" * (w_rank + w_val + w_plf + w_fname + 6)

    # Design extremes (rank 1 from each table)
    top_plf = ranked_plf[0] if ranked_plf else None
    top_noplf = ranked_noplf[0] if ranked_noplf else None

    with open(output_path, "w", encoding="utf-8") as f:
        # ── Summary header ─────────────────────────────────────────────────
        f.write(f"# {divider}\n")
        f.write(f"# EXT — {stat} Load Ranking\n")
        f.write(f"# {divider}\n")
        f.write(f"# Sensor : {sensor_col}\n")
        f.write("#\n")
        if top_plf:
            f.write(
                f"# Design Extreme (with PLF)    : "
                f"{top_plf[0]:>12.4f}  {unit:<8}  "
                f"PLF [{top_plf[1]:.2f}]     {top_plf[2]}\n"
            )
        if top_noplf:
            f.write(
                f"# Design Extreme (without PLF) : "
                f"{top_noplf[0]:>12.4f}  {unit:<8}  "
                f"PLF [-]        {top_noplf[1]}\n"
            )
        f.write("#\n")
        f.write(f"# {divider}\n")
        f.write("#\n")

        # ── Table 1: with PLF ──────────────────────────────────────────────
        f.write(f"# --- Table 1: Ranked with PLF (descending) ---\n")
        f.write("#\n")
        hdr = (
            f"{'Rank':>{w_rank}}  "
            f"{sensor_col:>{w_val}}  "
            f"{'PLF':>{w_plf}}  "
            f"{'FamilyName':<{w_fname}}"
        )
        f.write(f"# {hdr}\n")
        f.write(f"# {subdiv}\n")
        for rank, (val, plf, fname) in enumerate(ranked_plf, 1):
            f.write(
                f"  {rank:>{w_rank}}  "
                f"{val:>{w_val}.4f}  "
                f"{plf:>{w_plf}.2f}  "
                f"{fname:<{w_fname}}\n"
            )

        f.write("#\n")

        # ── Table 2: without PLF ───────────────────────────────────────────
        f.write(f"# --- Table 2: Ranked without PLF ---\n")
        f.write("#\n")
        f.write(f"# {hdr}\n")
        f.write(f"# {subdiv}\n")
        for rank, (val, fname) in enumerate(ranked_noplf, 1):
            f.write(
                f"  {rank:>{w_rank}}  "
                f"{val:>{w_val}.4f}  "
                f"{'[-]':>{w_plf}}  "
                f"{fname:<{w_fname}}\n"
            )

        f.write("#\n")
        f.write(f"# {divider}\n")


# =============================================================================
# Complementary load files  (_comp.max, _comp.min, _comp.abs)
# =============================================================================


def _infer_component_label(sensor_name):
    """
    Infer load component label from OpenFAST sensor name.
    Returns one of: 'Mres', 'Mx', 'My', 'Mz', 'Fx', 'Fy', 'Fz', or None.
    """
    n = sensor_name.lower()
    if "mres" in n:
        return "Mres"
    # Moments — check before forces (M before F)
    if any(
        p in n
        for p in ("mxc", "mxb", "mxt", "mxp", "mxa", "mxs", "mlxt", "mxe", "mxr", "mxl")
    ):
        return "Mx"
    if any(
        p in n
        for p in ("myc", "myb", "myt", "myp", "mya", "mys", "mlyt", "mye", "myr", "myl")
    ):
        return "My"
    if any(
        p in n
        for p in ("mzc", "mzb", "mzt", "mzp", "mza", "mzs", "mlzt", "mze", "mzr", "mzl")
    ):
        return "Mz"
    # Forces
    if any(
        p in n for p in ("fxc", "fxb", "fxt", "fxp", "fxa", "fxs", "flxt", "fxr", "fxl")
    ):
        return "Fx"
    if any(
        p in n for p in ("fyc", "fyb", "fyt", "fyp", "fya", "fys", "flyt", "fyr", "fyl")
    ):
        return "Fy"
    if any(
        p in n for p in ("fzc", "fzb", "fzt", "fzp", "fza", "fzs", "flzt", "fzr", "fzl")
    ):
        return "Fz"
    return None


def write_complementary_file(output_path, sensor_col, stat, comp_results, comp_method):
    """
    Write complementary loads file for one sensor and one statistic.

    Format
    ------
    Header  : sensor name, statistic, CompMethod, group
    Table 1 : With PLF    — rows=ranks, cols=Mres|Mx|My|Mz|Fx|Fy|Fz|Family|File|Time
    Table 2 : Without PLF — same column structure

    Parameters
    ----------
    output_path  : str
    sensor_col   : str
    stat         : str — 'Max', 'Min', 'AbsMax'
    comp_results : list of dicts from compute_complementary_loads()
    comp_method  : int — 1, 2, or 3
    """
    import re

    if not comp_results:
        # Driving sensor was not found in any seed file — skip _comp
        print(
            f"  WARNING: No complementary results for {sensor_col} "
            f"({stat}) — _comp file not written (driving sensor "
            f"missing from output files)"
        )
        return

    method_desc = {
        1: "1 — seed closest to family mean",
        2: "2 — worst seed (most conservative)",
        3: "3 — seed with highest moment resultant",
    }

    # Fixed column order
    COMP_ORDER = ["Mres", "Mx", "My", "Mz", "Fx", "Fy", "Fz"]

    # Build column map: scan ALL ranks to find best sensor per label
    # so col_map is stable even if some ranks have missing sensors
    col_map = {}  # {label: sensor_name}
    for res in comp_results:
        for sname in res["comp_values"].keys():
            label = _infer_component_label(sname)
            if label and label not in col_map:
                col_map[label] = sname

    # Build header unit strings per column
    def _unit(sname):
        if sname is None:
            return "-"
        m = re.search(r"\[(.+?)\]", sname)
        return m.group(1) if m else "-"

    # Active columns — ALL 6 standard components always present
    # If col_map has no sensor for a label → (label, None) → N/A in output
    # This ensures consistent column count regardless of missing sensors
    active_cols = [(lbl, col_map.get(lbl)) for lbl in COMP_ORDER]

    # Column widths
    w_rank = 5
    w_val = 14
    w_plf = 8
    w_family = 18
    w_file = 30
    w_time = 9
    n_val_cols = len(active_cols)
    total_w = w_rank + n_val_cols * w_val + w_plf + w_family + w_file + w_time + 4
    divider = "=" * total_w
    subdiv = "-" * total_w

    def _col_header():
        h = f"#  {'Rank':>{w_rank}}"
        for lbl, sname in active_cols:
            unit = _unit(sname)
            h += f"  {lbl+'_['+unit+']':>{w_val}}"
        h += f"  {'PLF':>{w_plf}}  {'Family':<{w_family}}  {'File':<{w_file}}  {'Time_[s]':>{w_time}}"
        return h

    def _data_row(rank, res, use_plf):
        comp_vals = res["comp_values"]
        row = f"   {rank:>{w_rank}}"
        for lbl, sname in active_cols:
            if sname is None or sname not in comp_vals:
                # Sensor not in group or not in this file's output
                row += f"  {'N/A':>{w_val}}"
            else:
                raw_val, fact_val = comp_vals[sname]
                val = fact_val if use_plf else raw_val
                row += f"  {val:>{w_val}.4f}"
        plf_val = res["plf"] if use_plf else 1.00
        row += (
            f"  {plf_val:>{w_plf}.2f}"
            f"  {res['family_name']:<{w_family}}"
            f"  {res['filename']:<{w_file}}"
            f"  {res['time_s']:>{w_time}.2f}"
        )
        return row

    with open(output_path, "w", encoding="utf-8") as f:
        # ── File header ────────────────────────────────────────────────────
        f.write(f"# {divider}\n")
        f.write(f"# Complementary Loads — {stat} of {sensor_col}\n")
        f.write(f"# {divider}\n")
        f.write(f"# Driving sensor : {sensor_col}\n")
        f.write(f"# Statistic      : {stat}\n")
        f.write(
            f"# CompMethod     : {method_desc.get(comp_method, str(comp_method))}\n"
        )
        f.write(
            f'# Columns        : {" | ".join(lbl for lbl, _ in active_cols)} | PLF | Family | File | Time\n'
        )
        f.write(f"# Note           : Driving sensor marked with * in column header\n")
        # List any sensors that are missing from the output
        missing_labels = [lbl for lbl, sname in active_cols if sname is None]
        if missing_labels:
            f.write(
                f'# Missing sensors : {", ".join(missing_labels)} '
                f"— sensor not found in OpenFAST output (N/A shown)\n"
            )
        f.write(f"# {divider}\n\n")

        # ── Design Driving Loads (Rank 1) ──────────────────────────────────
        r1 = comp_results[0]
        drv = r1["driving_sensor"]
        f.write(f'# ── Design Driving Loads (Rank 1) {"-" * 50}\n')
        f.write("#\n")

        # Mark driving sensor column with *
        driving_label = (
            _infer_component_label(comp_results[0]["driving_sensor"])
            if comp_results
            else None
        )

        def _col_header_marked():
            h = f"#  {'Rank':>{w_rank}}"
            for lbl, sname in active_cols:
                unit = _unit(sname)  # '-' if sname is None
                marker = "*" if lbl == driving_label else " "
                col_hdr = marker + lbl + "_[" + unit + "]"
                # Mark missing sensors with '?' in header
                if sname is None:
                    col_hdr = lbl + "_[N/A]"
                h += f"  {col_hdr:>{w_val}}"
            h += f"  {'PLF':>{w_plf}}  {'Family':<{w_family}}  {'File':<{w_file}}  {'Time_[s]':>{w_time}}"
            return h

        # Write design driving header line + two data rows (with/without PLF)
        f.write(_col_header_marked() + "\n")
        f.write(f"# {subdiv}\n")
        f.write("# With PLF    " + _data_row(1, r1, use_plf=True)[3:] + "\n")
        f.write("# Without PLF " + _data_row(1, r1, use_plf=False)[3:] + "\n")
        f.write(f"#\n")
        f.write(f"# {divider}\n\n")

        # ── Table 1: With PLF ──────────────────────────────────────────────
        f.write(f"# TABLE 1 — WITH PLF\n#\n")
        f.write(_col_header_marked() + "\n")
        f.write(f"# {subdiv}\n")
        for res in comp_results:
            f.write(_data_row(res["rank"], res, use_plf=True) + "\n")
        f.write("\n\n")

        # ── Table 2: Without PLF ───────────────────────────────────────────
        f.write(f"# TABLE 2 — WITHOUT PLF\n#\n")
        f.write(_col_header_marked() + "\n")
        f.write(f"# {subdiv}\n")
        for res in comp_results:
            f.write(_data_row(res["rank"], res, use_plf=False) + "\n")
        f.write("\n")

        f.write(f"# {divider}\n")
        f.write("# END OF FILE\n")
        f.write(f"# {divider}\n")


# =============================================================================
# DLC Fatigue Contribution Analysis  (FAT/dlc_contribution.txt)
# =============================================================================


def write_contribution_file(
    output_path,
    damage_per_file,
    file_metadata,
    fatigue_files,
    sensor_cols,
    m_values,
    lifetime_years,
    neq,
):
    """
    Write DLC fatigue contribution analysis to a single combined file.

    Layout per m value (8 m values total):
      Table 1: Damage        — individual seeds  (rows=files,    cols=sensors)
      Table 2: Contribution% — individual seeds  (rows=files,    cols=sensors)
      Table 3: Damage        — family averaged   (rows=families, cols=sensors)
      Table 4: Contribution% — family averaged   (rows=families, cols=sensors)

    Files are ordered as they appear in LC_PostProcess.txt.
    Files with occurrences = 0 are excluded from all tables.

    Parameters
    ----------
    damage_per_file : dict {fname: {sensor: {m: float}}}
    file_metadata   : dict {fname: {Family, FamilyMethod, PLF, SimTime}}
    fatigue_files   : dict {fname: occurrences} — from config.FATIGUE_FILES
    sensor_cols     : list of str — RFC-enabled sensors
    m_values        : list of int/float
    lifetime_years  : float
    neq             : float
    """
    from extreme_stats import _derive_family_name

    # ── Build ordered file list (LC_PostProcess.txt order, occ > 0) ──────────
    ordered_files = [f for f in fatigue_files if fatigue_files[f] > 0]

    # ── Build family grouping (preserve LC order) ─────────────────────────────
    family_order = []
    family_files = {}
    for fname in ordered_files:
        fam_name = _derive_family_name(fname)
        if fam_name not in family_files:
            family_order.append(fam_name)
            family_files[fam_name] = []
        family_files[fam_name].append(fname)

    # Column widths
    w_fname = max(35, max((len(f) for f in ordered_files), default=35) + 2)
    w_fam = max(20, max((len(f) for f in family_order), default=20) + 2)
    w_sensor = 18
    w_occ = 14
    n_sens = len(sensor_cols)
    divider = "=" * (w_fname + w_occ + n_sens * w_sensor + 4)
    subdiv = "-" * (w_fname + w_occ + n_sens * w_sensor + 4)
    fam_div = "-" * (w_fam + w_occ + n_sens * w_sensor + 4)

    def fmt_val(v):
        return f"{v:>{w_sensor}.4e}"

    def fmt_pct(v):
        return f"{v:>{w_sensor}.2f}"

    def sensor_header(label_w):
        hdr = f"  {'':<{label_w}}{' OccFreq':>{w_occ}}"
        for col in sensor_cols:
            hdr += f"{col:>{w_sensor}}"
        return hdr

    with open(output_path, "w", encoding="utf-8") as f:
        # ── File header ───────────────────────────────────────────────────────
        f.write(f"# {divider}\n")
        f.write(f"# DLC Fatigue Contribution Analysis\n")
        f.write(f"# {divider}\n")
        f.write(f"# Lifetime       : {lifetime_years:.0f} years\n")
        f.write(f"# Neq (RFC)      : {neq:.3g}\n")
        f.write(f"# RFC sensors    : {n_sens}\n")
        f.write(f"# Files included : {len(ordered_files)} (occurrences > 0)\n")
        f.write(f"# Families       : {len(family_order)}\n")
        f.write("#\n")
        f.write("# Method: Damage_k = sum(ni_k * Li^m)  raw rainflow damage per file\n")
        f.write(
            "#         Contribution_k = Damage_k * OccFreq_k / Total_damage * 100%\n"
        )
        f.write("#\n")
        f.write("# Layout per m value:\n")
        f.write("#   Table 1: Damage        — individual seeds\n")
        f.write("#   Table 2: Contribution% — individual seeds\n")
        f.write("#   Table 3: Damage        — family averaged\n")
        f.write("#   Table 4: Contribution% — family averaged\n")
        f.write(f"# {divider}\n\n")

        for m in m_values:
            f.write(f'\n# {"=" * 60}\n')
            f.write(f"# Wöhler slope m = {m}\n")
            f.write(f'# {"=" * 60}\n\n')

            # ── Pre-compute weighted damage per file ──────────────────────────
            # weighted_damage[fname][sensor] = damage * occurrences
            weighted = {}
            for fname in ordered_files:
                occ = fatigue_files[fname]
                weighted[fname] = {}
                for col in sensor_cols:
                    d = damage_per_file.get(fname, {}).get(col, {}).get(m, 0.0)
                    weighted[fname][col] = d * occ

            # Total weighted damage per sensor
            total_dmg = {
                col: sum(weighted[f][col] for f in ordered_files) for col in sensor_cols
            }

            # ── Table 1: Damage — individual seeds ────────────────────────────
            f.write(f"# --- Table 1: Damage — Individual seeds (m={m}) ---\n#\n")
            f.write(f"# {sensor_header(w_fname - 2)}\n")
            f.write(f"# {subdiv}\n")
            for fname in ordered_files:
                occ = fatigue_files[fname]
                row = f"  {fname:<{w_fname}}{occ:>{w_occ}.1f}"
                for col in sensor_cols:
                    row += fmt_val(weighted[fname][col])
                f.write(row + "\n")
            f.write(f"# {subdiv}\n")
            total_occ = sum(fatigue_files[fn] for fn in ordered_files)
            tot_row = f"  {'TOTAL':<{w_fname}}{total_occ:>{w_occ}.1f}"
            for col in sensor_cols:
                tot_row += fmt_val(total_dmg[col])
            f.write(tot_row + "\n\n")

            # ── Table 2: Contribution% — individual seeds ─────────────────────
            f.write(f"# --- Table 2: Contribution% — Individual seeds (m={m}) ---\n#\n")
            f.write(f"# {sensor_header(w_fname - 2)}\n")
            f.write(f"# {subdiv}\n")
            for fname in ordered_files:
                occ = fatigue_files[fname]
                row = f"  {fname:<{w_fname}}{occ:>{w_occ}.1f}"
                for col in sensor_cols:
                    pct = (
                        weighted[fname][col] / total_dmg[col] * 100.0
                        if total_dmg[col] > 0
                        else 0.0
                    )
                    row += fmt_pct(pct)
                f.write(row + "\n")
            f.write(f"# {subdiv}\n")
            tot_row = f"  {'TOTAL':<{w_fname}}{total_occ:>{w_occ}.1f}"
            for col in sensor_cols:
                tot_row += fmt_pct(100.0 if total_dmg[col] > 0 else 0.0)
            f.write(tot_row + "\n\n")

            # ── Pre-compute family weighted damage ────────────────────────────
            fam_dmg = {}
            fam_occ = {}
            for fam in family_order:
                fam_occ[fam] = sum(fatigue_files[fn] for fn in family_files[fam])
                fam_dmg[fam] = {}
                for col in sensor_cols:
                    fam_dmg[fam][col] = sum(
                        weighted[fn][col] for fn in family_files[fam]
                    )

            # ── Table 3: Damage — family averaged ─────────────────────────────
            f.write(f"# --- Table 3: Damage — Family averaged (m={m}) ---\n#\n")
            hdr_fam = f"  {'':<{w_fam}}{' OccFreq_total':>{w_occ}}"
            for col in sensor_cols:
                hdr_fam += f"{col:>{w_sensor}}"
            f.write(f"# {hdr_fam}\n")
            f.write(f"# {fam_div}\n")
            for fam in family_order:
                row = f"  {fam:<{w_fam}}{fam_occ[fam]:>{w_occ}.1f}"
                for col in sensor_cols:
                    row += fmt_val(fam_dmg[fam][col])
                f.write(row + "\n")
            f.write(f"# {fam_div}\n")
            tot_row = f"  {'TOTAL':<{w_fam}}{total_occ:>{w_occ}.1f}"
            for col in sensor_cols:
                tot_row += fmt_val(total_dmg[col])
            f.write(tot_row + "\n\n")

            # ── Table 4: Contribution% — family averaged ───────────────────────
            f.write(f"# --- Table 4: Contribution% — Family averaged (m={m}) ---\n#\n")
            f.write(f"# {hdr_fam}\n")
            f.write(f"# {fam_div}\n")
            for fam in family_order:
                row = f"  {fam:<{w_fam}}{fam_occ[fam]:>{w_occ}.1f}"
                for col in sensor_cols:
                    pct = (
                        fam_dmg[fam][col] / total_dmg[col] * 100.0
                        if total_dmg[col] > 0
                        else 0.0
                    )
                    row += fmt_pct(pct)
                f.write(row + "\n")
            f.write(f"# {fam_div}\n")
            tot_row = f"  {'TOTAL':<{w_fam}}{total_occ:>{w_occ}.1f}"
            for col in sensor_cols:
                tot_row += fmt_pct(100.0 if total_dmg[col] > 0 else 0.0)
            f.write(tot_row + "\n\n")

        f.write(f'\n# {"=" * 60}\n')
        f.write("# END OF FILE\n")
        f.write(f'# {"=" * 60}\n')
