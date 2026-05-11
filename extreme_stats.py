# =============================================================================
# extreme_stats.py
# OpenFAST Postprocessing — Extreme Statistics
# =============================================================================

import os
import numpy as np

# Ordered list of statistic names — order is preserved in all outputs
STAT_KEYS = ['Max', 'Min', 'Mean', 'Stdev', 'Range', 'AbsMax', 'RMS']


def _absmax_family_average(values, method):
    """
    Family-average a list of seed AbsMax values with sign-handling rules.

    Selection step (per FamilyMethod):
        Method 1: all N seeds
        Method 2: worst half — top ⌈N/2⌉ seeds by |value| descending
        Method 3: single worst seed by |value|

    Sign rule applied to the SELECTED subset (not the whole family for M2):
        — All selected seeds have the SAME sign  → signed mean (sign preserved)
        — Selected seeds have MIXED signs        → mean of |values| (positive)

    Returns (family_value, sign_consistent_flag) where sign_consistent_flag
    is True if the returned value carries a meaningful sign, False if it is
    a magnitude-only value resulting from mixed-sign averaging.
    """
    if not values:
        return 0.0, True
    n = len(values)

    # Step 1 — select the seeds to average
    if method == 1:
        selected = list(values)
    elif method == 2:
        # Sort by |v| descending, take worst half
        sorted_by_mag = sorted(values, key=abs, reverse=True)
        half          = max(1, n // 2)
        selected      = sorted_by_mag[:half]
    else:  # method 3 — worst single seed by magnitude (sign preserved)
        return float(max(values, key=abs)), True

    # Step 2 — sign consistency check on the SELECTED seeds
    pos = any(v > 0 for v in selected)
    neg = any(v < 0 for v in selected)

    if pos and neg:
        # Mixed signs → magnitude-only mean (positive)
        return float(np.mean([abs(v) for v in selected])), False

    # All selected seeds same sign (or all zero) → signed mean preserves sign
    return float(np.mean(selected)), True


def compute_extreme_stats(df, time_col='Time_[s]'):
    """
    Compute extreme statistics for every sensor channel in the DataFrame.

    Parameters
    ----------
    df       : pandas DataFrame from read_fast_output()
    time_col : name of the time column to exclude from computation

    Returns
    -------
    stats : dict  {sensor_col: {stat_name: float}}
        Keys in inner dict match STAT_KEYS.
    """
    stats = {}
    for col in df.columns:
        if col == time_col:
            continue
        s = df[col].values.astype(float)

        s_max   = float(np.max(s))
        s_min   = float(np.min(s))
        # AbsMax preserves sign of the larger-magnitude extreme.
        # Industry convention: report the worst load WITH its direction.
        # Example: signal range [-100, +50] -> AbsMax = -100 (not +100).
        s_absmax = s_max if abs(s_max) >= abs(s_min) else s_min

        stats[col] = {
            'Max'    : s_max,
            'Min'    : s_min,
            'Mean'   : float(np.mean(s)),
            'Stdev'  : float(np.std(s, ddof=1)),
            'Range'  : float(s_max - s_min),
            'AbsMax' : s_absmax,
            'RMS'    : float(np.sqrt(np.mean(s ** 2))),
        }
    return stats


# =============================================================================
# EXT — Family averaging and ranking
# =============================================================================

def _derive_family_name(filename):
    """
    Derive family name by stripping the last underscore-separated token
    from the filename stem.

    Examples
    --------
    'DLC11_08mps_Seed01.outb'  →  'DLC11_08mps'
    'DLC11_08mps_Az180.outb'   →  'DLC11_08mps'
    'DLC11_08mps_S3.out'       →  'DLC11_08mps'
    """
    stem = os.path.splitext(filename)[0]
    parts = stem.rsplit('_', 1)
    return parts[0] if len(parts) > 1 else stem


def compute_ext_ranking(all_extreme, file_metadata, sensor_col,
                        stat, top_n=10):
    """
    Compute EXT ranking for one sensor and one statistic.

    Steps
    -----
    1. Group files by Family number from file_metadata
    2. Apply FamilyMethod averaging within each group (raw values)
    3. Apply PLF to get factored values
    4. Rank top_n families — both with and without PLF

    Parameters
    ----------
    all_extreme   : dict {fname: {sensor: {stat: float}}}
    file_metadata : dict {fname: {Family, FamilyMethod, PLF, SimTime}}
    sensor_col    : str
    stat          : str — one of 'Max', 'Min', 'AbsMax'
    top_n         : int — number of top entries to return (default 10)

    Returns
    -------
    ranked_plf    : list of (value_plf, plf, family_name) — descending by abs value
    ranked_noplf  : list of (value_raw, family_name) — descending by abs value
    """
    import os as _os

    # ── Step 1: collect raw extreme per file ──────────────────────────────────
    # {family_num: [(raw_value, plf, family_method, family_name), ...]}
    families = {}
    for fname, meta in file_metadata.items():
        if fname not in all_extreme:
            continue
        raw_val = all_extreme[fname].get(sensor_col, {}).get(stat, None)
        if raw_val is None:
            continue
        fnum = meta['Family']
        if fnum not in families:
            families[fnum] = {
                'values'       : [],
                'plf'          : meta['PLF'],
                'method'       : meta['FamilyMethod'],
                'family_name'  : _derive_family_name(fname),
            }
        families[fnum]['values'].append(raw_val)

    # ── Step 2: apply family method averaging ────────────────────────────────
    family_results = []  # (raw_family_value, plf, family_name)

    for fnum, fdata in families.items():
        vals   = fdata['values']
        method = fdata['method']
        plf    = fdata['plf']
        name   = fdata['family_name']

        if not vals:
            continue

        if stat == 'AbsMax':
            # Special handling — sign consistency check on selected seeds
            # All same sign → signed mean (sign preserved)
            # Mixed signs   → mean of |values| (positive, magnitude-only)
            family_val, _sign_ok = _absmax_family_average(vals, method)

        else:
            # Sort direction depends on stat
            reverse = (stat == 'Max')

            if method == 1:
                # Mean of all seeds
                family_val = float(np.mean(vals))

            elif method == 2:
                # Mean of worst half
                sorted_vals = sorted(vals, reverse=reverse)
                half = max(1, len(sorted_vals) // 2)
                family_val = float(np.mean(sorted_vals[:half]))

            else:  # method == 3
                # Absolute worst seed
                family_val = max(vals) if reverse else min(vals)

        family_results.append((family_val, plf, name))

    # ── Step 3: Apply PLF and rank families ───────────────────────────────────
    # Sort key depends on stat:
    #   Max     → largest signed with-PLF value first
    #   Min     → most-negative with-PLF value first
    #   AbsMax  → largest |with-PLF| first (each family keeps its own sign)
    plf_results = [(val * plf, val, plf, name) for val, plf, name in family_results]

    if stat == 'Min':
        plf_results.sort(key=lambda x: x[0])
    elif stat == 'AbsMax':
        # Rank by magnitude — sign is preserved per row (sign-consistent
        # families show ±, mixed-sign families show +).
        plf_results.sort(key=lambda x: abs(x[0]), reverse=True)
    else:
        plf_results.sort(key=lambda x: x[0], reverse=True)

    # Both tables use top_n from the same sorted order
    top = plf_results[:top_n]
    ranked_plf   = [(val_plf, plf, name) for val_plf, raw, plf, name in top]
    ranked_noplf = [(raw,           name) for val_plf, raw, plf, name in top]

    return ranked_plf, ranked_noplf


# =============================================================================
# Complementary loads
# =============================================================================

import re as _re

def _is_moment_sensor(sensor_name):
    """
    Auto-detect moment sensors by name pattern.
    Matches OpenFAST moment channels: contains M followed by x/y/z
    e.g. RootMxc1, TwrBsMyt, LSSGagMza, YawBrMyp, LSShftMxa
    """
    return bool(_re.search(r'M[xyz]', sensor_name, _re.IGNORECASE))


def _get_group_sensors(sensor_name, sensor_list, derived_active=None):
    """
    Return all real sensors in the same group as sensor_name.
    Also handles derived sensors (Mres) as driving sensor by looking
    up their group from derived_active.
    """
    # Try real sensor first
    target_group = sensor_list.get(sensor_name, {}).get('Group', 0)

    # If not found, check derived_active (Mres as driving sensor)
    if target_group == 0 and derived_active:
        for ds in derived_active:
            if ds['name'] == sensor_name:
                target_group = ds.get('group', 0)
                break

    if target_group == 0:
        return [sensor_name]
    return [s for s, flags in sensor_list.items()
            if flags.get('Group') == target_group]


def _moment_resultant(df, moment_sensors, time_idx):
    """
    Compute moment resultant sqrt(Mx² + My² + Mz²) at a given time index.
    Returns scalar float.
    """
    total = 0.0
    for col in moment_sensors:
        if col in df.columns:
            val = float(df[col].iloc[time_idx])
            total += val ** 2
    return float(np.sqrt(total))


def compute_complementary_loads(sensor_col, stat,
                                ranked_plf, all_extreme,
                                file_metadata, sensor_list,
                                input_folder, time_channel,
                                top_n=10,
                                derived_active=None):
    """
    Compute complementary loads for top_n ranked EXT events.

    For each rank, selects the seed file using CompMethod:
      1 = seed closest to family mean extreme
      2 = worst seed
      3 = seed with highest moment resultant (falls back to 2 if no moments)

    Parameters
    ----------
    sensor_col    : str — driving sensor
    stat          : str — 'Max', 'Min', 'AbsMax'
    ranked_plf    : list of (value_plf, plf, family_name) — top N from EXT ranking
    all_extreme   : dict {fname: {sensor: {stat: float}}}
    file_metadata : dict {fname: {Family, FamilyMethod, PLF, SimTime}}
    sensor_list   : dict {sensor: flags} — full SENSOR_LIST
    input_folder  : str
    time_channel  : str
    top_n         : int
    derived_active: list of derived sensor dicts — for Mres computation

    Returns
    -------
    list of dicts, one per rank:
      {rank, family_name, filename, time_s, comp_values: {sensor: (raw, factored)}, plf}
    """
    from io_reader import read_fast_output




    # Identify if the driving sensor is a derived sensor
    driving_ds = None
    if derived_active:
        for ds in derived_active:
            if ds['name'] == sensor_col:
                driving_ds = ds
                break





    # For Mres derived sensors, get CompMethod from derived_active
    comp_method = sensor_list.get(sensor_col, {}).get('CompMethod')
    if comp_method is None and derived_active:
        for _ds in derived_active:
            if _ds['name'] == sensor_col:
                comp_method = _ds.get('compmethod', 2)
                break
    if comp_method is None:
        comp_method = 2
    group_sensors = _get_group_sensors(
        sensor_col, sensor_list, derived_active=derived_active)
    moment_sensors = [s for s in group_sensors if _is_moment_sensor(s)]

    # Fall back to method 2 if method 3 requested but no moment sensors
    if comp_method == 3 and not moment_sensors:
        print(f"    WARNING: CompMethod=3 for '{sensor_col}' but no moment sensors "
              f"in group — falling back to Method 2 (worst seed)")
        comp_method = 2

    # Build family → list of files mapping
    family_files = {}
    for fname, meta in file_metadata.items():
        fname_base = _derive_family_name(fname)
        if fname_base not in family_files:
            family_files[fname_base] = []
        family_files[fname_base].append(fname)

    results = []

    for rank_idx, (val_plf, plf, family_name) in enumerate(ranked_plf[:top_n], 1):
        files_in_family = family_files.get(family_name, [])
        if not files_in_family:
            continue

        # ── Select seed based on CompMethod ───────────────────────────────────
        if comp_method == 1:
            # Seed closest to family mean (raw value = val_plf / plf)
            family_mean_raw = val_plf / plf
            best_file = min(
                files_in_family,
                key=lambda f: abs(
                    all_extreme.get(f, {}).get(sensor_col, {}).get(stat, 0.0)
                    - family_mean_raw))

        elif comp_method == 2:
            # Worst seed
            if stat == 'Min':
                best_file = min(
                    files_in_family,
                    key=lambda f: all_extreme.get(f, {}).get(sensor_col, {}).get(stat, 0.0))
            else:
                best_file = max(
                    files_in_family,
                    key=lambda f: all_extreme.get(f, {}).get(sensor_col, {}).get(stat, 0.0))

        else:  # method == 3 — highest moment resultant
            # Need to read files to get resultant — use worst seed first to
            # get time index, then compare resultants
            best_file = None
            best_resultant = -1.0
            for fname in files_in_family:
                fpath = os.path.join(input_folder, fname)
                if not os.path.isfile(fpath):
                    continue
                try:
                    df_tmp = read_fast_output(fpath)
                    # # Find time index of extreme for this file
                    # if sensor_col not in df_tmp.columns:
                    #     continue
                    # sig = df_tmp[sensor_col].values.astype(float)



                    if driving_ds:
                        ops = driving_ds['operands']
                        if ops[0] not in df_tmp.columns or ops[1] not in df_tmp.columns:
                            continue
                        sig = np.sqrt(df_tmp[ops[0]].values.astype(float)**2 + 
                                      df_tmp[ops[1]].values.astype(float)**2)
                    else:
                        if sensor_col not in df_tmp.columns:
                            continue
                        sig = df_tmp[sensor_col].values.astype(float)





                    if stat == 'Min':
                        tidx = int(np.argmin(sig))
                    elif stat == 'AbsMax':
                        tidx = int(np.argmax(np.abs(sig)))
                    else:
                        tidx = int(np.argmax(sig))
                    res = _moment_resultant(df_tmp, moment_sensors, tidx)
                    if res > best_resultant:
                        best_resultant = res
                        best_file = fname
                except Exception:
                    continue

            if best_file is None:
                # fallback to worst seed
                best_file = max(
                    files_in_family,
                    key=lambda f: abs(
                        all_extreme.get(f, {}).get(sensor_col, {}).get(stat, 0.0)))

        # ── Read best file and find time of extreme ────────────────────────────
        fpath = os.path.join(input_folder, best_file)
        try:
            df = read_fast_output(fpath)
        except Exception as e:
            print(f"    WARNING: Could not read {best_file}: {e} — skipping rank {rank_idx}")
            continue

        # if sensor_col not in df.columns:
        #     print(f"    WARNING: Driving sensor '{sensor_col}' not found "
        #           f"in {best_file} — skipping rank {rank_idx} for _comp")
        #     continue

        # sig  = df[sensor_col].values.astype(float)


        # Handle signal extraction: Calculate if derived, otherwise read column
        if driving_ds:
            ops = driving_ds['operands']
            if ops[0] not in df.columns or ops[1] not in df.columns:
                print(f"    WARNING: Operands {ops} for derived sensor '{sensor_col}' "
                      f"not found in {best_file} — skipping rank {rank_idx}")
                continue
            # Reconstruct the derived signal on the fly
            sig = np.sqrt(df[ops[0]].values.astype(float)**2 + 
                          df[ops[1]].values.astype(float)**2)
        else:
            if sensor_col not in df.columns:
                print(f"    WARNING: Driving sensor '{sensor_col}' not found "
                      f"in {best_file} — skipping rank {rank_idx} for _comp")
                continue
            sig = df[sensor_col].values.astype(float)



        time = df[time_channel].values

        if stat == 'Min':
            tidx = int(np.argmin(sig))
        elif stat == 'AbsMax':
            tidx = int(np.argmax(np.abs(sig)))
        else:
            tidx = int(np.argmax(sig))

        time_s = float(time[tidx])

        # ── Extract complementary values at that time index ───────────────────
        comp_values = {}
        for col in group_sensors:
            if col in df.columns:
                raw_val      = float(df[col].iloc[tidx])
                factored_val = raw_val * plf
                comp_values[col] = (raw_val, factored_val)

        # ── Compute Mres derived sensors at this time instant ─────────────────
        # Finds Mres sensors in same group as driving sensor and computes
        # sqrt(op1² + op2²) at tidx using already-read df
        if derived_active:
            # Find group number of driving sensor
            drv_group = sensor_list.get(sensor_col, {}).get('Group')
            for ds in derived_active:
                if ds.get('group') != drv_group:
                    continue
                ops = ds.get('operands', [])
                if len(ops) < 2:
                    continue
                if ops[0] not in df.columns or ops[1] not in df.columns:
                    continue
                op1 = float(df[ops[0]].iloc[tidx])
                op2 = float(df[ops[1]].iloc[tidx])
                mres_raw = float(np.sqrt(op1**2 + op2**2))
                comp_values[ds['name']] = (mres_raw, mres_raw * plf)

        results.append({
            'rank'        : rank_idx,
            'family_name' : family_name,
            'filename'    : best_file,
            'time_s'      : time_s,
            'plf'         : plf,
            'comp_values' : comp_values,
            'driving_sensor': sensor_col,
        })

    return results


# =============================================================================
# Family averaging for summary_Family.sta and summary_FamilyPLF.sta
# =============================================================================

# Statistics where higher absolute value = worse
_STAT_WORST_HIGH = {'Max', 'AbsMax', 'RMS', 'Range', 'Stdev'}
# Statistics where lower value = worse
_STAT_WORST_LOW  = {'Min'}
# Statistics where higher absolute mean = worse
_STAT_WORST_ABSMEAN = {'Mean'}

# PLF is applied to these statistics only
PLF_STATS = {'Max', 'Min', 'AbsMax', 'Range', 'Mean'}


def _family_average_stat(values, stat, method):
    """
    Apply family averaging to a list of seed values for one statistic.

    Parameters
    ----------
    values : list of float — one value per seed
    stat   : str — statistic name
    method : int — 1, 2, or 3

    Returns
    -------
    float — family-averaged value

    Notes
    -----
    For AbsMax the seed values are signed (Bug #25 fix preserves the sign
    of the larger-magnitude extreme). The averaging applies a sign rule:
        — Selected seeds all same sign → signed mean (sign preserved)
        — Mixed signs                  → mean of |values| (positive only)
    See _absmax_family_average() for details.
    """
    if not values:
        return 0.0

    n = len(values)

    if stat == 'AbsMax':
        # Use shared helper — same behaviour as compute_ext_ranking
        family_val, _sign_ok = _absmax_family_average(values, method)
        return family_val

    # Determine sort direction
    if stat in _STAT_WORST_HIGH:
        sorted_vals = sorted(values, reverse=True)   # worst = highest
    elif stat in _STAT_WORST_LOW:
        sorted_vals = sorted(values, reverse=False)  # worst = lowest
    else:  # Mean — sort by absolute value descending
        sorted_vals = sorted(values, key=abs, reverse=True)

    if method == 1:
        return float(np.mean(sorted_vals))
    elif method == 2:
        half = max(1, n // 2)
        return float(np.mean(sorted_vals[:half]))
    else:  # method == 3
        return float(sorted_vals[0])


def _family_average_del(del_values, m, method):
    """
    Apply power mean family averaging to 1Hz DEL values.

    Formula: DEL_family = ( Σ(DEL_seedᵢ^m) / N_effective )^(1/m)

    Method 1: N_effective = all seeds
    Method 2: N_effective = worst half (sorted by DEL descending)
    Method 3: N_effective = 1 (worst single seed)

    Parameters
    ----------
    del_values : list of float — DEL per seed
    m          : int/float — Wöhler slope
    method     : int — 1, 2, or 3

    Returns
    -------
    float — family-averaged DEL
    """
    if not del_values:
        return 0.0

    sorted_dels = sorted(del_values, reverse=True)  # worst = highest DEL
    n = len(sorted_dels)

    if method == 1:
        selected = sorted_dels
    elif method == 2:
        half = max(1, n // 2)
        selected = sorted_dels[:half]
    else:  # method == 3
        selected = sorted_dels[:1]

    n_eff = len(selected)
    damage = sum(d ** m for d in selected)
    if damage <= 0 or n_eff <= 0:
        return 0.0
    return float((damage / n_eff) ** (1.0 / m))


def compute_family_stats(all_extreme, all_del, file_metadata,
                         fatigue_files, sensor_cols, m_values):
    """
    Compute family-averaged statistics and 1Hz DEL for summary_Family.sta
    and summary_FamilyPLF.sta.

    Parameters
    ----------
    all_extreme   : dict {fname: {sensor: {stat: float}}}
    all_del       : dict {fname: {sensor: {m: float}}}
    file_metadata : dict {fname: {Family, FamilyMethod, PLF, SimTime}}
    fatigue_files : dict {fname: occurrences} — used to get ordered file list
    sensor_cols   : list of str
    m_values      : list of int/float

    Returns
    -------
    family_order    : list of str — family names in LC_PostProcess.txt order
    family_extreme  : dict {family_name: {sensor: {stat: float}}}
    family_del      : dict {family_name: {sensor: {m: float}}}
    family_plf      : dict {family_name: float} — PLF per family
    family_method   : dict {family_name: int} — FamilyMethod per family
    n_seeds         : dict {family_name: int} — seed count per family
    """
    from extreme_stats import _derive_family_name

    # ── Build ordered family structure ────────────────────────────────────────
    family_order  = []
    family_files  = {}   # {family_name: [fname, ...]}
    family_plf    = {}
    family_method = {}

    for fname in fatigue_files:
        meta     = file_metadata.get(fname, {})
        fam_name = _derive_family_name(fname)
        if fam_name not in family_files:
            family_order.append(fam_name)
            family_files[fam_name]  = []
            family_plf[fam_name]    = meta.get('PLF', 1.0)
            family_method[fam_name] = meta.get('FamilyMethod', 1)
        family_files[fam_name].append(fname)

    n_seeds = {fam: len(family_files[fam]) for fam in family_order}

    # ── Compute family averaged extreme stats ─────────────────────────────────
    family_extreme = {}
    for fam in family_order:
        method = family_method[fam]
        family_extreme[fam] = {}
        for col in sensor_cols:
            family_extreme[fam][col] = {}
            for stat in STAT_KEYS:
                vals = [all_extreme.get(f, {}).get(col, {}).get(stat, 0.0)
                        for f in family_files[fam]]
                family_extreme[fam][col][stat] = _family_average_stat(
                    vals, stat, method)

    # ── Compute family averaged 1Hz DEL (power mean) ──────────────────────────
    family_del = {}
    for fam in family_order:
        method = family_method[fam]
        family_del[fam] = {}
        for col in sensor_cols:
            family_del[fam][col] = {}
            for m in m_values:
                del_vals = [all_del.get(f, {}).get(col, {}).get(m, 0.0)
                            for f in family_files[fam]]
                family_del[fam][col][m] = _family_average_del(
                    del_vals, m, method)

    return family_order, family_extreme, family_del, family_plf, family_method, n_seeds


# =============================================================================
# Derived sensor computation
# =============================================================================

def compute_derived_channel(df, derived_sensor):
    """
    Compute a derived sensor time series from real sensor columns.

    Currently supports sqrt(A^2 + B^2) formula only.

    Parameters
    ----------
    df             : pd.DataFrame — time series data for one file
    derived_sensor : dict — from get_derived_sensors()

    Returns
    -------
    np.ndarray or None — derived time series, or None if any operand missing
    """
    operands = derived_sensor['operands']
    missing  = [op for op in operands if op not in df.columns]
    if missing:
        return None, missing

    # Build and evaluate formula safely
    # Only support sqrt(A^2 + B^2) pattern
    # Extract the two operand arrays
    if len(operands) == 2:
        a = df[operands[0]].values.astype(float)
        b = df[operands[1]].values.astype(float)
        result = np.sqrt(a**2 + b**2)
        return result, []
    else:
        return None, [f"Unsupported formula with {len(operands)} operands"]
