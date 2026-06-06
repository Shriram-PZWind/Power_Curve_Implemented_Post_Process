# =============================================================================
# fatigue_stats.py
# OpenFAST Postprocessing — Fatigue Statistics
#
# Provides:
#   determine_bin_ranges   — global pass over fatigue DLCs to set bin edges
#   compute_del            — 1Hz DEL for one signal, one m value
#   compute_del_all_m      — DEL for all sensors and all m values in one file
#   compute_rfc_spectrum   — 1D RFC cycle count per range bin
#   compute_markov_matrix  — 2D Markov matrix (min-level × max-level bins)
#   compute_ldd_lrd        — Level Duration / Revolution Distribution
#   compute_lifetime_del   — 20yr lifetime DEL from accumulated RFC spectrum
# =============================================================================

import numpy as np
from tqdm import tqdm

try:
    import rainflow
except ImportError:
    raise ImportError(
        "rainflow not found.\n"
        "Install with:  pip install rainflow"
    )

from io_reader import read_fast_output


# ─────────────────────────────────────────────────────────────────────────────
# Internal helper: raw rainflow cycle extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_cycles(signal):
    """
    Run ASTM E1049-85 rainflow counting on a 1-D signal array.
    Returns list of (range, mean, count) tuples.
    Half-cycles have count = 0.5.
    """
    return [(rng, mean, count)
            for rng, mean, count, *_ in rainflow.extract_cycles(signal)]


# ─────────────────────────────────────────────────────────────────────────────
# Global bin range determination (first pass over fatigue DLCs)
# ─────────────────────────────────────────────────────────────────────────────

def determine_bin_ranges(fatigue_file_paths, n_bins,
                         time_col='Time_[s]',
                         rotor_col='RotSpeed_[rpm]',
                         sensor_filter=None):
    """
    First pass over all fatigue DLC files to determine global bin edges and
    total simulation time.

    Bin conventions
    ---------------
    RFC bins   : 0  →  global maximum RFC cycle range   (from actual rainflow)
    Level bins : global signal min  →  global signal max  (used for Markov/LDD/LRD)

    Parameters
    ----------
    fatigue_file_paths : list of str — full paths to fatigue DLC files
    n_bins             : int — number of bins (50)
    time_col           : str — time channel name
    rotor_col          : str — rotor speed channel name (excluded from sensor bins)
    sensor_filter      : list of str or None — if provided, only compute bin edges
                         for these sensors. If None, compute for all sensors.

    Returns
    -------
    bin_edges_rfc   : dict {sensor_col: np.ndarray (n_bins+1,)}
    bin_edges_level : dict {sensor_col: np.ndarray (n_bins+1,)}
    T_total_sim     : float — sum of durations of all fatigue DLC files (s)
    """
    global_max_rfc = {}   # maximum RFC cycle range per sensor
    global_min     = {}   # global signal minimum per sensor
    global_max     = {}   # global signal maximum per sensor
    T_total_sim    = 0.0

    skip_cols = {time_col, rotor_col}
    sensor_set = set(sensor_filter) if sensor_filter is not None else None

    print("  [bin range scan]")
    # for fpath in fatigue_file_paths:
    #     print(f"    scanning: {fpath}")
    #     try:
    #         df = read_fast_output(fpath)
    #     except Exception as e:
    #         print(f"    WARNING: could not read {fpath}: {e} — skipping")
    #         continue
    for fpath in tqdm(fatigue_file_paths, desc="Scanning files", unit="file"):
        try:
            df = read_fast_output(fpath)
        except Exception as e:
            # Use tqdm.write instead of print so it doesn't corrupt the loading bar
            tqdm.write(f"    WARNING: could not read {fpath}")

        time = df[time_col].values
        T_total_sim += float(time[-1] - time[0])

        for col in df.columns:
            if col in skip_cols:
                continue
            if sensor_set is not None and col not in sensor_set:
                continue
            s = df[col].values.astype(float)

            # Signal range bounds (for Markov / LDD / LRD)
            s_min = float(np.min(s))
            s_max = float(np.max(s))

            # Actual maximum RFC cycle range for this file / sensor
            cycles  = _extract_cycles(s)
            max_rfc = max((c[0] for c in cycles), default=float(s_max - s_min))

            if col not in global_max_rfc:
                global_max_rfc[col] = max_rfc
                global_min[col]     = s_min
                global_max[col]     = s_max
            else:
                global_max_rfc[col] = max(global_max_rfc[col], max_rfc)
                global_min[col]     = min(global_min[col],     s_min)
                global_max[col]     = max(global_max[col],     s_max)

    # Build edge arrays ─────────────────────────────────────────────────────
    bin_edges_rfc   = {}
    bin_edges_level = {}

    for col in global_max_rfc:
        max_rfc = global_max_rfc[col]
        g_min   = global_min[col]
        g_max   = global_max[col]

        # RFC: 0 → max_rfc  (slight upper buffer to catch edge cycles)
        upper_rfc = max_rfc * 1.001 if max_rfc > 0 else 1.0
        bin_edges_rfc[col] = np.linspace(0.0, upper_rfc, n_bins + 1)

        # Level: g_min → g_max  (slight lower/upper buffer)
        span = g_max - g_min
        buf  = span * 0.001 if span > 0 else 1e-6
        bin_edges_level[col] = np.linspace(g_min - buf, g_max + buf, n_bins + 1)

    return bin_edges_rfc, bin_edges_level, T_total_sim


# ─────────────────────────────────────────────────────────────────────────────
# DEL (Damage Equivalent Load)
# ─────────────────────────────────────────────────────────────────────────────

def compute_del(signal, T_sim, m):
    """
    Compute 1Hz Damage Equivalent Load (DEL) for a single signal.

    Formula
    -------
    DEL = ( Σ(n_i · L_i^m) / Neq )^(1/m)
    where Neq = T_sim  (one reference cycle per second → 1 Hz equivalent)

    Parameters
    ----------
    signal : np.ndarray — load time series
    T_sim  : float      — simulation duration (s)
    m      : int/float  — Wöhler slope (cast to float internally)

    Returns
    -------
    float — DEL value in the same units as the input signal
    """
    m = float(m)  # ensure float
    cycles = _extract_cycles(signal)
    if not cycles or T_sim <= 0:
        return 0.0
    ranges = np.array([c[0] for c in cycles])
    counts = np.array([c[2] for c in cycles])
    damage = float(np.sum(counts * ranges ** m))
    if damage <= 0:
        return 0.0
    return float((damage / T_sim) ** (1.0 / m))


# def compute_del_all_m(df, m_values, time_col='Time_[s]'):
#     """
#     Compute 1Hz DEL for every sensor channel and every Wöhler slope value.

#     Parameters
#     ----------
#     df       : pandas DataFrame
#     m_values : list of float — Wöhler slopes
#     time_col : str

#     Returns
#     -------
#     del_stats : dict  {sensor_col: {m: float}}
#     """
#     time   = df[time_col].values
#     T_sim  = float(time[-1] - time[0])
#     result = {}
#     for col in df.columns:
#         if col == time_col:
#             continue
#         signal = df[col].values.astype(float)
#         result[col] = {m: compute_del(signal, T_sim, m) for m in m_values}
#     return result

def compute_del_all_m(df, m_values, time_col='Time_[s]'):
    """
    Compute 1Hz DEL for every sensor channel and every Wöhler slope value.
    Optimised: Extracts cycles and builds typed arrays ONLY ONCE per signal.
    """
    time = df[time_col].values
    T_sim = float(time[-1] - time[0])
    result = {}
   
    for col in df.columns:
        if col == time_col:
            continue
           
        # Using typed numpy array for speed
        signal = df[col].values.astype(float)
       
        # 1. EXTRACT CYCLES ONCE PER SIGNAL (Massive algorithmic speedup)
        cycles = _extract_cycles(signal)
       
        if not cycles or T_sim <= 0:
            result[col] = {m: 0.0 for m in m_values}
            continue
           
        # 2. CREATE TYPED ARRAYS ONCE
        ranges = np.array([c[0] for c in cycles], dtype=float)
        counts = np.array([c[2] for c in cycles], dtype=float)
       
        # 3. COMPUTE ALL 'm' SLOPES VECTORISED
        res_m = {}
        for m in m_values:
            m_f = float(m)
            damage = float(np.sum(counts * (ranges ** m_f)))
            if damage <= 0:
                res_m[m] = 0.0
            else:
                res_m[m] = float((damage / T_sim) ** (1.0 / m_f))
               
        result[col] = res_m
       
    return result
 


# ─────────────────────────────────────────────────────────────────────────────
# RFC Spectrum (1D)
# ─────────────────────────────────────────────────────────────────────────────

# def compute_rfc_spectrum(signal, bin_edges_rfc):
#     """
#     Compute 1D RFC spectrum: total cycle count per range bin.

#     Parameters
#     ----------
#     signal       : np.ndarray — load time series
#     bin_edges_rfc: np.ndarray (n_bins+1,) — range bin edges starting at 0

#     Returns
#     -------
#     counts : np.ndarray (n_bins,) — cycle counts per range bin
#     """
#     n      = len(bin_edges_rfc) - 1
#     counts = np.zeros(n, dtype=float)
#     for rng, _mean, count in _extract_cycles(signal):
#         idx = int(np.clip(
#             np.searchsorted(bin_edges_rfc, rng, side='right') - 1,
#             0, n - 1))
#         counts[idx] += count
#     return counts


# # ─────────────────────────────────────────────────────────────────────────────
# # Markov Matrix (2D)
# # ─────────────────────────────────────────────────────────────────────────────

# def compute_markov_matrix(signal, bin_edges_range, bin_edges_mean):
#     """
#     Compute 2D Markov matrix (Convention 2): rows = cycle range bins,
#     columns = cycle mean bins.

#     For each RFC cycle:
#       range = max - min  (binned on rows)
#       mean  = (max + min) / 2  (binned on columns)

#     Parameters
#     ----------
#     signal          : np.ndarray — load time series
#     bin_edges_range : np.ndarray (n_bins+1,) — range bin edges (0 → global max range)
#     bin_edges_mean  : np.ndarray (n_bins+1,) — mean bin edges (global min → global max)

#     Returns
#     -------
#     matrix : np.ndarray (n_range_bins, n_mean_bins) — cycle counts
#     """
#     n_range = len(bin_edges_range) - 1
#     n_mean  = len(bin_edges_mean)  - 1
#     matrix  = np.zeros((n_range, n_mean), dtype=float)
#     for rng, mean, count in _extract_cycles(signal):
#         i_range = int(np.clip(
#             np.searchsorted(bin_edges_range, rng,  side='right') - 1, 0, n_range - 1))
#         i_mean  = int(np.clip(
#             np.searchsorted(bin_edges_mean,  mean, side='right') - 1, 0, n_mean  - 1))
#         matrix[i_range, i_mean] += count
#     return matrix

def compute_rfc_spectrum(signal, bin_edges_rfc):
    """
    Compute 1D RFC spectrum: total cycle count per range bin (Vectorized).
    """
    n = len(bin_edges_rfc) - 1
    counts = np.zeros(n, dtype=float)
    
    # Extract all cycles at once
    cycles = _extract_cycles(signal)
    if not cycles:
        return counts
        
    # Convert to a single 2D numpy array: columns are [range, mean, count]
    cycles_arr = np.array(cycles)
    ranges = cycles_arr[:, 0]
    weights = cycles_arr[:, 2]
    
    # Vectorized bin index calculation matching your exact clipping logic
    idx = np.clip(
        np.searchsorted(bin_edges_rfc, ranges, side='right') - 1, 
        0, n - 1
    ).astype(int)
    
    # Instantaneous un-looped accumulation
    np.add.at(counts, idx, weights)
    return counts


def compute_markov_matrix(signal, bin_edges_range, bin_edges_mean):
    """
    Compute 2D Markov matrix: rows = cycle range bins, columns = cycle mean bins (Vectorized).
    """
    n_range = len(bin_edges_range) - 1
    n_mean  = len(bin_edges_mean)  - 1
    matrix  = np.zeros((n_range, n_mean), dtype=float)
    
    # Extract all cycles at once
    cycles = _extract_cycles(signal)
    if not cycles:
        return matrix
        
    cycles_arr = np.array(cycles)
    ranges  = cycles_arr[:, 0]
    means   = cycles_arr[:, 1]
    weights = cycles_arr[:, 2]
    
    # Vectorized calculation for both dimensions matching your exact logic
    i_range = np.clip(
        np.searchsorted(bin_edges_range, ranges, side='right') - 1, 
        0, n_range - 1
    ).astype(int)
    
    i_mean = np.clip(
        np.searchsorted(bin_edges_mean, means, side='right') - 1, 
        0, n_mean - 1
    ).astype(int)
    
    # Instantaneous multi-dimensional un-looped accumulation
    np.add.at(matrix, (i_range, i_mean), weights)
    return matrix


# ─────────────────────────────────────────────────────────────────────────────
# LDD / LRD (Level Duration & Revolution Distribution)
# ─────────────────────────────────────────────────────────────────────────────

def compute_ldd_lrd(time, signal, rotor_speed_rpm, bin_edges_level):
    """
    Compute Level Duration Distribution (LDD) and Level Revolution Distribution
    (LRD) simultaneously.

    For each time-step interval the midpoint signal value determines the load
    level bin; the interval duration (dt) and revolutions (rpm/60 · dt) are
    accumulated into that bin.

    Both LDD and LRD share the same load-level bins and the same two output
    arrays:
      - time_at_level  : cumulative time (s) spent in each level bin
      - revs_at_level  : cumulative revolutions spent in each level bin

    LDD emphasises the time column; LRD emphasises the revolution column.
    Both arrays are written to their respective output files.

    Parameters
    ----------
    time            : np.ndarray (N,) — time vector (s)
    signal          : np.ndarray (N,) — load time series
    rotor_speed_rpm : np.ndarray (N,) — rotor speed (rpm)
    bin_edges_level : np.ndarray (n_bins+1,) — level bin edges

    Returns
    -------
    time_at_level : np.ndarray (n_bins,) — cumulative time (s) per level bin
    revs_at_level : np.ndarray (n_bins,) — cumulative revolutions per level bin
    """
    n             = len(bin_edges_level) - 1
    time_at_level = np.zeros(n, dtype=float)
    revs_at_level = np.zeros(n, dtype=float)

    dt      = np.diff(time)
    # Use midpoint values for better accuracy
    sig_mid = 0.5 * (signal[:-1]          + signal[1:])
    rpm_mid = 0.5 * (rotor_speed_rpm[:-1] + rotor_speed_rpm[1:])

    # Vectorised bin assignment
    idx = np.clip(
        np.searchsorted(bin_edges_level, sig_mid, side='right') - 1,
        0, n - 1).astype(int)

    np.add.at(time_at_level, idx, dt)
    np.add.at(revs_at_level, idx, rpm_mid / 60.0 * dt)

    return time_at_level, revs_at_level


# ─────────────────────────────────────────────────────────────────────────────
# Lifetime DEL (from 20yr-scaled RFC spectrum)
# ─────────────────────────────────────────────────────────────────────────────

def compute_lifetime_del(rfc_accum, bin_edges_rfc, m_values, Neq_life):
    """
    Compute lifetime DEL per sensor per Wöhler slope from the accumulated
    RFC spectrum.

    Formula
    -------
    DEL_life = ( Σ(n_i_life · L_i^m) / Neq_life )^(1/m)
    where L_i = bin centre values of the RFC range spectrum.

    Parameters
    ----------
    rfc_accum     : dict {sensor_col: np.ndarray (n_bins,)} — lifetime-scaled counts
    bin_edges_rfc : dict {sensor_col: np.ndarray (n_bins+1,)} — range bin edges
    m_values      : list of float
    Neq_life      : float — reference cycle count for lifetime DEL (e.g. 1e7)

    Returns
    -------
    lifetime_del : dict {sensor_col: {m: float}}
    """

    if isinstance(rfc_accum, np.ndarray):
        rfc_accum = {'default': rfc_accum}
        bin_edges_rfc = {'default': bin_edges_rfc}
    
    
    lifetime_del = {}

    for col, counts in rfc_accum.items():
        edges       = bin_edges_rfc[col]
        bin_centers = 0.5 * (edges[:-1] + edges[1:])
        lifetime_del[col] = {}
        for m in m_values:
            damage = float(np.sum(counts * bin_centers ** m))
            if damage <= 0 or Neq_life <= 0:
                lifetime_del[col][m] = 0.0
            else:
                lifetime_del[col][m] = float((damage / Neq_life) ** (1.0 / m))

    return lifetime_del


# ─────────────────────────────────────────────────────────────────────────────
# Lifetime DEL from LDD (Level Duration Distribution)
# ─────────────────────────────────────────────────────────────────────────────

def compute_lifetime_del_ldd(ldd_time, bin_edges_level, m_values, Neq_time):
    """
    Compute lifetime DEL from LDD (Level Duration Distribution).

    Converts accumulated time at each load level to equivalent cycles using
    the 1Hz assumption (1 cycle per second), then applies the DEL formula.

    Formula
    -------
    nᵢ       = time_at_level_i  (seconds → cycles at 1Hz)
    DEL_life = ( Σ(nᵢ · Lᵢᵐ) / Neq_time )^(1/m)
    where Lᵢ = level bin centre values

    Parameters
    ----------
    ldd_time       : np.ndarray (n_bins,) — lifetime accumulated time (s) per level bin
    bin_edges_level: np.ndarray (n_bins+1,) — level bin edges
    m_values       : list of float — Wöhler slopes
    Neq_time       : float — reference cycle count = full lifetime in seconds

    Returns
    -------
    del_ldd : dict {m: float}
    """
    bin_centers = 0.5 * (bin_edges_level[:-1] + bin_edges_level[1:])
    del_ldd = {}
    for m in m_values:
        # time_at_level used directly as cycle count (1Hz assumption)
        damage = float(np.sum(ldd_time * np.abs(bin_centers) ** m))
        if damage <= 0 or Neq_time <= 0:
            del_ldd[m] = 0.0
        else:
            del_ldd[m] = float((damage / Neq_time) ** (1.0 / m))
    return del_ldd


# ─────────────────────────────────────────────────────────────────────────────
# Lifetime DEL from LRD (Level Revolution Distribution)
# ─────────────────────────────────────────────────────────────────────────────

def compute_lifetime_del_lrd(lrd_revs, bin_edges_level, m_values, Neq_rev):
    """
    Compute lifetime DEL from LRD (Level Revolution Distribution).

    Uses accumulated rotor revolutions at each load level as the cycle count,
    suitable for rotating components (blade root, main shaft).

    Formula
    -------
    nᵢ       = revolutions_at_level_i
    DEL_life = ( Σ(nᵢ · Lᵢᵐ) / Neq_rev )^(1/m)
    where Lᵢ = level bin centre values

    Parameters
    ----------
    lrd_revs       : np.ndarray (n_bins,) — lifetime accumulated revolutions per level bin
    bin_edges_level: np.ndarray (n_bins+1,) — level bin edges
    m_values       : list of float — Wöhler slopes
    Neq_rev        : float — reference revolution count (e.g. 1e8)

    Returns
    -------
    del_lrd : dict {m: float}
    """
    bin_centers = 0.5 * (bin_edges_level[:-1] + bin_edges_level[1:])
    del_lrd = {}
    for m in m_values:
        damage = float(np.sum(lrd_revs * np.abs(bin_centers) ** m))
        if damage <= 0 or Neq_rev <= 0:
            del_lrd[m] = 0.0
        else:
            del_lrd[m] = float((damage / Neq_rev) ** (1.0 / m))
    return del_lrd


# =============================================================================
# Per-file damage scalar (for DLC contribution analysis)
# =============================================================================

def compute_file_damage(signal, m_values):
    """
    Compute raw damage sum per Wöhler slope from a single signal using
    raw rainflow cycles (no binning). Used for DLC contribution analysis.

    Formula
    -------
    damage[m] = Σᵢ(nᵢ · Lᵢᵐ)
    where Lᵢ = rainflow cycle range, nᵢ = cycle count

    Parameters
    ----------
    signal   : np.ndarray — load time series
    m_values : list of float — Wöhler slopes

    Returns
    -------
    dict {m: float} — raw damage sum per slope
    """
    cycles = _extract_cycles(signal)
    if not cycles:
        return {m: 0.0 for m in m_values}
    ranges = np.array([c[0] for c in cycles])
    counts = np.array([c[2] for c in cycles])
    return {m: float(np.sum(counts * ranges ** m)) for m in m_values}
