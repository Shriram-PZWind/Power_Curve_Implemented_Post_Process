# =============================================================================
# main.py
# OpenFAST Postprocessing — Pipeline Orchestration
#
# Pipeline phases
# ---------------
#  0  Setup: create output subfolders (STA, FAT), discover input files
#  1  Read sensor list from the first file
#  2  Global scan of fatigue DLCs → bin edges + T_total_sim
#  3  Initialise fatigue accumulation arrays
#  4  Main loop (all files):
#       read → extreme stats → DEL → write .sta to STA/ → update summary.sta
#       (fatigue DLCs only) → accumulate RFC / Markov / LDD / LRD
#  5  Scale accumulators to 20-year lifetime
#  6  Compute 20-year lifetime DEL
#  7  Write per-sensor fatigue files to FAT/ (.rfc .markov .ldd .lrd)
#  8  Final write of summary.sta
#  9  EXT ranking + complementary loads
# 10  DLC fatigue contribution analysis
# 11  Component load summary files (.sum)
# =============================================================================

import os
import sys
import numpy as np
from tqdm import tqdm
from logger import PostProcessLogger
log = PostProcessLogger("main files")

import config
from config       import (get_sensor_flags, get_blade_config, get_hub_config,
                          get_derived_sensors, get_tower_config, get_yaw_config,
                          get_drivetrain_config, get_foundation_config,
                          get_pitch_bearing_config, get_tower_clearance_config,
                          validate_sum_slopes, LOG_FILE, fmt_slope)
from logger        import PostProcessLogger
from main_loads_writer import write_main_loads_sum
from blade_reader import detect_blade_sensors, read_radial_positions
from sum_writer   import (write_bld_loads_sum, write_hub_loads_sum,
                          write_twr_loads_sum, write_yaw_loads_sum,
                          write_drt_loads_sum, write_fnd_loads_sum,
                          write_pitch_bearing_sum)
from io_reader      import (get_all_output_files, read_fast_output,
                             get_sensor_columns, sanitize_for_filename)
from extreme_stats  import (compute_extreme_stats, compute_ext_ranking,
                             compute_complementary_loads,
                             compute_family_stats, PLF_STATS,
                             compute_derived_channel)
from fatigue_stats  import (determine_bin_ranges, compute_del_all_m,
                             compute_rfc_spectrum, compute_markov_matrix,
                             compute_ldd_lrd, compute_lifetime_del,
                             compute_lifetime_del_ldd, compute_lifetime_del_lrd,
                             compute_file_damage)
from output_writer  import (write_per_file_sta, write_summary_sta,
                             write_rfc_file, write_markov_file,
                             write_ldd_file, write_lrd_file,
                             write_ext_file, write_complementary_file,
                             write_contribution_file)
from PowerCurve import(MultiDensityPowerCalculator)
from merge_All_pc_files import(merge_power_curve_files)

# Force underlying linear algebra libraries to single-thread to prevent multi-core lockups
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import numpy as np
import rainflow

# ── 100% ACCURATE ACCELERATED RAINFLOW INTERFACE ────────────────────────────
# def _extract_cycles_fast(signal):
#     """
#     Blazing fast pre-filter using NumPy to extract strict reversals (peaks/valleys).
#     Feeds the highly compressed array to the official rainflow package to guarantee 
#     100% exact numerical matching with your original results.
#     """
#     # 1. Eliminate consecutive duplicates instantly
#     mask = np.empty(len(signal), dtype=np.bool_)
#     mask[0] = True
#     mask[1:] = (signal[1:] != signal[:-1])
#     filtered = signal[mask]
    
#     if len(filtered) >= 3:
#         # 2. Identify turning points where direction changes sign
#         signs = np.sign(np.diff(filtered))
#         turning_idx = np.where(signs[1:] != signs[:-1])[0] + 1
#         keep_idx = np.concatenate(([0], turning_idx, [len(filtered) - 1]))
#         reversals = filtered[keep_idx]
#     else:
#         reversals = filtered

#     # 3. Process via official library for precise ASTM compliance
#     return [(rng, mean, count) for rng, mean, count, *_ in rainflow.extract_cycles(reversals)]


# # ── HIGH-SPEED MULTI-CORE WORKER ENGINE ─────────────────────────────────────
# def fast_turbo_worker_phase4(fpath, is_fatigue, occ_k, config_data, bin_edges_rfc, 
#                              bin_edges_level, derived_bin_edges, sensor_cols, 
#                              sensor_cols_all, derived_active, fatigue_sensors):
#     from io_reader import read_fast_output
#     from extreme_stats import compute_extreme_stats, compute_derived_channel
#     from output_writer import write_per_file_sta
#     from config import get_sensor_flags

#     fname = os.path.basename(fpath)
#     stem = os.path.splitext(fname)[0]
    
#     try:
#         df = read_fast_output(fpath)
#     except Exception as e:
#         return {'fname': fname, 'error': str(e)}

#     time = df[config_data['TIME_CHANNEL']].values
#     t_sim = float(time[-1] - time[0])

#     # Compute extreme summary statistics
#     ext_stats = compute_extreme_stats(df, config_data['TIME_CHANNEL'])
    
#     # Fast calculated DEL and Cached Rainflow outputs
#     del_stats = {}
#     cached_cycles = {}
    
#     for col in sensor_cols:
#         if col not in df.columns: continue
#         sig = df[col].values.astype(float)
        
#         # Call the exact pre-filtered rainflow routine
#         cycles = _extract_cycles_fast(sig)
#         cached_cycles[col] = cycles
        
#         # Calculate 1Hz DEL values instantly via vector math
#         del_stats[col] = {}
#         for m in config_data['RFC_M_VALUES']:
#             dmg_sum = sum(count * (rng ** m) for rng, mean, count in cycles)
#             del_stats[col][m] = float((dmg_sum / t_sim) ** (1.0 / m)) if dmg_sum > 0 else 0.0

#     # Process Derived Channels once
#     derived_extreme = {}
#     derived_del = {}
#     derived_signals = {}
    
#     if derived_active:
#         for ds in derived_active:
#             dn = ds['name']
#             sig, _ = compute_derived_channel(df, ds)
#             if sig is None: continue
#             derived_signals[dn] = sig
            
#             if ds.get('sta', 1):
#                 smax, smin = float(np.max(sig)), float(np.min(sig))
#                 derived_extreme[dn] = {
#                     'Max': smax, 'Min': smin, 'Mean': float(np.mean(sig)),
#                     'Stdev': float(np.std(sig, ddof=1)), 'Range': smax - smin,
#                     'AbsMax': smax if abs(smax) >= abs(smin) else smin,
#                     'RMS': float(np.sqrt(np.mean(sig**2)))
#                 }
#             if ds.get('1hzeq', 1):
#                 dcycles = _extract_cycles_fast(sig)
#                 derived_del[dn] = {}
#                 for m in config_data['RFC_M_VALUES']:
#                     ddmg = sum(dcnt * (drng ** m) for drng, dmean, dcnt in dcycles)
#                     derived_del[dn][m] = float((ddmg / t_sim) ** (1.0 / m)) if ddmg > 0 else 0.0

#     # Write independent .sta files directly from inside the worker core
#     ext_stats_all = {**ext_stats, **derived_extreme}
#     del_stats_all = {**del_stats, **derived_del}
#     sta_path = os.path.join(config_data['STA_FOLDER'], stem + '.sta')
#     write_per_file_sta(sta_path, sensor_cols_all, ext_stats_all, del_stats_all, config_data['RFC_M_VALUES'])

#     # Compile Fatigue Distribution Data Maps
#     fatigue_payload = {}
#     if is_fatigue:
#         from fatigue_stats import compute_rfc_spectrum, compute_markov_matrix, compute_ldd_lrd
#         rs_chan = config_data['ROTOR_SPEED_CHANNEL']
#         rotor_speed = df[rs_chan].values.astype(float) if rs_chan in df.columns else np.zeros(len(time))

#         rfc_up, mkv_up, ldd_lrd_up, dmg_up = {}, {}, {}, {}
#         for col in fatigue_sensors:
#             if col not in df.columns: continue
#             sig = df[col].values.astype(float)
#             flags = get_sensor_flags(col)
            
#             cycles = cached_cycles[col]
#             rngs = np.array([c[0] for c in cycles])
#             w_counts = np.array([c[2] for c in cycles])
            
#             if flags['RFC']:
#                 if len(rngs) > 0:
#                     counts, _ = np.histogram(rngs, bins=bin_edges_rfc[col], weights=w_counts)
#                 else:
#                     counts = np.zeros(len(bin_edges_rfc[col]) - 1)
#                 rfc_up[col] = counts * occ_k
#                 dmg_up[col] = {m: float(sum(c[2] * (c[0] ** m) for c in cycles)) for m in config_data['RFC_M_VALUES']}
#             if flags['Markov']:
#                 mkv_up[col] = compute_markov_matrix(sig, bin_edges_rfc[col], bin_edges_level[col]) * occ_k
#             if flags['LDD'] or flags['LRD']:
#                 t_lvl, r_lvl = compute_ldd_lrd(time, sig, rotor_speed, bin_edges_level[col])
#                 ldd_lrd_up[col] = (t_lvl * occ_k, r_lvl * occ_k)

#         # Derived Sensor Fatigue Allocations
#         # drfc_up, dmkv_up, dldd_lrd_up = {}, {}, {}
#         # for ds in derived_active:
#         #     dn = ds['name']
#         #     if dn not in derived_signals or occ_k <= 0: continue
#         #     sig = derived_signals[dn]
#         #     be = derived_bin_edges.get(dn, {})
            
#         #     if (ds['rfc'] or ds['markov']) and 'rfc' in be:
#         #         dcycles = _extract_cycles_fast(sig)
#         #         drngs = np.array([c[0] for c in dcycles])
#         #         dw_counts = np.array([c[2] for c in dcycles])
#         #         if ds['rfc']:
#         #             if len(drngs) > 0:
#         #                 counts, _ = np.histogram(drngs, bins=be['rfc'], weights=dw_counts)
#         #             else:
#         #                 counts = np.zeros(len(be['rfc']) - 1)
#         #             drfc_up[dn] = counts * occ_k
#         #         if ds['markov']:
#         #             dmkv_up[dn] = compute_markov_matrix(sig, be['rfc'], be['level']) * occ_k
#         #     if (ds['ldd'] or ds['lrd']) and 'level' in be:
#         #         t_lvl, r_lvl = compute_ldd_lrd(time, sig, rotor_speed, be['level'])
#         #         dldd_lrd_up[dn] = (t_lvl * occ_k, r_lvl * occ_k)

#         # fatigue_payload = {
#         #     'rfc': rfc_up, 'markov': mkv_up, 'ldd_lrd': ldd_lrd_up, 'dmg': dmg_up,
#         #     'drfc': drfc_up, 'dmarkov': dmkv_up, 'dldd_lrd': dldd_lrd_up
#         # }

#         # Derived Sensor Fatigue Allocations
#         drfc_up, dmkv_up, dldd_lrd_up = {}, {}, {}
#         ddmg_up = {}  # FIX 2: Initialize raw exact damage dictionary for derived sensors
        
#         for ds in derived_active:
#             dn = ds['name']
#             if dn not in derived_signals or occ_k <= 0: continue
#             sig = derived_signals[dn]
#             be = derived_bin_edges.get(dn, {})
            
#             if (ds['rfc'] or ds['markov']) and 'rfc' in be:
#                 dcycles = _extract_cycles_fast(sig)
#                 drngs = np.array([c[0] for c in dcycles])
#                 dw_counts = np.array([c[2] for c in dcycles])
                
#                 if ds['rfc']:
#                     if len(drngs) > 0:
#                         # FIX 1: Clip ranges to the absolute min/max bin edges. 
#                         # This guarantees NO cycles are dropped due to float precision limits.
#                         drngs_clipped = np.clip(drngs, be['rfc'][0], be['rfc'][-1])
#                         counts, _ = np.histogram(drngs_clipped, bins=be['rfc'], weights=dw_counts)
#                     else:
#                         counts = np.zeros(len(be['rfc']) - 1)
                    
#                     drfc_up[dn] = counts * occ_k
                    
#                     # FIX 2: Capture exact raw damage sum (matches standard sensor logic)
#                     ddmg_up[dn] = {
#                         m: float(sum(c[2] * (c[0] ** m) for c in dcycles)) 
#                         for m in config_data['RFC_M_VALUES']
#                     }
                    
#                 if ds['markov']:
#                     dmkv_up[dn] = compute_markov_matrix(sig, be['rfc'], be['level']) * occ_k
                    
#             if (ds['ldd'] or ds['lrd']) and 'level' in be:
#                 t_lvl, r_lvl = compute_ldd_lrd(time, sig, rotor_speed, be['level'])
#                 dldd_lrd_up[dn] = (t_lvl * occ_k, r_lvl * occ_k)

#         # Update the payload to include ddmg
#         fatigue_payload = {
#             'rfc': rfc_up, 'markov': mkv_up, 'ldd_lrd': ldd_lrd_up, 'dmg': dmg_up,
#             'drfc': drfc_up, 'dmarkov': dmkv_up, 'dldd_lrd': dldd_lrd_up, 
#             'ddmg': ddmg_up  # FIX 2: Pass exact derived damage back to main thread
#         }

#     return {
#         'fname': fname, 'stem': stem, 'ext_all': ext_stats_all, 'del_all': del_stats_all,
#         'is_fatigue': is_fatigue, 'fatigue_payload': fatigue_payload
#     }

# def fast_turbo_worker_phase4(fpath, is_fatigue, occ_k, config_data, bin_edges_rfc, 
#                              bin_edges_level, derived_bin_edges, sensor_cols, 
#                              sensor_cols_all, derived_active, fatigue_sensors):
#     from io_reader import read_fast_output
#     from extreme_stats import compute_extreme_stats, compute_derived_channel
#     from output_writer import write_per_file_sta
#     from config import get_sensor_flags

#     fname = os.path.basename(fpath)
#     stem = os.path.splitext(fname)[0]
    
#     try:
#         df = read_fast_output(fpath)
#     except Exception as e:
#         return {'fname': fname, 'error': str(e)}

#     time = df[config_data['TIME_CHANNEL']].values
#     t_sim = float(time[-1] - time[0])

#     # Compute extreme summary statistics
#     ext_stats = compute_extreme_stats(df, config_data['TIME_CHANNEL'])
    
#     # ── OPTIMIZATION: Cache structures to prevent duplicate iterations and loops
#     del_stats = {}
#     cached_cycles = {}
#     cached_rngs_wts = {}  # Holds fast pre-packaged NumPy arrays
#     cached_dmg = {}       # Caches damage sums to eliminate duplicate loops later
    
#     for col in sensor_cols:
#         if col not in df.columns: continue
#         sig = df[col].values.astype(float)
        
#         # Call the exact pre-filtered rainflow routine
#         cycles = _extract_cycles_fast(sig)
#         cached_cycles[col] = cycles
        
#         # Vectorization: Pack list into NumPy arrays immediately for vector calculations
#         if cycles:
#             rngs = np.array([c[0] for c in cycles], dtype=float)
#             w_counts = np.array([c[2] for c in cycles], dtype=float)
#         else:
#             rngs = np.array([], dtype=float)
#             w_counts = np.array([], dtype=float)
            
#         cached_rngs_wts[col] = (rngs, w_counts)
        
#         # Calculate 1Hz DEL values via highly optimized vector math
#         del_stats[col] = {}
#         cached_dmg[col] = {}
#         for m in config_data['RFC_M_VALUES']:
#             dmg_sum = float(np.sum(w_counts * (rngs ** m))) if len(rngs) > 0 else 0.0
#             cached_dmg[col][m] = dmg_sum
#             del_stats[col][m] = float((dmg_sum / t_sim) ** (1.0 / m)) if dmg_sum > 0 else 0.0

#     # Process Derived Channels once
#     derived_extreme = {}
#     derived_del = {}
#     derived_signals = {}
#     cached_derived_rngs_wts = {} # Cache arrays to avoid double Rainflow extraction
#     cached_derived_dmg = {}      # Cache damage sums for derived channels
    
#     if derived_active:
#         for ds in derived_active:
#             dn = ds['name']
#             sig, _ = compute_derived_channel(df, ds)
#             if sig is None: continue
#             derived_signals[dn] = sig
            
#             if ds.get('sta', 1):
#                 smax, smin = float(np.max(sig)), float(np.min(sig))
#                 derived_extreme[dn] = {
#                     'Max': smax, 'Min': smin, 'Mean': float(np.mean(sig)),
#                     'Stdev': float(np.std(sig, ddof=1)), 'Range': smax - smin,
#                     'AbsMax': smax if abs(smax) >= abs(smin) else smin,
#                     'RMS': float(np.sqrt(np.mean(sig**2)))
#                 }
            
#             # CRITICAL OPTIMIZATION: Determine if Rainflow counting is needed overall
#             need_rainflow = ds.get('1hzeq', 1) or ds.get('rfc', 0) or ds.get('markov', 0)
#             if need_rainflow:
#                 dcycles = _extract_cycles_fast(sig)
#                 if dcycles:
#                     drngs = np.array([c[0] for c in dcycles], dtype=float)
#                     dw_counts = np.array([c[2] for c in dcycles], dtype=float)
#                 else:
#                     drngs = np.array([], dtype=float)
#                     dw_counts = np.array([], dtype=float)
#                 cached_derived_rngs_wts[dn] = (drngs, dw_counts)
#             else:
#                 drngs, dw_counts = np.array([], dtype=float), np.array([], dtype=float)
                
#             # Vectorized derived channel damage tracking
#             if ds.get('1hzeq', 1) or ds.get('rfc', 0):
#                 cached_derived_dmg[dn] = {}
#                 if ds.get('1hzeq', 1): 
#                     derived_del[dn] = {}
#                 for m in config_data['RFC_M_VALUES']:
#                     ddmg = float(np.sum(dw_counts * (drngs ** m))) if len(drngs) > 0 else 0.0
#                     cached_derived_dmg[dn][m] = ddmg
#                     if ds.get('1hzeq', 1):
#                         derived_del[dn][m] = float((ddmg / t_sim) ** (1.0 / m)) if ddmg > 0 else 0.0

#     # Write independent .sta files directly from inside the worker core
#     ext_stats_all = {**ext_stats, **derived_extreme}
#     del_stats_all = {**del_stats, **derived_del}
#     sta_path = os.path.join(config_data['STA_FOLDER'], stem + '.sta')
#     write_per_file_sta(sta_path, sensor_cols_all, ext_stats_all, del_stats_all, config_data['RFC_M_VALUES'])

#     # Compile Fatigue Distribution Data Maps
#     fatigue_payload = {}
#     if is_fatigue:
#         from fatigue_stats import compute_rfc_spectrum, compute_markov_matrix, compute_ldd_lrd
#         rs_chan = config_data['ROTOR_SPEED_CHANNEL']
#         rotor_speed = df[rs_chan].values.astype(float) if rs_chan in df.columns else np.zeros(len(time))

#         # OPTIMIZATION: Pre-calculate sensor configuration flags once outside the loop
#         sensor_flags_cached = {col: get_sensor_flags(col) for col in fatigue_sensors}

#         rfc_up, mkv_up, ldd_lrd_up, dmg_up = {}, {}, {}, {}
#         for col in fatigue_sensors:
#             if col not in df.columns: continue
#             sig = df[col].values.astype(float)
#             flags = sensor_flags_cached[col] # Speedup lookup
            
#             # OPTIMIZATION: Reuse pre-packaged NumPy arrays from the first pass
#             rngs, w_counts = cached_rngs_wts[col]
            
#             if flags['RFC']:
#                 if len(rngs) > 0:
#                     counts, _ = np.histogram(rngs, bins=bin_edges_rfc[col], weights=w_counts)
#                 else:
#                     counts = np.zeros(len(bin_edges_rfc[col]) - 1)
#                 rfc_up[col] = counts * occ_k
                
#                 # OPTIMIZATION: Instantly grab pre-calculated damages from cache
#                 dmg_up[col] = dict(cached_dmg[col])
                
#             if flags['Markov']:
#                 mkv_up[col] = compute_markov_matrix(sig, bin_edges_rfc[col], bin_edges_level[col]) * occ_k
#             if flags['LDD'] or flags['LRD']:
#                 t_lvl, r_lvl = compute_ldd_lrd(time, sig, rotor_speed, bin_edges_level[col])
#                 ldd_lrd_up[col] = (t_lvl * occ_k, r_lvl * occ_k)

#         # Derived Sensor Fatigue Allocations
#         drfc_up, dmkv_up, dldd_lrd_up = {}, {}, {}
#         ddmg_up = {}  
        
#         for ds in derived_active:
#             dn = ds['name']
#             if dn not in derived_signals or occ_k <= 0: continue
#             sig = derived_signals[dn]
#             be = derived_bin_edges.get(dn, {})
            
#             if (ds['rfc'] or ds['markov']) and 'rfc' in be:
#                 # OPTIMIZATION: Pull pre-calculated arrays from cache instead of running Rainflow again!
#                 drngs, dw_counts = cached_derived_rngs_wts[dn]
                
#                 if ds['rfc']:
#                     if len(drngs) > 0:
#                         drngs_clipped = np.clip(drngs, be['rfc'][0], be['rfc'][-1])
#                         counts, _ = np.histogram(drngs_clipped, bins=be['rfc'], weights=dw_counts)
#                     else:
#                         counts = np.zeros(len(be['rfc']) - 1)
                    
#                     drfc_up[dn] = counts * occ_k
                    
#                     # OPTIMIZATION: Pull pre-calculated derived damage from cache
#                     ddmg_up[dn] = dict(cached_derived_dmg[dn])
                    
#                 if ds['markov']:
#                     dmkv_up[dn] = compute_markov_matrix(sig, be['rfc'], be['level']) * occ_k
                    
#             if (ds['ldd'] or ds['lrd']) and 'level' in be:
#                 t_lvl, r_lvl = compute_ldd_lrd(time, sig, rotor_speed, be['level'])
#                 dldd_lrd_up[dn] = (t_lvl * occ_k, r_lvl * occ_k)

#         # Pack optimized variables into payload
#         fatigue_payload = {
#             'rfc': rfc_up, 'markov': mkv_up, 'ldd_lrd': ldd_lrd_up, 'dmg': dmg_up,
#             'drfc': drfc_up, 'dmarkov': dmkv_up, 'dldd_lrd': dldd_lrd_up, 
#             'ddmg': ddmg_up  
#         }

#     return {
#         'fname': fname, 'stem': stem, 'ext_all': ext_stats_all, 'del_all': del_stats_all,
#         'is_fatigue': is_fatigue, 'fatigue_payload': fatigue_payload
#     }

def _extract_cycles_exact(signal):
    """
    Uses the exact official ASTM rainflow extraction to guarantee 100% precision
    and completely eliminate the .5 to .6 float/binning deviations.
    The massive speedup is instead achieved by calling this ONLY ONCE per signal
    and caching the results into vectorized NumPy arrays for all downstream math!
    """
    return [(rng, mean, count) for rng, mean, count, *_ in rainflow.extract_cycles(signal)]

def fast_turbo_worker_phase4(fpath, is_fatigue, occ_k, config_data, bin_edges_rfc, 
                             bin_edges_level, derived_bin_edges, sensor_cols, 
                             sensor_cols_all, derived_active, fatigue_sensors):
    from io_reader import read_fast_output
    from extreme_stats import compute_extreme_stats, compute_derived_channel
    from output_writer import write_per_file_sta
    from config import get_sensor_flags

    fname = os.path.basename(fpath)
    stem = os.path.splitext(fname)[0]
    
    try:
        df = read_fast_output(fpath)
    except Exception as e:
        return {'fname': fname, 'error': str(e)}

    time = df[config_data['TIME_CHANNEL']].values
    t_sim = float(time[-1] - time[0])

    # Compute extreme summary statistics
    ext_stats = compute_extreme_stats(df, config_data['TIME_CHANNEL'])
    
    # ── OPTIMIZATION: Cache structures to prevent duplicate iterations and loops
    del_stats = {}
    cached_cycles = {}
    cached_rngs_wts = {}  # Holds fast pre-packaged NumPy arrays
    cached_dmg = {}       # Caches damage sums to eliminate duplicate loops later
    
    # FIX: Iterate over ALL dataframe columns for DELs, matching your original script!
    # (Previously restricted to `sensor_cols`, skipping Wind channels resulting in exact 0 DELs)
    for col in df.columns:
        if col == config_data['TIME_CHANNEL']: continue
        sig = df[col].values.astype(float)
        
        # Call the EXACT rainflow routine for 100% precision
        cycles = _extract_cycles_exact(sig)
        cached_cycles[col] = cycles
        
        # Vectorization: Pack list into NumPy arrays immediately for vector calculations
        if cycles:
            rngs = np.array([c[0] for c in cycles], dtype=float)
            w_counts = np.array([c[2] for c in cycles], dtype=float)
        else:
            rngs = np.array([], dtype=float)
            w_counts = np.array([], dtype=float)
            
        cached_rngs_wts[col] = (rngs, w_counts)
        
        # Calculate 1Hz DEL values via highly optimized vector math
        del_stats[col] = {}
        cached_dmg[col] = {}
        for m in config_data['RFC_M_VALUES']:
            m_f = float(m) # Safeguard m mapping logic
            dmg_sum = float(np.sum(w_counts * (rngs ** m_f))) if len(rngs) > 0 else 0.0
            cached_dmg[col][m] = dmg_sum
            del_stats[col][m] = float((dmg_sum / t_sim) ** (1.0 / m_f)) if dmg_sum > 0 else 0.0

    # Process Derived Channels once
    derived_extreme = {}
    derived_del = {}
    derived_signals = {}
    cached_derived_rngs_wts = {} # Cache arrays to avoid double Rainflow extraction
    cached_derived_dmg = {}      # Cache damage sums for derived channels
    
    if derived_active:
        for ds in derived_active:
            dn = ds['name']
            sig, _ = compute_derived_channel(df, ds)
            if sig is None: continue
            derived_signals[dn] = sig
            
            if ds.get('sta', 1):
                smax, smin = float(np.max(sig)), float(np.min(sig))
                derived_extreme[dn] = {
                    'Max': smax, 'Min': smin, 'Mean': float(np.mean(sig)),
                    'Stdev': float(np.std(sig, ddof=1)), 'Range': smax - smin,
                    'AbsMax': smax if abs(smax) >= abs(smin) else smin,
                    'RMS': float(np.sqrt(np.mean(sig**2)))
                }
            
            # CRITICAL OPTIMIZATION: Determine if Rainflow counting is needed overall
            need_rainflow = ds.get('1hzeq', 1) or ds.get('rfc', 0) or ds.get('markov', 0)
            if need_rainflow:
                dcycles = _extract_cycles_exact(sig)
                if dcycles:
                    drngs = np.array([c[0] for c in dcycles], dtype=float)
                    dw_counts = np.array([c[2] for c in dcycles], dtype=float)
                else:
                    drngs = np.array([], dtype=float)
                    dw_counts = np.array([], dtype=float)
                cached_derived_rngs_wts[dn] = (drngs, dw_counts)
            else:
                drngs, dw_counts = np.array([], dtype=float), np.array([], dtype=float)
                
            # Vectorized derived channel damage tracking
            if ds.get('1hzeq', 1) or ds.get('rfc', 0):
                cached_derived_dmg[dn] = {}
                if ds.get('1hzeq', 1): 
                    derived_del[dn] = {}
                for m in config_data['RFC_M_VALUES']:
                    m_f = float(m)
                    ddmg = float(np.sum(dw_counts * (drngs ** m_f))) if len(drngs) > 0 else 0.0
                    cached_derived_dmg[dn][m] = ddmg
                    if ds.get('1hzeq', 1):
                        derived_del[dn][m] = float((ddmg / t_sim) ** (1.0 / m_f)) if ddmg > 0 else 0.0

    # Write independent .sta files directly from inside the worker core
    ext_stats_all = {**ext_stats, **derived_extreme}
    del_stats_all = {**del_stats, **derived_del}
    sta_path = os.path.join(config_data['STA_FOLDER'], stem + '.sta')
    write_per_file_sta(sta_path, sensor_cols_all, ext_stats_all, del_stats_all, config_data['RFC_M_VALUES'])

    # Compile Fatigue Distribution Data Maps
    fatigue_payload = {}
    if is_fatigue:
        from fatigue_stats import compute_markov_matrix, compute_ldd_lrd
        rs_chan = config_data['ROTOR_SPEED_CHANNEL']
        rotor_speed = df[rs_chan].values.astype(float) if rs_chan in df.columns else np.zeros(len(time))

        # OPTIMIZATION: Pre-calculate sensor configuration flags once outside the loop
        sensor_flags_cached = {col: get_sensor_flags(col) for col in fatigue_sensors}

        rfc_up, mkv_up, ldd_lrd_up, dmg_up = {}, {}, {}, {}
        for col in fatigue_sensors:
            if col not in df.columns: continue
            sig = df[col].values.astype(float)
            flags = sensor_flags_cached[col] 
            
            # OPTIMIZATION: Reuse pre-packaged NumPy arrays from the first pass
            rngs, w_counts = cached_rngs_wts[col]
            
            if flags['RFC']:
                if len(rngs) > 0:
                    counts, _ = np.histogram(rngs, bins=bin_edges_rfc[col], weights=w_counts)
                else:
                    counts = np.zeros(len(bin_edges_rfc[col]) - 1)
                rfc_up[col] = counts * occ_k
                
                dmg_up[col] = dict(cached_dmg[col])
                
            if flags['Markov']:
                mkv_up[col] = compute_markov_matrix(sig, bin_edges_rfc[col], bin_edges_level[col]) * occ_k
            if flags['LDD'] or flags['LRD']:
                t_lvl, r_lvl = compute_ldd_lrd(time, sig, rotor_speed, bin_edges_level[col])
                ldd_lrd_up[col] = (t_lvl * occ_k, r_lvl * occ_k)

        # Derived Sensor Fatigue Allocations
        drfc_up, dmkv_up, dldd_lrd_up = {}, {}, {}
        ddmg_up = {}  
        
        for ds in derived_active:
            dn = ds['name']
            if dn not in derived_signals or occ_k <= 0: continue
            sig = derived_signals[dn]
            be = derived_bin_edges.get(dn, {})
            
            if (ds.get('rfc', 0) or ds.get('markov', 0)) and 'rfc' in be:
                drngs, dw_counts = cached_derived_rngs_wts[dn]
                
                if ds.get('rfc', 0):
                    if len(drngs) > 0:
                        drngs_clipped = np.clip(drngs, be['rfc'][0], be['rfc'][-1])
                        counts, _ = np.histogram(drngs_clipped, bins=be['rfc'], weights=dw_counts)
                    else:
                        counts = np.zeros(len(be['rfc']) - 1)
                    
                    drfc_up[dn] = counts * occ_k
                    ddmg_up[dn] = dict(cached_derived_dmg[dn])
                    
                if ds.get('markov', 0):
                    dmkv_up[dn] = compute_markov_matrix(sig, be['rfc'], be['level']) * occ_k
                    
            if (ds.get('ldd', 0) or ds.get('lrd', 0)) and 'level' in be:
                t_lvl, r_lvl = compute_ldd_lrd(time, sig, rotor_speed, be['level'])
                dldd_lrd_up[dn] = (t_lvl * occ_k, r_lvl * occ_k)

        fatigue_payload = {
            'rfc': rfc_up, 'markov': mkv_up, 'ldd_lrd': ldd_lrd_up, 'dmg': dmg_up,
            'drfc': drfc_up, 'dmarkov': dmkv_up, 'dldd_lrd': dldd_lrd_up, 
            'ddmg': ddmg_up  
        }

    return {
        'fname': fname, 'stem': stem, 'ext_all': ext_stats_all, 'del_all': del_stats_all,
        'is_fatigue': is_fatigue, 'fatigue_payload': fatigue_payload
    }

def _raw_header(n_files):
    """Generate header lines for summary_Raw.sta."""
    return [
        '# OpenFAST Postprocessing — Raw Statistics Summary',
        '# ' + '-' * 49,
        f'# Content      : Per-file raw statistics and 1Hz DEL',
        f'# Files        : {n_files} (all processed files)',
        f'# Rows         : Individual simulation files',
        f'# Columns      : Sensor channels + derived Mres sensors',
        f'# Note (Mres)  : Min = minimum resultant (always positive, not a reversal)',
        f'#                AbsMax = identical to Max for all Mres channels',
        f'# PLF          : Not applied',
        f'# Family       : Not applied',
        f'# 1Hz DEL Neq  : T_sim per file',
    ]


def main():
    # ── Phase 0: Setup ────────────────────────────────────────────────────────
    print("=" * 70)
    print("  OpenFAST Postprocessing")
    print("=" * 70)

    # Create output subfolders
    os.makedirs(config.STA_FOLDER, exist_ok=True)
    os.makedirs(config.FAT_FOLDER, exist_ok=True)
    os.makedirs(config.EXT_FOLDER, exist_ok=True)
    os.makedirs(config.SUM_FOLDER, exist_ok=True)
    os.makedirs(config.POWER_CURVE_FOLDER, exist_ok=True)

    all_files = get_all_output_files(config.INPUT_FOLDER)
    if not all_files:
        print(f"\nERROR: No .outb or .out files found in:\n  {config.INPUT_FOLDER}")
        sys.exit(1)

    fatigue_set       = set(config.FATIGUE_FILES.keys())
    fatigue_filepaths = [f for f in all_files
                         if os.path.basename(f) in fatigue_set]

    # Error on any fatigue file listed but not found on disk
    found_fatigue_names = {os.path.basename(f) for f in fatigue_filepaths}
    missing = [name for name in config.FATIGUE_FILES if name not in found_fatigue_names]
    if missing:
        print("\nERROR: The following fatigue DLC files were listed in "
              "fatigue_files.txt but not found in the input folder:")
        for name in missing:
            print(f"  {name}")
        sys.exit(1)

    print(f"\n  Input folder  : {config.INPUT_FOLDER}")
    print(f"  STA folder    : {config.STA_FOLDER}")
    print(f"  FAT folder    : {config.FAT_FOLDER}")
    print(f"  EXT folder    : {config.EXT_FOLDER}")
    print(f"  SUM folder    : {config.SUM_FOLDER}")
    print(f" POWER CURVE folder  : {config.POWER_CURVE_FOLDER}")

    # ── Initialise logger ─────────────────────────────────────────────────────
    log = PostProcessLogger(config.LOG_FILE, config.OUTPUT_FOLDER)
    log.phase_start(0, 'Creating output folders and discovering files')

    # Load derived sensor definitions from [DERIVED_SENSORS] in sensorList.txt
    derived_sensors  = get_derived_sensors()
    derived_active   = []   # populated after Phase 1
    derived_extreme  = {}   # {fname: {dname: {stat: float}}}
    derived_del_file = {}   # {fname: {dname: {m: float}}}
    derived_rfc_accum    = {}
    derived_markov_accum = {}
    derived_ldd_accum    = {}
    derived_lrd_accum    = {}
    derived_bin_edges    = {}
    print(f"  Derived sensors defined: {len(derived_sensors)}")
    log.phase_complete(0, f"{len(all_files)} input files found")
    print(f"  Total files   : {len(all_files)}  "
          f"({len(fatigue_filepaths)} fatigue DLCs)")
    print(f"  RFC slopes    : {[fmt_slope(m) for m in config.RFC_M_VALUES]}")
    print(f"  LDD slopes    : {[fmt_slope(m) for m in config.LDD_M_VALUES]}")
    print(f"  LRD slopes    : {[fmt_slope(m) for m in config.LRD_M_VALUES]}")
    print(f"  Lifetime      : {config.LIFETIME_YEARS} years  "
          f"({config.LIFETIME_SECS:.3e} s)")
    print(f"  Neq (RFC)     : {config.NEQ_LIFETIME:.3g}")
    print(f"  Neq_time (LDD): {config.LIFETIME_SECS:.3g} s")
    print(f"  Neq_rev (LRD) : {config.NEQ_REV:.3g}")
    print(f"  Bins          : {config.N_BINS}")

    # # ── Phase 1: Sensor names from the first file ──────────────────────────────
    # log.phase_start(1, "Reading sensor list and building flag subsets")
    # print("\n[Phase 1] Reading sensor list from first file...")
    # df0         = read_fast_output(all_files[0])
    # sensor_cols = get_sensor_columns(df0, config.TIME_CHANNEL)
    # print(f"  → {len(sensor_cols)} sensor channels found")

    # # Build per-flag sensor subsets from sensorList.txt
    # # All sensors always get STA + 1HzEq (computed in Phase 4 for all)
    # sensors_rfc    = [c for c in sensor_cols if get_sensor_flags(c)['RFC']    == 1]
    # sensors_markov = [c for c in sensor_cols if get_sensor_flags(c)['Markov'] == 1]
    # sensors_lrd    = [c for c in sensor_cols if get_sensor_flags(c)['LRD']    == 1]
    # sensors_ldd    = [c for c in sensor_cols if get_sensor_flags(c)['LDD']    == 1]
    # # Union of all fatigue sensors (need bin ranges for any of RFC/Markov/LRD/LDD)
    # fatigue_flag_sensors = list(dict.fromkeys(
    #     sensors_rfc + sensors_markov + sensors_lrd + sensors_ldd))

    # print(f"  → sensorList.txt: {len(config.SENSOR_LIST)} entries loaded")

    # # Validate derived sensors against first file sensor list
    # for ds in derived_sensors:
    #     available_channels_lower = [c.lower() for c in sensor_cols]
    #     missing = [op for op in ds["operands"] if op.lower() not in available_channels_lower]
    #     if missing:
    #         print(f"  WARNING: {ds['name']} skipped — "
    #               f"component sensor(s) not found: {missing}")
    #     else:

    #         actual_operands = []
    #         for op in ds["operands"]:
    #             for real_col in sensor_cols:
    #                 if real_col.lower() == op.lower():
    #                     actual_operands.append(real_col)
    #                     break
            
    #         ds["operands"] = actual_operands # Update with the correct case
    #         derived_active.append(ds)

    # print(f"  → Derived sensors active: {len(derived_active)} / {len(derived_sensors)}")
    # for _ds in derived_sensors:
    #     if _ds not in derived_active:
    #         log.sensor_skipped(_ds["name"], "component sensor not in output")
    # log.phase_complete(1, f"{len(sensor_cols)} real + {len(derived_active)} derived sensors")

    # # Build sensor_cols_all HERE — real + active derived sensors
    # # Must be done in Phase 1 so Phase 4 per-file .sta includes Mres
    # sensor_cols_all = list(sensor_cols)
    # for _ds in derived_active:

    #     operands = _ds["operands"] 
    #     missing = [op for op in operands if op not in sensor_cols]

    #     if _ds.get('sta', 1) and _ds['name'] not in sensor_cols_all:
    #         sensor_cols_all.append(_ds['name'])
    # print(f"  → sensor_cols_all: {len(sensor_cols_all)} "
    #       f"({len(sensor_cols)} real + {len(derived_active)} derived)")
    # log.info(f"sensor_cols_all built: {len(sensor_cols_all)} channels", tag='Phase 1')

    # print(f"  → RFC sensors    : {len(sensors_rfc)}")
    # print(f"  → Markov sensors : {len(sensors_markov)}")
    # print(f"  → LRD sensors    : {len(sensors_lrd)}")
    # print(f"  → LDD sensors    : {len(sensors_ldd)}")

    # ── Phase 1: Sensor names from the first file ──────────────────────────────
    log.phase_start(1, "Reading sensor list and building flag subsets")
    print("\n[Phase 1] Reading sensor list from first file...")
    df0         = read_fast_output(all_files[0])
    sensor_cols = get_sensor_columns(df0, config.TIME_CHANNEL)
    print(f"  → {len(sensor_cols)} sensor channels found")

    # print(f"DEBUG: Available channels in file: {sensor_cols}")

    # 1. Pre-build a lowercase map for case-insensitive matching
    sensor_cols_lower = {c.lower(): c for c in sensor_cols}

    # Build per-flag sensor subsets for REAL sensors
    sensors_rfc    = [c for c in sensor_cols if get_sensor_flags(c)['RFC']    == 1]
    sensors_markov = [c for c in sensor_cols if get_sensor_flags(c)['Markov'] == 1]
    sensors_lrd    = [c for c in sensor_cols if get_sensor_flags(c)['LRD']    == 1]
    sensors_ldd    = [c for c in sensor_cols if get_sensor_flags(c)['LDD']    == 1]

    print(f"  → sensorList.txt: {len(config.SENSOR_LIST)} entries loaded")

    # 2. Validate and Activate Derived Sensors
    derived_active = []
    for ds in derived_sensors:
        # Check if all operands in the formula exist in the file (case-insensitive)
        missing = [op for op in ds["operands"] if op.lower() not in sensor_cols_lower]
        
        if missing:
            print(f"  WARNING: {ds['name']} skipped — component sensor(s) not found: {missing}")
        else:
            # Update operands to the EXACT case found in the file
            ds["operands"] = [sensor_cols_lower[op.lower()] for op in ds["operands"]]
            derived_active.append(ds)

            # Add this derived sensor to the fatigue subsets if flags are set
            d_name = ds['name']
            if ds.get('rfc', 0) == 1:    sensors_rfc.append(d_name)
            if ds.get('markov', 0) == 1: sensors_markov.append(d_name)
            if ds.get('lrd', 0) == 1:    sensors_lrd.append(d_name)
            if ds.get('ldd', 0) == 1:    sensors_ldd.append(d_name)

    print(f"  → Derived sensors active: {len(derived_active)} / {len(derived_sensors)}")
    
    # Log skips for report
    for _ds in derived_sensors:
        if _ds not in derived_active:
            log.sensor_skipped(_ds["name"], "component sensor not in output")

    # 3. Build the final sensor list (Real + Active Derived)
    sensor_cols_all = list(sensor_cols)
    for _ds in derived_active:
        if _ds['name'] not in sensor_cols_all:
            sensor_cols_all.append(_ds['name'])

    # 4. Final union of all sensors requiring bin ranges
    fatigue_flag_sensors = list(dict.fromkeys(
        sensors_rfc + sensors_markov + sensors_lrd + sensors_ldd))

    log.phase_complete(1, f"{len(sensor_cols)} real + {len(derived_active)} derived sensors")

    print(f"  → sensor_cols_all: {len(sensor_cols_all)} "
          f"({len(sensor_cols)} real + {len(derived_active)} derived)")
    
    print(f"  → RFC sensors     : {len(sensors_rfc)}")
    print(f"  → Markov sensors : {len(sensors_markov)}")
    print(f"  → LRD sensors    : {len(sensors_lrd)}")
    print(f"  → LDD sensors    : {len(sensors_ldd)}")

    # ── Phase 2: Global bin range scan ────────────────────────────────────────
    print("\n[Phase 2] Global scan of fatigue DLCs for bin ranges...")
    if not fatigue_filepaths:
        print("  WARNING: No fatigue DLC files found — fatigue matrices will be empty")
        bin_edges_rfc   = {}
        bin_edges_level = {}
        T_total_sim     = 1.0   # avoid division-by-zero
    else:
        bin_edges_rfc, bin_edges_level, _ = determine_bin_ranges(
            fatigue_filepaths,
            config.N_BINS,
            config.TIME_CHANNEL,
            config.ROTOR_SPEED_CHANNEL,
            sensor_filter=fatigue_flag_sensors,
        )

    # Log per-file occurrence weights
    # print(f"  → {len(config.FATIGUE_FILES)} fatigue DLCs with occurrence weights:")
    # for name, occ in config.FATIGUE_FILES.items():
        # print(f"      {name:<50s}  occurrences = {occ:g}")

    # Compute bin edges for derived sensors needing RFC/Markov
    for ds in derived_active:
        if not (ds["rfc"] or ds["markov"]):
            continue
        max_rng = 0.0
        gmin, gmax = np.inf, -np.inf
        for fname in list(config.FATIGUE_FILES.keys())[:min(5, len(config.FATIGUE_FILES))]:
            if config.FATIGUE_FILES[fname] <= 0:
                continue
            fpath = os.path.join(config.INPUT_FOLDER, fname)
            if not os.path.isfile(fpath):
                continue
            try:
                df_tmp = read_fast_output(fpath)
                sig, _ = compute_derived_channel(df_tmp, ds)
                if sig is None:
                    continue
                max_rng = max(max_rng, float(np.max(sig) - np.min(sig)))
                gmin = min(gmin, float(np.min(sig)))
                gmax = max(gmax, float(np.max(sig)))
            except Exception:
                continue
        if max_rng > 0:
            derived_bin_edges[ds["name"]] = {
                "rfc"  : np.linspace(0, max_rng, config.N_BINS + 1),
                "level": np.linspace(gmin, gmax, config.N_BINS + 1),
            }

    # ── Phase 3: Initialise accumulators ──────────────────────────────────────
    print("\n[Phase 3] Initialising fatigue accumulators...")
    fatigue_sensors = [c for c in sensor_cols if c in bin_edges_rfc]

    rfc_accum     = {col: np.zeros(config.N_BINS)
                     for col in sensors_rfc    if col in bin_edges_rfc}
    markov_accum  = {col: np.zeros((config.N_BINS, config.N_BINS))
                     for col in sensors_markov if col in bin_edges_level}
    # ldd_lrd_accum[:, 0] = cumulative time (s)
    # ldd_lrd_accum[:, 1] = cumulative revolutions
    ldd_lrd_accum = {col: np.zeros((config.N_BINS, 2))
                     for col in (sensors_lrd + sensors_ldd)
                     if col in bin_edges_level}
    # Combined set of sensors that need any fatigue accumulation
    fatigue_sensors = list(dict.fromkeys(
        list(rfc_accum) + list(markov_accum) + list(ldd_lrd_accum)))
    print(f"  → Accumulators initialised: RFC={len(rfc_accum)}, "
          f"Markov={len(markov_accum)}, LDD/LRD={len(ldd_lrd_accum)}")

    # Initialise derived sensor accumulators
    for ds in derived_active:
        n = ds["name"]
        if (ds["rfc"] or ds["markov"]) and n in derived_bin_edges:
            derived_rfc_accum[n]    = np.zeros(config.N_BINS)
            derived_markov_accum[n] = np.zeros((config.N_BINS, config.N_BINS))
        if ds["ldd"] or ds["lrd"]:
            derived_ldd_accum[n] = np.zeros(config.N_BINS)
            derived_lrd_accum[n] = np.zeros(config.N_BINS)

    # Per-file damage scalars for DLC contribution analysis
    # {fname: {sensor: {m: damage_sum}}} — only for RFC sensors, occ > 0
    damage_per_file = {}

    # # ── Phase 4: Main processing loop ─────────────────────────────────────────
    # summary_files    = []
    # summary_extreme  = {}   # {fname: {sensor: {stat: val}}}
    # summary_del      = {}   # {fname: {sensor: {m: val}}}
    # summary_sta_path = os.path.join(config.STA_FOLDER, 'summary_Raw.sta')

    # print(f"\n[Phase 4] Processing {len(all_files)} files...\n")

    # for fpath in all_files:
    #     fname = os.path.basename(fpath)
    #     print(f"  ► {fname}")

    #     # Read file
    #     try:
    #         df = read_fast_output(fpath)
    #     except Exception as e:
    #         print(f"    ERROR reading file: {e} — skipped")
    #         continue

    #     time = df[config.TIME_CHANNEL].values
    #     t_sim = float(time[-1] - time[0])

    #     # Check sensor consistency
    #     file_sensors = get_sensor_columns(df, config.TIME_CHANNEL)
    #     if set(file_sensors) != set(sensor_cols):
    #         extra   = set(file_sensors) - set(sensor_cols)
    #         missing = set(sensor_cols)  - set(file_sensors)
    #         if extra:
    #             print(f"    WARNING: {len(extra)} extra channels (ignored)")
    #         if missing:
    #             print(f"    WARNING: {len(missing)} channels missing vs first file")

    #     # ── Real sensor stats ─────────────────────────────────────────────────
    #     ext_stats = compute_extreme_stats(df, config.TIME_CHANNEL)
    #     del_stats = compute_del_all_m(df, config.RFC_M_VALUES, config.TIME_CHANNEL)

    #     # ── Derived sensor stats (Part A: STA + 1HzEq) ───────────────────────
    #     # Runs for ALL files (not just fatigue DLCs)
    #     # Must happen BEFORE write_per_file_sta so Mres is included in .sta
    #     if derived_active:
    #         if fname not in derived_extreme:
    #             derived_extreme[fname]  = {}
    #             derived_del_file[fname] = {}
    #         for _ds in derived_active:
    #             _dn = _ds['name']
    #             _sig, _miss = compute_derived_channel(df, _ds)
    #             if _sig is None:
    #                 continue
    #             if _ds.get('sta', 1):
    #                 _smax = float(np.max(_sig))
    #                 _smin = float(np.min(_sig))
    #                 # AbsMax preserves sign of larger-magnitude extreme
    #                 _absmax = _smax if abs(_smax) >= abs(_smin) else _smin
    #                 derived_extreme[fname][_dn] = {
    #                     'Max'   : _smax,
    #                     'Min'   : _smin,
    #                     'Mean'  : float(np.mean(_sig)),
    #                     'Stdev' : float(np.std(_sig, ddof=1)),
    #                     'Range' : _smax - _smin,
    #                     'AbsMax': _absmax,
    #                     'RMS'   : float(np.sqrt(np.mean(_sig**2))),
    #                 }
    #             if _ds.get('1hzeq', 1):
    #                 # compute_del returns single value; loop over slopes
    #                 from fatigue_stats import compute_del
    #                 derived_del_file[fname][_dn] = {
    #                     m: compute_del(_sig, t_sim, m)
    #                     for m in config.RFC_M_VALUES
    #                 }

    #     # ── Merge real + derived → write per-file .sta ────────────────────────
    #     ext_stats_all = dict(ext_stats)
    #     del_stats_all = dict(del_stats)
    #     for _dn, _dstats in derived_extreme.get(fname, {}).items():
    #         ext_stats_all[_dn] = _dstats
    #     for _dn, _ddel in derived_del_file.get(fname, {}).items():
    #         del_stats_all[_dn] = _ddel

    #     stem     = os.path.splitext(fname)[0]
    #     sta_path = os.path.join(config.STA_FOLDER, stem + '.sta')

    #     # print("ext stats all", ext_stats_all)
        
    #     write_per_file_sta(sta_path, sensor_cols_all,
    #                        ext_stats_all, del_stats_all, config.RFC_M_VALUES)
    #     print(f"    ✓ written: STA/{stem}.sta")
    #     log.file_written(f'STA/{stem}.sta', tag='Phase 4')

    #     # Update in-memory summary — real + derived combined
    #     summary_files.append(fname)
    #     summary_extreme[fname] = ext_stats_all
    #     summary_del[fname]     = del_stats_all

    #     # Incrementally rewrite summary.sta in STA folder (no lifetime DEL yet)
    #     write_summary_sta(
    #         summary_sta_path, summary_files, sensor_cols_all,
    #         summary_extreme, summary_del, config.RFC_M_VALUES,
    #         header=_raw_header(len(summary_files)),
    #     )

    #     # ── Fatigue accumulation (fatigue DLCs only) ──────────────────────────
    #     if fname in fatigue_set:
    #         occ_k = config.FATIGUE_FILES[fname]   # occurrences over lifetime

    #         # Rotor speed for LDD/LRD
    #         if config.ROTOR_SPEED_CHANNEL in df.columns:
    #             rotor_speed = df[config.ROTOR_SPEED_CHANNEL].values.astype(float)
    #         else:
    #             print(f"    WARNING: '{config.ROTOR_SPEED_CHANNEL}' not found — "
    #                   f"revolutions set to zero for this file")
    #             rotor_speed = np.zeros(len(time))

    #         for col in fatigue_sensors:
    #             if col not in df.columns:
    #                 continue
    #             signal = df[col].values.astype(float)
    #             flags  = get_sensor_flags(col)

    #             # RFC spectrum — only if RFC flag enabled
    #             if flags['RFC'] and col in rfc_accum:
    #                 rfc_accum[col] += compute_rfc_spectrum(
    #                     signal, bin_edges_rfc[col]) * occ_k

    #             # Markov matrix — only if Markov flag enabled
    #             if flags['Markov'] and col in markov_accum:
    #                 markov_accum[col] += compute_markov_matrix(
    #                     signal, bin_edges_rfc[col], bin_edges_level[col]) * occ_k

    #             # LDD / LRD — only if LDD or LRD flag enabled
    #             if (flags['LDD'] or flags['LRD']) and col in ldd_lrd_accum:
    #                 t_lvl, r_lvl = compute_ldd_lrd(
    #                     time, signal, rotor_speed, bin_edges_level[col])
    #                 ldd_lrd_accum[col][:, 0] += t_lvl * occ_k
    #                 ldd_lrd_accum[col][:, 1] += r_lvl * occ_k

    #         # Per-file damage scalar for contribution analysis
    #         file_dmg = {}
    #         for col in fatigue_sensors:
    #             if col not in df.columns:
    #                 continue
    #             if not get_sensor_flags(col)['RFC']:
    #                 continue
    #             signal = df[col].values.astype(float)
    #             file_dmg[col] = compute_file_damage(signal, config.RFC_M_VALUES)
    #         if file_dmg:
    #             damage_per_file[fname] = file_dmg

    #         # ── Derived sensor fatigue accumulation (Part B) ──────────────────
    #         # STA stats already computed in Part A above for all files
    #         # Here only RFC/Markov/LDD/LRD accumulation for fatigue DLCs
    #         if derived_active and occ_k > 0:
    #             for ds in derived_active:
    #                 dname = ds['name']
    #                 # Time series already computed in Part A — recompute for fatigue
    #                 sig, miss = compute_derived_channel(df, ds)
    #                 if sig is None:
    #                     continue
    #                 be = derived_bin_edges.get(dname, {})
    #                 if (ds['rfc'] or ds['markov']) and 'rfc' in be:
    #                     if dname in derived_rfc_accum:
    #                         rfc = compute_rfc_spectrum(sig, be['rfc'])
    #                         derived_rfc_accum[dname] += rfc * occ_k
    #                     if ds['markov'] and dname in derived_markov_accum:
    #                         mkv = compute_markov_matrix(sig, be['rfc'], be['level'])
    #                         derived_markov_accum[dname] += mkv * occ_k
    #                 if (ds['ldd'] or ds['lrd']) and dname in derived_ldd_accum:
    #                     # rotor_speed already loaded above for this file
    #                     lvl_be = be.get('level',
    #                         np.linspace(float(np.min(sig)), float(np.max(sig)), config.N_BINS+1))
    #                     # compute_ldd_lrd signature: (time, signal, rotor_speed_rpm, bin_edges_level)
    #                     ldd_arr, lrd_arr = compute_ldd_lrd(time, sig, rotor_speed, lvl_be)
    #                     if ds['ldd']:
    #                         derived_ldd_accum[dname] += ldd_arr * occ_k
    #                     if ds['lrd']:
    #                         derived_lrd_accum[dname] += lrd_arr * occ_k

    #         print(f"    ✓ fatigue matrices updated  (occurrences = {occ_k:g})")

# ── Phase 4: Main processing loop ─────────────────────────────────────────
    summary_files    = []
    summary_extreme  = {}   
    summary_del      = {}   
    summary_sta_path = os.path.join(config.STA_FOLDER, 'summary_Raw.sta')

    print(f"\n[Phase 4] Processing {len(all_files)} files...\n")

    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, as_completed

    config_data = {
        'TIME_CHANNEL': config.TIME_CHANNEL,
        'RFC_M_VALUES': config.RFC_M_VALUES,
        'ROTOR_SPEED_CHANNEL': config.ROTOR_SPEED_CHANNEL,
        'STA_FOLDER': config.STA_FOLDER
    }

    num_cores = max(1, multiprocessing.cpu_count() - 1)

    with ProcessPoolExecutor(max_workers=num_cores) as executor:
        futures = {}
        for fpath in all_files:
            fname = os.path.basename(fpath)
            is_fatigue = fname in fatigue_set
            occ_k = config.FATIGUE_FILES[fname] if is_fatigue else 0
            
            futures[executor.submit(
                fast_turbo_worker_phase4, fpath, is_fatigue, occ_k, config_data, 
                bin_edges_rfc, bin_edges_level, derived_bin_edges, 
                sensor_cols, sensor_cols_all, derived_active, fatigue_sensors
            )] = fname

        # for future in as_completed(futures):
        #     res = future.result()
        #     if 'error' in res:
        #         print(f"    ERROR reading file {res['fname']}: {res['error']} — skipped")
        #         continue

        #     fname, stem = res['fname'], res['stem']
        #     print(f"  ► {fname} ✓")
        #     log.file_written(f'STA/{stem}.sta', tag='Phase 4')
        for future in tqdm(as_completed(futures), total=len(futures), desc="Processing Phase 4", unit="file"):
            res = future.result()
            if 'error' in res:
                # Use tqdm.write so errors print beautifully *above* the progress bar without tearing it apart
                tqdm.write(f"    ERROR reading file {res['fname']}: {res['error']} — skipped")
                continue

            fname, stem = res['fname'], res['stem']
            
            # The serial print statement has been removed to keep your terminal clean!
            # log.file_written(f'STA/{stem}.sta', tag='Phase 4')

            summary_files.append(fname)
            summary_extreme[fname] = res['ext_all']
            summary_del[fname]     = res['del_all']

            if res['is_fatigue']:
                p = res['fatigue_payload']
                for col, val in p['rfc'].items(): rfc_accum[col] += val
                for col, val in p['markov'].items(): markov_accum[col] += val
                for col, (t_lvl, r_lvl) in p['ldd_lrd'].items():
                    ldd_lrd_accum[col][:, 0] += t_lvl
                    ldd_lrd_accum[col][:, 1] += r_lvl
                if p['dmg']: damage_per_file[fname] = p['dmg']

                # Accumulate derived sensor components
                for dname, val in p['drfc'].items(): derived_rfc_accum[dname] += val
                for dname, val in p['dmarkov'].items(): derived_markov_accum[dname] += val
                for dname, (t_lvl, r_lvl) in p['dldd_lrd'].items():
                    if dname in derived_ldd_accum: derived_ldd_accum[dname] += t_lvl
                    if dname in derived_lrd_accum: derived_lrd_accum[dname] += r_lvl

    print("\n[Phase 4] Concluding processing passes. Generating unified summary file data...")
    # Critical performance modification: Consolidated into a single, high-speed write pass
    write_summary_sta(
        summary_sta_path, summary_files, sensor_cols_all,
        summary_extreme, summary_del, config.RFC_M_VALUES,
        header=_raw_header(len(summary_files)),
    )

    # ── Phase 5: Accumulators already lifetime-scaled ─────────────────────────
    # Each file's contribution was multiplied by its occurrence count during
    # Phase 4, so no further scaling is needed here.
    print(f"\n[Phase 5] Accumulators already scaled by per-file occurrences — "
          f"no further scaling needed.")

    # ── Phase 6: Lifetime DEL (RFC, LDD, LRD) ────────────────────────────────────
    print(f"\n[Phase 6] Computing lifetime DEL...")
    print(f"  → RFC : Neq        = {config.NEQ_LIFETIME:.3g}")
    print(f"  → LDD : Neq_time   = {config.LIFETIME_SECS:.3g} s")
    print(f"  → LRD : Neq_rev    = {config.NEQ_REV:.3g}")

    # RFC-based lifetime DEL
    lifetime_del_rfc = compute_lifetime_del(
        rfc_accum, bin_edges_rfc, config.RFC_M_VALUES, config.NEQ_LIFETIME)

    # LDD-based lifetime DEL
    lifetime_del_ldd = {}
    for col, accum in ldd_lrd_accum.items():
        if get_sensor_flags(col)['LDD']:
            lifetime_del_ldd[col] = compute_lifetime_del_ldd(
                accum[:, 0], bin_edges_level[col],
                config.LDD_M_VALUES, config.LIFETIME_SECS)

    # LRD-based lifetime DEL
    lifetime_del_lrd = {}
    for col, accum in ldd_lrd_accum.items():
        if get_sensor_flags(col)['LRD']:
            lifetime_del_lrd[col] = compute_lifetime_del_lrd(
                accum[:, 1], bin_edges_level[col],
                config.LRD_M_VALUES, config.NEQ_REV)

    # ── Phase 7: Write per-sensor fatigue files → FAT folder ─────────────────
    print(f"\n[Phase 7] Writing per-sensor fatigue files to FAT/ "
          f"({len(fatigue_sensors)} sensors × 4 types)...")
    n_written = 0
    for col in fatigue_sensors:
        safe  = sanitize_for_filename(col)
        flags = get_sensor_flags(col)

        if flags['RFC'] and col in rfc_accum:
            write_rfc_file(
                os.path.join(config.FAT_FOLDER, safe + '.rfc'),
                bin_edges_rfc[col], rfc_accum[col], col,
                del_rfc=lifetime_del_rfc.get(col),
                lifetime_years=config.LIFETIME_YEARS,
                neq=config.NEQ_LIFETIME,
                m_values=config.RFC_M_VALUES)
            n_written += 1

        if flags['Markov'] and col in markov_accum:
            write_markov_file(
                os.path.join(config.FAT_FOLDER, safe + '.markov'),
                bin_edges_rfc[col], bin_edges_level[col], markov_accum[col], col)
            n_written += 1

        if flags['LDD'] and col in ldd_lrd_accum:
            write_ldd_file(
                os.path.join(config.FAT_FOLDER, safe + '.ldd'),
                bin_edges_level[col],
                ldd_lrd_accum[col][:, 0], ldd_lrd_accum[col][:, 1], col,
                del_ldd=lifetime_del_ldd.get(col),
                lifetime_years=config.LIFETIME_YEARS,
                neq_time=config.LIFETIME_SECS,
                m_values=config.LDD_M_VALUES)
            n_written += 1

        if flags['LRD'] and col in ldd_lrd_accum:
            write_lrd_file(
                os.path.join(config.FAT_FOLDER, safe + '.lrd'),
                bin_edges_level[col],
                ldd_lrd_accum[col][:, 0], ldd_lrd_accum[col][:, 1], col,
                del_lrd=lifetime_del_lrd.get(col),
                lifetime_years=config.LIFETIME_YEARS,
                neq_rev=config.NEQ_REV,
                m_values=config.LRD_M_VALUES)
            n_written += 1

    print(f"  → {n_written} fatigue files written")

    # Write derived sensor FAT files
    n_derived_fat = 0
    for ds in derived_active:
        dname = ds["name"]
        safe  = sanitize_for_filename(dname)
        be    = derived_bin_edges.get(dname, {})
        if ds["rfc"] and dname in derived_rfc_accum and "rfc" in be:
            # compute_lifetime_del wraps a single-array input into
            # {'default': {m: del}} for uniformity with multi-sensor calls;
            # write_rfc_file expects a flat {m: del}, so unwrap here.
            drfc_del_nested = compute_lifetime_del(
                derived_rfc_accum[dname], be["rfc"],
                config.RFC_M_VALUES, config.NEQ_LIFETIME)
            if isinstance(drfc_del_nested, dict) and 'default' in drfc_del_nested:
                drfc_del = drfc_del_nested['default']
            else:
                drfc_del = drfc_del_nested
            write_rfc_file(
                os.path.join(config.FAT_FOLDER, safe + ".rfc"),
                be["rfc"], derived_rfc_accum[dname], dname,
                del_rfc=drfc_del, lifetime_years=config.LIFETIME_YEARS,
                neq=config.NEQ_LIFETIME, m_values=config.RFC_M_VALUES)
            n_derived_fat += 1
        if ds["markov"] and dname in derived_markov_accum and "level" in be:
            write_markov_file(
                os.path.join(config.FAT_FOLDER, safe + ".markov"),
                be["rfc"], be["level"], derived_markov_accum[dname], dname)
            n_derived_fat += 1
        if ds["ldd"] and dname in derived_ldd_accum and "level" in be:
            dldd_del = compute_lifetime_del_ldd(
                derived_ldd_accum[dname], be["level"], config.LDD_M_VALUES, config.LIFETIME_SECS)
            write_ldd_file(
                os.path.join(config.FAT_FOLDER, safe + ".ldd"),
                be["level"], derived_ldd_accum[dname],
                np.zeros(config.N_BINS), dname,
                del_ldd=dldd_del, lifetime_years=config.LIFETIME_YEARS,
                neq_time=config.LIFETIME_SECS, m_values=config.LDD_M_VALUES)
            n_derived_fat += 1
        if ds["lrd"] and dname in derived_lrd_accum and "level" in be:
            dlrd_del = compute_lifetime_del_lrd(
                derived_lrd_accum[dname], be["level"], config.LRD_M_VALUES, config.NEQ_REV)
            write_lrd_file(
                os.path.join(config.FAT_FOLDER, safe + ".lrd"),
                be["level"], derived_ldd_accum.get(dname, np.zeros(config.N_BINS)),
                derived_lrd_accum[dname], dname,
                del_lrd=dlrd_del, lifetime_years=config.LIFETIME_YEARS,
                neq_rev=config.NEQ_REV, m_values=config.LRD_M_VALUES)
            n_derived_fat += 1
    print(f"  → {n_derived_fat} derived sensor FAT files written")

    # sensor_cols_all already built in Phase 1
    # summary_extreme and summary_del already include Mres from Phase 4
    # No merge step needed here

    # ── Phase 8: Final summary.sta (with LIFETIME_DEL_20yr row) ───────────────
    print("\n[Phase 8] Finalising summary.sta with LIFETIME_DEL_20yr rows...")
    write_summary_sta(
        summary_sta_path, summary_files, sensor_cols_all,
        summary_extreme, summary_del, config.RFC_M_VALUES,
        header=_raw_header(len(summary_files)),
    )
    print(f"  → Written: STA/summary_Raw.sta")
    log.file_written("STA/summary_Raw.sta", tag="Phase 8")


# Phase 8a : for power curve Input file
    print("\n[Phase 8a] Power Curve Input file")
    calculator = MultiDensityPowerCalculator()
    output_txt = os.path.join(config.POWER_CURVE_FOLDER, "Power_Curve_Input_file11.txt")
    
    base_folder_path = r"C:\\Akanksha\\Post-Processing\\Output"
    
    
    calculator.process_root_directory(base_folder_path)
    calculator.generate_formatted_text(output_txt)

# Phase 8aa : Power Curve merged file
    base_folder_path = r"C:\\Akanksha\\Post-Processing\\Output\\POWER_CURVE"
    output_txt = r"C:\\Akanksha\\Post-Processing\\Output\\Power_Curve.txt"
    
    merge_power_curve_files(base_folder_path, output_txt)  


    # ── Phase 8b: Family averaged summary files ──────────────────────────────
    print("\n[Phase 8b] Computing family averaged summary files...")
    family_order, family_extreme, family_del, family_plf, family_method, n_seeds = \
        compute_family_stats(
            summary_extreme, summary_del,
            config.FILE_METADATA, config.FATIGUE_FILES,
            sensor_cols_all, config.RFC_M_VALUES)

    # Build family method description for headers
    method_desc = {1: 'mean of all seeds',
                   2: 'mean of worst half seeds',
                   3: 'worst single seed'}
    # Assume all families use same method (take first)
    fam_method_val = list(family_method.values())[0] if family_method else 1
    fam_plf_val    = list(family_plf.values())[0]    if family_plf    else 1.0
    n_seeds_val    = list(n_seeds.values())[0]       if n_seeds       else 0
    half_seeds     = max(1, n_seeds_val // 2)

    # ── Write summary_Family.sta ─────────────────────────────────────────────
    family_header = [
        '# OpenFAST Postprocessing — Family Averaged Statistics Summary',
        '# ' + '-' * 60,
        f'# Content                          : Family-averaged statistics and 1Hz DEL',
        f'# Families                         : {len(family_order)} (wind speed groups)',
        f'# Rows                             : Family names (e.g. DLC11_08mps)',
        f'# Columns                          : Sensor channels + derived Mres sensors',
        f'# PLF                              : Not applied',
        f'# 1Hz DEL Neq                      : T_sim per file (e.g. {config.FILE_METADATA[list(config.FILE_METADATA.keys())[0]]["SimTime"]:.0f} s)',
        f'# Family Method (excludes 1Hz DEL) : 1 = mean of all seeds',
        f'#                                    2 = mean of worst half seeds',
        f'#                                    3 = worst single seed',
        f'# 1Hz DEL family method formula    : DEL_family = ( Σ(DEL_seedᵢ^m) / N_effective )^(1/m)',
        f'#                                        Method 1: N_effective = {n_seeds_val}  (all seeds)',
        f'#                                        Method 2: N_effective = {half_seeds}   (worst half, sorted by 1Hz DEL descending)',
        f'#                                        Method 3: N_effective = 1   (worst single seed)',
    ]
    family_sta_path = os.path.join(config.STA_FOLDER, 'summary_Family.sta')
    write_summary_sta(
        family_sta_path, family_order, sensor_cols_all,
        family_extreme, family_del, config.RFC_M_VALUES,
        header=family_header)
    print(f"  → Written: STA/summary_Family.sta")

    # ── Write summary_FamilyPLF.sta ──────────────────────────────────────────
    # Apply PLF to Max, Min, AbsMax, Range only
    family_extreme_plf = {}
    for fam in family_order:
        plf = family_plf[fam]
        family_extreme_plf[fam] = {}
        for col in sensor_cols_all:
            family_extreme_plf[fam][col] = {}
            for stat, val in family_extreme[fam][col].items():
                family_extreme_plf[fam][col][stat] = (
                    val * plf if stat in PLF_STATS else val)

    plf_header = [
        '# OpenFAST Postprocessing — Family Averaged + PLF Statistics Summary',
        '# ' + '-' * 60,
        f'# Content                          : Family-averaged statistics with PLF applied to design loads',
        f'# Families                         : {len(family_order)} (wind speed groups)',
        f'# Rows                             : Family names (e.g. DLC11_08mps)',
        f'# Columns                          : Sensor channels + derived Mres sensors',
        f'# Family Method (excludes 1Hz DEL) : 1 = mean of all seeds',
        f'#                                    2 = mean of worst half seeds',
        f'#                                    3 = worst single seed',
        f'# 1Hz DEL family method formula    : DEL_family = ( Σ(DEL_seedᵢ^m) / N_effective )^(1/m)',
        f'#                                        Method 1: N_effective = {n_seeds_val}  (all seeds)',
        f'#                                        Method 2: N_effective = {half_seeds}   (worst half, sorted by 1Hz DEL descending)',
        f'#                                        Method 3: N_effective = 1   (worst single seed)',
        f'# 1Hz DEL Neq                      : T_sim per file (e.g. {config.FILE_METADATA[list(config.FILE_METADATA.keys())[0]]["SimTime"]:.0f} s)',
        f'# PLF applied to                   : Max, Min, AbsMax, Range, Mean',
        f'# PLF NOT applied                  : Stdev, RMS, 1Hz DEL (all m values)',
    ]
    fam_plf_sta_path = os.path.join(config.STA_FOLDER, 'summary_FamilyPLF.sta')
    write_summary_sta(
        fam_plf_sta_path, family_order, sensor_cols_all,
        family_extreme_plf, family_del, config.RFC_M_VALUES,
        header=plf_header)
    print(f"  → Written: STA/summary_FamilyPLF.sta")
    log.file_written("STA/summary_Family.sta", tag="Phase 8b")
    log.file_written("STA/summary_FamilyPLF.sta", tag="Phase 8b")

    # ── Phase 9: EXT ranking ─────────────────────────────────────────────────
    # Real sensors: all three stats (Max, Min, AbsMax) with complementary loads
    # Derived sensors (Mres): Max + AbsMax (no Min — near zero; no _comp)
    # Both use the same summary_extreme dict — unified path
    ext_sensors = [c for c in sensor_cols if get_sensor_flags(c)['EXT'] == 1]
    ext_derived  = [ds for ds in derived_active if ds.get('ext', 1)]
    log.phase_start(9, f'EXT ranking — {len(ext_sensors)} real + '
                       f'{len(ext_derived)} derived sensors')
    print(f"\n[Phase 9] EXT rankings: {len(ext_sensors)} real sensors "
          f"+ {len(ext_derived)} derived sensors...")

    ext_stats_map = {'max': 'Max', 'min': 'Min', 'abs': 'AbsMax'}

    n_ext_written  = 0
    n_comp_written = 0

    # ── Real sensors: Max / Min / AbsMax ──────────────────────────────────────
    # for col in ext_sensors:
    #     safe         = sanitize_for_filename(col)
    #     comp_method  = get_sensor_flags(col)['CompMethod']
    #     comp_enabled = get_sensor_flags(col)['Complimentary']

    #     for ext_suffix, stat_key in ext_stats_map.items():
    #         ranked_plf, ranked_noplf = compute_ext_ranking(
    #             summary_extreme, config.FILE_METADATA, col,
    #             stat=stat_key, top_n=10)
    #         if not ranked_plf and not ranked_noplf:
    #             continue
    #         out_path = os.path.join(config.EXT_FOLDER, safe + f'.{ext_suffix}')
    #         write_ext_file(out_path, col, stat_key, ranked_plf, ranked_noplf)
    #         log.file_written(f'EXT/{safe}.{ext_suffix}', tag='Phase 9')
    #         n_ext_written += 1

    # 1. Calculate total expected tasks for the progress bar
    total_tasks = len(ext_sensors) * len(ext_stats_map)

    # 2. Wrap the execution block with tqdm
    with tqdm(total=total_tasks, desc="Ranking Real Sensors", unit="stat") as pbar:
        
        # ── Real sensors: Max / Min / AbsMax ──────────────────────────────────────
        for col in ext_sensors:
            safe         = sanitize_for_filename(col)
            comp_method  = get_sensor_flags(col)['CompMethod']
            comp_enabled = get_sensor_flags(col)['Complimentary']

            for ext_suffix, stat_key in ext_stats_map.items():
                ranked_plf, ranked_noplf = compute_ext_ranking(
                    summary_extreme, config.FILE_METADATA, col,
                    stat=stat_key, top_n=10)
                
                if not ranked_plf and not ranked_noplf:
                    pbar.update(1)  # Still increment progress if skipped
                    continue
                    
                out_path = os.path.join(config.EXT_FOLDER, safe + f'.{ext_suffix}')
                write_ext_file(out_path, col, stat_key, ranked_plf, ranked_noplf)
                
                # Internal log keeping can stay, but direct print statements are gone!
                # log.file_written(f'EXT/{safe}.{ext_suffix}', tag='Phase 9')
                n_ext_written += 1
                
                pbar.update(1)  # Increment progress on successful write

            if comp_enabled:
                comp_results = compute_complementary_loads(
                    sensor_col=col,
                    stat=stat_key,
                    ranked_plf=ranked_plf,
                    all_extreme=summary_extreme,
                    file_metadata=config.FILE_METADATA,
                    sensor_list=config.SENSOR_LIST,
                    input_folder=config.INPUT_FOLDER,
                    time_channel=config.TIME_CHANNEL,
                    top_n=10,
                    derived_active=derived_active)
                if comp_results:
                    comp_path = os.path.join(
                        config.EXT_FOLDER, safe + f'_comp.{ext_suffix}')
                    write_complementary_file(
                        comp_path, col, stat_key, comp_results, comp_method)
                    n_comp_written += 1

    # ── Derived sensors (Mres): Max + AbsMax + _comp files ──────────────────
    # .max  = worst resultant (primary design value)
    # .abs  = AbsMax (same as Max for Mres — written for consistency)
    # .min  = excluded (minimum resultant near zero — no design meaning)
    # _comp.max / _comp.abs = written if Complimentary=1 in Table 3B
    #   Mres computed at time instant from operands inside
    #   compute_complementary_loads() via derived_active parameter
    # n_derived_ext = 0
    # for ds in ext_derived:
    #     dname      = ds['name']
    #     safe       = sanitize_for_filename(dname)
    #     comp_meth  = ds.get('compmethod', 2)
    #     comp_en    = ds.get('comp', 0)

    #     for stat_key, ext_suffix in [('Max', 'max'), ('AbsMax', 'abs')]:
    #         ranked_plf_d, ranked_noplf_d = compute_ext_ranking(
    #             summary_extreme, config.FILE_METADATA, dname,
    #             stat=stat_key, top_n=10)
    #         if not ranked_plf_d and not ranked_noplf_d:
    #             continue
    #         # Write EXT ranking file
    #         out_path = os.path.join(config.EXT_FOLDER, safe + f'.{ext_suffix}')
    #         write_ext_file(out_path, dname, stat_key, ranked_plf_d, ranked_noplf_d)
    #         log.file_written(f'EXT/{safe}.{ext_suffix}', tag='Phase 9')
    #         n_derived_ext += 1
    #         # Write _comp file if Complimentary=1
    #         if comp_en:
    #             comp_results_d = compute_complementary_loads(
    #                 sensor_col=dname,
    #                 stat=stat_key,
    #                 ranked_plf=ranked_plf_d,
    #                 all_extreme=summary_extreme,
    #                 file_metadata=config.FILE_METADATA,
    #                 sensor_list=config.SENSOR_LIST,
    #                 input_folder=config.INPUT_FOLDER,
    #                 time_channel=config.TIME_CHANNEL,
    #                 top_n=10,
    #                 derived_active=derived_active)
    #             if comp_results_d:
    #                 comp_path = os.path.join(
    #                     config.EXT_FOLDER, safe + f'_comp.{ext_suffix}')
    #                 write_complementary_file(
    #                     comp_path, dname, stat_key,
    #                     comp_results_d, comp_meth)
    #                 log.file_written(f'EXT/{safe}_comp.{ext_suffix}', tag='Phase 9')
    #                 n_derived_ext += 1

    # print(f"  → {n_ext_written} EXT files  "
    #       f"({len(ext_sensors)} sensors × 3 types)")
    # print(f"  → {n_comp_written} complementary files")
    # print(f"  → {n_derived_ext} derived sensor EXT + _comp files")
    # log.phase_complete(9, f'{n_ext_written} real EXT + {n_comp_written} _comp + '
    #                    f'{n_derived_ext} derived EXT files written')

    n_derived_ext = 0
    
    # 1. Calculate total expected iterations (2 stats per derived sensor)
    total_derived_tasks = len(ext_derived) * 2

    # 2. Wrap the execution block with tqdm
    with tqdm(total=total_derived_tasks, desc="Ranking Derived Sensors", unit="stat") as pbar:
        for ds in ext_derived:
            dname      = ds['name']
            safe       = sanitize_for_filename(dname)
            comp_meth  = ds.get('compmethod', 2)
            comp_en    = ds.get('comp', 0)

            for stat_key, ext_suffix in [('Max', 'max'), ('AbsMax', 'abs')]:
                ranked_plf_d, ranked_noplf_d = compute_ext_ranking(
                    summary_extreme, config.FILE_METADATA, dname,
                    stat=stat_key, top_n=10)
                
                if not ranked_plf_d and not ranked_noplf_d:
                    pbar.update(1)  # Still update progress if skipped
                    continue
                    
                # Write EXT ranking file
                out_path = os.path.join(config.EXT_FOLDER, safe + f'.{ext_suffix}')
                write_ext_file(out_path, dname, stat_key, ranked_plf_d, ranked_noplf_d)
                # log.file_written(f'EXT/{safe}.{ext_suffix}', tag='Phase 9')
                n_derived_ext += 1
                
                # Write _comp file if Complimentary=1
                if comp_en:
                    comp_results_d = compute_complementary_loads(
                        sensor_col=dname,
                        stat=stat_key,
                        ranked_plf=ranked_plf_d,
                        all_extreme=summary_extreme,
                        file_metadata=config.FILE_METADATA,
                        sensor_list=config.SENSOR_LIST,
                        input_folder=config.INPUT_FOLDER,
                        time_channel=config.TIME_CHANNEL,
                        top_n=10,
                        derived_active=derived_active)
                    if comp_results_d:
                        comp_path = os.path.join(
                            config.EXT_FOLDER, safe + f'_comp.{ext_suffix}')
                        write_complementary_file(
                            comp_path, dname, stat_key,
                            comp_results_d, comp_meth)
                        # log.file_written(f'EXT/{safe}_comp.{ext_suffix}', tag='Phase 9')
                        n_derived_ext += 1
                
                pbar.update(1)  # Update progress after processing the stat block

    # The final summaries print once cleanly at the very end
    print(f"  → {n_ext_written} EXT files  "
          f"({len(ext_sensors)} sensors × 3 types)")
    print(f"  → {n_comp_written} complementary files")
    print(f"  → {n_derived_ext} derived sensor EXT + _comp files")
    log.phase_complete(9, f'{n_ext_written} real EXT + {n_comp_written} _comp + '
                       f'{n_derived_ext} derived EXT files written')

    # ── Phase 10: DLC Fatigue Contribution Analysis ───────────────────────────
    print("\n[Phase 10] Computing DLC fatigue contribution analysis...")
    rfc_sensors = [c for c in sensor_cols if get_sensor_flags(c)['RFC'] == 1]
    if damage_per_file and rfc_sensors:
        contrib_path = os.path.join(config.FAT_FOLDER, 'summary_dlc_contribution.fat')
        write_contribution_file(
            contrib_path,
            damage_per_file,
            config.FILE_METADATA,
            config.FATIGUE_FILES,
            rfc_sensors,
            config.RFC_M_VALUES,
            config.LIFETIME_YEARS,
            config.NEQ_LIFETIME)
        print(f"  → Written: FAT/summary_dlc_contribution.fat")
    else:
        print("  → Skipped: no RFC sensors or no fatigue files with occurrences > 0")

    # ── Phase 11: Component load summary files (.sum) ───────────────────────
    print("\n[Phase 11] Writing component load summary files...")

    # Read blade configuration from sensorList.txt [BLADE] section
    # blade_cfg  = get_blade_config()
    # convention_hint = blade_cfg['CONVENTION']

    # # Detect blade sensors from output file sensor list
    # convention, bld_stations, n_blades = detect_blade_sensors(
    #     sensor_cols)

    # # Override convention if explicitly set
    # if convention_hint != 'AUTO' and convention_hint in ('ED', 'BD'):
    #     print(f"  → Convention override: {convention_hint}")

    # if convention == 'None':
    #     print("  WARNING: No blade sensors detected — BldLoads.sum skipped")
    #     print("           Check sensorList.txt [BLADE] section and OpenFAST output channels")
    # else:
    #     print(f"  → Detected {convention} convention, "
    #           f"{n_blades} blade(s), "
    #           f"{len(bld_stations)} stations")

    #     # Read radial positions — prefer ElastoDyn primary file (correct
    #     # OpenFAST formula); fall back to BLADE_FILE_PATH if ED file absent.
    #     # The ED path is shared with TWR_ED_FILE under [TOWER] in sensorList.
    #     twr_cfg_for_ed = get_tower_config(log=log)
    #     # NOTE: get_tower_config consumes the path internally; expose it here.
    #     from config import COMPONENT_CONFIG as _CC
    #     ed_file_path_for_blade = _CC.get('TOWER', {}).get('TWR_ED_FILE', '').strip()
    #     if ed_file_path_for_blade.upper() == 'NONE':
    #         ed_file_path_for_blade = ''
    #     radial_src = ed_file_path_for_blade or blade_cfg['BLADE_FILE_PATH']
    #     radial_pos = read_radial_positions(
    #         radial_src,
    #         blade_cfg['BLADE_LENGTH'],
    #         list(bld_stations.keys()),
    #         log=log)

    #     # Write BldLoads.sum
    #     bld_sum_path = os.path.join(config.SUM_FOLDER, 'BldLoads.sum')
    #     write_bld_loads_sum(
    #         output_path    = bld_sum_path,
    #         stations       = bld_stations,
    #         radial_pos     = radial_pos,
    #         family_order   = family_order,
    #         sensor_cols    = sensor_cols_all,
    #         m_values_sum   = blade_cfg['SUM_DEL_SLOPES'],
    #         lifetime_years = config.LIFETIME_YEARS,
    #         neq_lifetime   = config.NEQ_LIFETIME,
    #         ext_folder     = config.EXT_FOLDER,
    #         fat_folder     = config.FAT_FOLDER,
    #         log            = log,
    #         n_blades       = n_blades)
    #     print(f"  → Written: SUM/BldLoads.sum")
    #     log.file_written("SUM/BldLoads.sum", tag="Phase 11")

    # 1. Get parsed config dictionary
    blade_cfg = get_blade_config()
    convention_hint = blade_cfg['CONVENTION']

    # 2. Determine stations based on AUTO vs EXPLICIT logic
    if convention_hint == 'EXPLICIT':
        print(f"  → Convention override: EXPLICIT (Reading sensors directly from sensorList.txt)")
        convention = 'EXPLICIT'
        bld_stations = blade_cfg['stations']
        n_blades = blade_cfg['n_blades']
    else:
        # Fallback to legacy auto-detection
        convention, bld_stations, n_blades = detect_blade_sensors(sensor_cols)
        if convention_hint != 'AUTO' and convention_hint in ('ED', 'BD'):
            print(f"  → Convention override: {convention_hint}")

    if convention == 'None' or not bld_stations:
        print("  WARNING: No blade sensors detected or defined — BldLoads.sum skipped")
        print("           Check sensorList.txt [BLADE] section and OpenFAST output channels")
    else:
        print(f"  → Detected {convention} convention, "
              f"{n_blades} blade(s), "
              f"{len(bld_stations)} stations")

        # Read radial positions — prefer ElastoDyn primary file
        twr_cfg_for_ed = get_tower_config(log=log)
        
        from config import COMPONENT_CONFIG as _CC
        ed_file_path_for_blade = _CC.get('TOWER', {}).get('TWR_ED_FILE', '').strip()
        if ed_file_path_for_blade.upper() == 'NONE':
            ed_file_path_for_blade = ''
            
        radial_src = ed_file_path_for_blade or blade_cfg['BLADE_FILE_PATH']
        radial_pos = read_radial_positions(
            radial_src,
            blade_cfg['BLADE_LENGTH'],
            list(bld_stations.keys()),
            log=log)

        # Write BldLoads.sum
        bld_sum_path = os.path.join(config.SUM_FOLDER, 'BldLoads.sum')
        write_bld_loads_sum(
            output_path    = bld_sum_path,
            stations       = bld_stations,    # This will now contain your parsed explicitly defined sensors
            radial_pos     = radial_pos,
            family_order   = family_order,
            sensor_cols    = sensor_cols_all,
            m_values_sum   = blade_cfg['SUM_DEL_SLOPES'],
            lifetime_years = config.LIFETIME_YEARS,
            neq_lifetime   = config.NEQ_LIFETIME,
            ext_folder     = config.EXT_FOLDER,
            fat_folder     = config.FAT_FOLDER,
            log            = log,
            n_blades       = n_blades)
            
        print(f"  → Written: SUM/BldLoads.sum")
        log.file_written("SUM/BldLoads.sum", tag="Phase 11")

    # ── HubLoads.sum ──────────────────────────────────────────────────────────
    hub_cfg = get_hub_config()
    hub_map = hub_cfg['sensor_map']


    # print(f"DEBUG: Available sensors are: {sensor_cols[:10]}...") 
    # print(f"DEBUG: Looking for: {hub_map.get('Mx', {}).get('B1')}")



    # Check if any hub sensors are configured
    hub_sensors_found = any(
        hub_map.get(comp, {}).get(b) in sensor_cols
        for comp in ['Mx','My','Mz','Fx','Fy','Fz']
        for b in ['B1','B2','B3']
    )
    if not hub_sensors_found:
        print("  WARNING: No hub sensors found — HubLoads.sum skipped")
        print("           Check [HUB] section in sensorList.txt")
    else:
        hub_sum_path = os.path.join(config.SUM_FOLDER, 'HubLoads.sum')
        write_hub_loads_sum(
            output_path        = hub_sum_path,
            hub_sensor_map     = hub_map,
            family_order       = family_order,
            sensor_cols        = sensor_cols,
            m_values_hub       = hub_cfg['del_slopes'],
            lifetime_years     = config.LIFETIME_YEARS,
            neq_lifetime       = config.NEQ_LIFETIME,
            ext_folder         = config.EXT_FOLDER,
            fat_folder         = config.FAT_FOLDER,
            log                = log)
        print(f"  → Written: SUM/HubLoads.sum")
        log.file_written("SUM/HubLoads.sum", tag="Phase 11")

    # ── TwrLoads.sum ──────────────────────────────────────────────────────────
    twr_cfg = get_tower_config()
    # Check if any tower sensors are configured
    twr_sensors_found = any(
        st.get(comp) in sensor_cols_all
        for st in twr_cfg['stations']
        for comp in ['Mres', 'Mx', 'My', 'Mz', 'Fx', 'Fy', 'Fz']
        if st.get(comp)
    )
    if not twr_sensors_found:
        print("  WARNING: No tower sensors found — TwrLoads.sum skipped")
        print("           Check [TOWER] section in sensorList.txt")
    else:
        twr_sum_path = os.path.join(config.SUM_FOLDER, 'TwrLoads.sum')
        write_twr_loads_sum(
            output_path        = twr_sum_path,
            twr_config         = twr_cfg,
                                    family_order       = family_order,
            sensor_cols        = sensor_cols_all,
            lifetime_years     = config.LIFETIME_YEARS,
            neq_lifetime       = config.NEQ_LIFETIME,
                ext_folder         = config.EXT_FOLDER,
                fat_folder         = config.FAT_FOLDER,
                log                = log)
        print(f"  → Written: SUM/TwrLoads.sum")
        log.file_written("SUM/TwrLoads.sum", tag="Phase 11")

    # ── YawLoads.sum ──────────────────────────────────────────────────────────
    yaw_cfg = get_yaw_config()
    yaw_sensors_found = any(
        s in sensor_cols_all
        for group in [yaw_cfg["loads"], yaw_cfg["accel_trans"], yaw_cfg["accel_ang"]]
        for s in group.values()
        if s
    )
    if not yaw_sensors_found:
        print("  WARNING: No yaw sensors found — YawLoads.sum skipped")
        print("           Check [YAW] section in sensorList.txt")
    else:
        # Build per-sensor lifetime RFC DEL dict
        # Combine real sensor RFC DEL and derived sensor RFC DEL
        yaw_rfc_del = {}
        for col in sensor_cols_all:
            if col in lifetime_del_rfc:
                yaw_rfc_del[col] = lifetime_del_rfc[col]
        yaw_sum_path = os.path.join(config.SUM_FOLDER, "YawLoads.sum")
        write_yaw_loads_sum(
            output_path        = yaw_sum_path,
            yaw_config         = yaw_cfg,
                                                family_order       = family_order,
            sensor_cols        = sensor_cols_all,
            lifetime_years     = config.LIFETIME_YEARS,
            neq_lifetime       = config.NEQ_LIFETIME,
                ext_folder         = config.EXT_FOLDER,
                fat_folder         = config.FAT_FOLDER,
                log                = log)
        print(f"  → Written: SUM/YawLoads.sum")
        log.file_written("SUM/YawLoads.sum", tag="Phase 11")

    # ── DRTLoads.sum ──────────────────────────────────────────────────
    drt_cfg = get_drivetrain_config()
    drt_sensors = (
        list(drt_cfg["a_frame"].values()) +
        list(drt_cfg["s_frame"].values()) +
        drt_cfg["ldd_lrd_sensors"])
    drt_found = any(s in sensor_cols_all for s in drt_sensors if s)
    if not drt_found:
        print("  WARNING: No drivetrain sensors found — DRTLoads.sum skipped")
        log.warning("No drivetrain sensors found — DRTLoads.sum skipped",
                    tag="Phase 11")
    else:
        drt_sum_path = os.path.join(config.SUM_FOLDER, "DRTLoads.sum")
        write_drt_loads_sum(
            output_path    = drt_sum_path,
            drt_config     = drt_cfg,
            family_order   = family_order,
            sensor_cols    = sensor_cols_all,
            lifetime_years = config.LIFETIME_YEARS,
            neq_lifetime   = config.NEQ_LIFETIME,
            ext_folder     = config.EXT_FOLDER,
            fat_folder     = config.FAT_FOLDER,
            log            = log)
        print(f"  → Written: SUM/DRTLoads.sum")
        log.file_written("SUM/DRTLoads.sum", tag="Phase 11")

    # ── FNDLoads.sum ──────────────────────────────────────────────────
    fnd_cfg = get_foundation_config()
    fnd_sensors_found = any(
        s in sensor_cols_all
        for s in fnd_cfg["sensors"].values() if s)
    if not fnd_sensors_found:
        print("  WARNING: No foundation sensors found — FNDLoads.sum skipped")
        log.warning("No foundation sensors found — FNDLoads.sum skipped",
                    tag="Phase 11")
    else:
        fnd_sum_path = os.path.join(config.SUM_FOLDER, "FNDLoads.sum")
        write_fnd_loads_sum(
            output_path    = fnd_sum_path,
            fnd_config     = fnd_cfg,
            family_order   = family_order,
            sensor_cols    = sensor_cols_all,
            lifetime_years = config.LIFETIME_YEARS,
            neq_lifetime   = config.NEQ_LIFETIME,
            ext_folder     = config.EXT_FOLDER,
            fat_folder     = config.FAT_FOLDER,
            log            = log)
        print(f"  → Written: SUM/FNDLoads.sum")
        log.file_written("SUM/FNDLoads.sum", tag="Phase 11")

    # ── PitchBearing.sum ──────────────────────────────────────────────
    ptb_cfg = get_pitch_bearing_config()
    ptb_sensors_found = any(
        s in sensor_cols_all
        for b in ptb_cfg["blades"].values()
        for s in b.values() if s)
    if not ptb_sensors_found:
        print("  WARNING: No pitch bearing sensors found — PitchBearing.sum skipped")
        log.warning("No pitch bearing sensors found — PitchBearing.sum skipped",
                    tag="Phase 11")
    else:
        ptb_sum_path = os.path.join(config.SUM_FOLDER, "PitchBearing.sum")
        write_pitch_bearing_sum(
            output_path    = ptb_sum_path,
            ptb_config     = ptb_cfg,
            family_order   = family_order,
            sensor_cols    = sensor_cols_all,
            lifetime_years = config.LIFETIME_YEARS,
            neq_lifetime   = config.NEQ_LIFETIME,
            ext_folder     = config.EXT_FOLDER,
            fat_folder     = config.FAT_FOLDER,
            log            = log)
        print(f"  → Written: SUM/PitchBearing.sum")
        log.file_written("SUM/PitchBearing.sum", tag="Phase 11")

    # ── PitchBearing.sum ──────────────────────────────────────────────────
    # ptb_cfg = get_pitch_bearing_config()
    # ptb_sensors_found = any(
    #     s in sensor_cols_all
    #     for b in ptb_cfg["blades"].values()
    #     for s in b.values() if s)
    # if not ptb_sensors_found:
    #     print("  WARNING: No pitch bearing sensors found — PitchBearing.sum skipped")
    #     log.warning("No pitch bearing sensors found — PitchBearing.sum skipped",
    #                 tag="Phase 11")
    # else:
    #     ptb_sum_path = os.path.join(config.SUM_FOLDER, "PitchBearing.sum")
    #     write_pitch_bearing_sum(
    #         output_path    = ptb_sum_path,
    #         ptb_config     = ptb_cfg,
    #         family_order   = family_order,
    #         sensor_cols    = sensor_cols_all,
    #         lifetime_years = config.LIFETIME_YEARS,
    #         neq_lifetime   = config.NEQ_LIFETIME,
    #         ext_folder     = config.EXT_FOLDER,
    #         fat_folder     = config.FAT_FOLDER,
    #         log            = log)
    #     print(f"  -> Written: SUM/PitchBearing.sum")
    #     log.file_written("SUM/PitchBearing.sum", tag="Phase 11")

    # ── MainLoads.sum ─────────────────────────────────────────────────
    main_sum_path = os.path.join(config.SUM_FOLDER, "MainLoads.sum")
    write_main_loads_sum(
        output_path    = main_sum_path,
        sum_folder     = config.SUM_FOLDER,
        ext_folder     = config.EXT_FOLDER,
        lifetime_years = config.LIFETIME_YEARS,
        neq_lifetime   = config.NEQ_LIFETIME,
        log            = log)
    print(f"  -> Written: SUM/MainLoads.sum")
    log.file_written("SUM/MainLoads.sum", tag="Phase 11")

    # ── Done ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  Processing complete.")
    print(f"  Output structure:")
    print(f"    {config.STA_FOLDER}/")
    print(f"      {len(summary_files)} × .sta  (per-file statistics)")
    print(f"      1 × summary_Raw.sta       (raw, all files)")
    print(f"      1 × summary_Family.sta    (family averaged)")
    print(f"      1 × summary_FamilyPLF.sta (family averaged + PLF)")
    print(f"    {config.FAT_FOLDER}/")
    print(f"      {len(fatigue_sensors)} × .rfc    (RFC spectrum)")
    print(f"      {len(fatigue_sensors)} × .markov  (Markov matrix)")
    print(f"      {len(fatigue_sensors)} × .ldd    (LDD)")
    print(f"      {len(fatigue_sensors)} × .lrd    (LRD)")
    print(f"    {config.EXT_FOLDER}/")
    print(f"      {n_ext_written} × .max/.min/.abs           (EXT rankings)")
    print(f"      {n_comp_written} × _comp.max/.min/.abs   (complementary loads)")
    print(f"      1 × summary_dlc_contribution.fat (DLC fatigue contribution)")
    print(f"    {config.SUM_FOLDER}/")
    print(f"      BldLoads.sum  (blade load summary)")
    print(f"      HubLoads.sum  (hub load summary)")
    print(f"      TwrLoads.sum  (tower load summary)")
    print(f"      YawLoads.sum  (yaw system load summary)")
    print(f"      DRTLoads.sum  (drivetrain load summary)")
    print(f"      FNDLoads.sum      (foundation load summary)")
    print(f"      PitchBearing.sum  (pitch bearing load summary)")
    print(f"      MainLoads.sum     (master turbine load summary)")
    # print(f"      YawLoads.sum  (yaw bearing load summary)")
    print("=" * 70)


if __name__ == '__main__':
    main()
    log.close(success=True)
