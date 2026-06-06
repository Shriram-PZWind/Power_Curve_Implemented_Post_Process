# =============================================================================
# sum_writer.py
# OpenFAST Postprocessing — Component Load Summary File Writers
#
# Writes .sum files for each turbine component:
#   BldLoads.sum   — blade root + spanwise loads
#   (future: DRTLoads, TwrLoads, FNDLoads, YawLoads, HubLoads, MainLoads)
# =============================================================================

import os
import re
import math
from blade_reader import format_station_label
from tower_reader import format_station_label_twr
from config import fmt_slope
from io_reader import sanitize_for_filename


# =============================================================================
# FAT file DEL reader — single source of truth for all .sum DEL values
# =============================================================================

# def read_fat_del_header(fat_folder, sensor_name, m_value, method='rfc', log=None):
#     """
#     Read a single lifetime DEL value from a FAT file header.

#     FAT file DEL header format (lines starting with #):
#     # Lifetime DEL    DEL_m3    DEL_m4  ... DEL_m25
#     # [units]         456.78    389.21  ...

#     Parameters
#     ----------
#     fat_folder  : str  — path to FAT/ folder
#     sensor_name : str  — exact sensor name
#     m_value     : int  — Wöhler slope (e.g. 4)
#     method      : str  — 'rfc', 'ldd', or 'lrd'
#     log         : PostProcessLogger or None

#     Returns float or None.
#     """
#     safe  = sanitize_for_filename(sensor_name)
#     fpath = os.path.join(fat_folder, safe + f'.{method}')

#     if not os.path.isfile(fpath):
#         if log:
#             log.warning(f'{safe}.{method} not found — N/A in .sum', tag='SUM')
#         return None

#     try:
#         with open(fpath, 'r', encoding='utf-8') as fh:
#             lines = fh.readlines()

#         target = f'DEL_m{fmt_slope(m_value)}'
#         for i, line in enumerate(lines):
#             stripped = line.strip()
#             if 'Lifetime DEL' in stripped and target in stripped:
#                 # Parse column positions from this header line
#                 parts = stripped.lstrip('#').split()
#                 if target not in parts:
#                     break
#                 col_idx = parts.index(target)  # 0-based in parts list
#                 # Next comment line has values — skip 'Lifetime DEL' label
#                 # col_idx=0 is 'Lifetime', col_idx=1 is 'DEL', col_idx=2 is 'DEL_m3'...
#                 # Value index in value line = col_idx - 2 (skip 'Lifetime DEL' two tokens)
#                 # But header is: '# Lifetime DEL  DEL_m3  DEL_m4 ...'
#                 # So parts = ['Lifetime', 'DEL', 'DEL_m3', 'DEL_m4', ...]
#                 # And value line: '# [units]  val_m3  val_m4 ...'
#                 # So value index = col_idx - 2
#                 val_idx = col_idx - 2
#                 for j in range(i + 1, min(i + 6, len(lines))):
#                     vline = lines[j].strip()
#                     if vline.startswith('#'):
#                         vparts = vline.lstrip('#').split()
#                         # vparts[0] = units label, vparts[1..] = values
#                         try:
#                             return float(vparts[val_idx])
#                         except (IndexError, ValueError):
#                             break
#                 break
#             elif 'Lifetime DEL' in stripped and target not in stripped:
#                 # Header line found but target m not in it
#                 if log:
#                     log.slope_mismatch(sensor_name, m_value,
#                                        os.path.basename(fpath))
#                 return None
#     except Exception as e:
#         if log:
#             log.warning(f'Error reading {os.path.basename(fpath)}: {e}', tag='SUM')
#     return None

_FAT_HEADER_CACHE = {}
_EXT_LOAD_CACHE = {}

def read_fat_del_header(fat_folder, sensor_name, m_value, method='rfc', log=None):
    """
    Corrected reader that handles multi-word unit strings by indexing from the right.
    """
    import os

    cache_key = (fat_folder, sensor_name, float(m_value), method)
    if cache_key in _FAT_HEADER_CACHE:
        return _FAT_HEADER_CACHE[cache_key]
 
    # 1. Strip unit suffixes like _[kN-m] so we find the actual file "TwrBsMxt.rfc"
    # clean_name = sensor_name.split('[')[0].strip().rstrip('_')
    # print("sensor name", sensor_name)
    safe = sanitize_for_filename(sensor_name)
    # print("sanitize is ", safe)
    fpath = os.path.join(fat_folder, safe + f'.{method}')
 
    # print("fat search akash folder ", fpath)
 
    if not os.path.isfile(fpath):
        if log:
            log.warning(f'{safe}.{method} not found in FAT folder', tag='SUM')
        return None
 
    try:
        # Match the "DEL_m3.0" format seen in your image
        target = f'DEL_m{float(m_value):.1f}'
       
        with open(fpath, 'r', encoding='utf-8') as fh:
            lines = fh.readlines()
 
        for i, line in enumerate(lines):
            stripped = line.strip()
           
            # 2. Find the header line containing "Lifetime DEL"
            if 'Lifetime DEL' in stripped and target in stripped:
                header_parts = stripped.lstrip('#').split()
               
                # Identify only the slope columns (DEL_m...)
                slope_cols = [p for p in header_parts if p.startswith('DEL_m')]
                if target not in slope_cols:
                    continue
               
                # Find which position our target is in (e.g., 0th slope, 1st slope...)
                target_idx = slope_cols.index(target)
                total_slopes = len(slope_cols)
 
                # 3. The very next line starting with # contains the values
                for j in range(i + 1, i + 5):
                    if j >= len(lines): break
                    v_line = lines[j].strip()
                   
                    if v_line.startswith('#') and any(char.isdigit() for char in v_line):
                        v_parts = v_line.lstrip('#').split()
                       
                        # LOGIC: The values are the LAST 'total_slopes' items on the line.
                        # We ignore the units by counting from the end of the list.
                        # try:
                        #     # e.g., if there are 9 slopes, v_parts[-9] is the first value
                        #     value_str = v_parts[-total_slopes + target_idx]
                        #     return float(value_str)
                        # except (ValueError, IndexError):
                        #     continue

                        try:
                            value_str = v_parts[-total_slopes + target_idx]
                            res = float(value_str)
                            _FAT_HEADER_CACHE[cache_key] = res  # Store in Cache
                            return res
                        except (ValueError, IndexError):
                            continue
                break
 
    except Exception as e:
        if log:
            log.warning(f'Error reading {safe}.{method}: {e}', tag='SUM')

    _FAT_HEADER_CACHE[cache_key] = None       
    return None
 

# ─── Column widths ────────────────────────────────────────────────────────────
_W_STATION = 18    # radial station label column
_W_VAL     = 20    # numeric value + [Bx] column
_W_SRC     = 28    # DLC source column (wider to fit 'DLC_name (PLF=x.xx) [Bx]')
_COMPONENTS = ['Mres', 'Mx', 'My', 'Mz', 'Fx', 'Fy', 'Fz']


def _divider(n_cols, w_station=_W_STATION, w_col=_W_VAL, char='='):
    return char * (w_station + n_cols * w_col)


def _subdiv(n_cols, w_station=_W_STATION, w_col=_W_VAL):
    return '.' * (w_station + n_cols * w_col)


def _header_line(label, w_station, components, units, w_col):
    """Build column header line."""
    line = f"#  {label:<{w_station}}"
    for comp, unit in zip(components, units):
        hdr = f"{comp}_{unit}"
        line += f"{hdr:>{w_col}}"
    return line


def write_bld_loads_sum(output_path, stations, radial_pos,
                        family_order, sensor_cols,
                        m_values_sum, lifetime_years, neq_lifetime,
                        ext_folder=None, fat_folder=None, log=None,
                        n_blades=3):
    """
    Write BldLoads.sum — blade load summary file.

    Parameters
    ----------
    output_path       : str — full path to BldLoads.sum
    stations          : dict {station_label: {blade: {component: sensor_name}}}
                        from blade_reader.detect_blade_sensors()
    radial_pos        : dict {station_label: float or None}
    ext_folder        : str — path to EXT/ folder (reads extremes from headers)
    family_order      : list of str — family names in LC order
    sensor_cols       : list of str — all available sensor columns
    m_values_sum      : list of int — DEL slopes to include (e.g. [10, 12, 25])
    lifetime_years    : float
    neq_lifetime      : float
    n_blades          : int
    """
    station_labels = list(stations.keys())
    n_stations     = len(station_labels)
    n_comp         = len(_COMPONENTS)
    div            = _divider(n_comp)
    subdiv_dot     = _subdiv(n_comp)

    # Units per component
    units = []
    for comp in _COMPONENTS:
        if comp.startswith('M'):
            units.append('[kN-m]')
        else:
            units.append('[kN]')

    def get_val(family, station_lbl, blade, comp, stat, use_plf=True):
        """
        Get value from EXT file header. Returns None if sensor missing.

        For Mres: computed at the time-instant Max (always non-negative).
        Mres has no meaningful Min — sqrt(Mx_min² + My_min²) is incoherent
        because Mx_min and My_min may not occur at the same time instant
        and Mres is by definition non-negative. Returns None for Min stat.
        """
        if comp == 'Mres':
            # Mres is non-negative — Min stat is undefined.
            if stat == 'Min':
                return None
            mx_val = _get_component_val(station_lbl, blade, 'Mx', stat, use_plf)
            my_val = _get_component_val(station_lbl, blade, 'My', stat, use_plf)
            if mx_val is None or my_val is None:
                return None
            return math.sqrt(mx_val**2 + my_val**2)
        return _get_component_val(station_lbl, blade, comp, stat, use_plf)

    def _get_component_val(station_lbl, blade, comp, stat, use_plf=True):
        """Read single component value from EXT file header."""
        sensor = stations.get(station_lbl, {}).get(blade, {}).get(comp)
        if sensor is None or sensor not in sensor_cols:
            return None
        ext_type = 'max' if stat == 'Max' else 'min' if stat == 'Min' else 'abs'
        val, plf, fam = read_ext_design_load(
            ext_folder, sensor, ext_type, with_plf=use_plf, log=log)
        return val

    def get_del(family, station_lbl, blade, comp, m):
        """
        Get DEL value. Returns None if sensor missing.
        For Mres: use worst DEL of Mx and My (conservative).
        """
        if comp == 'Mres':
            mx_del = _get_component_del(station_lbl, blade, 'Mx', m)
            my_del = _get_component_del(station_lbl, blade, 'My', m)
            if mx_del is None and my_del is None:
                return None
            vals = [v for v in [mx_del, my_del] if v is not None]
            return max(vals)   # conservative: worst bending component DEL
        return _get_component_del(station_lbl, blade, comp, m)

    def _get_component_del(station_lbl, blade, comp, m):
        """Read single component DEL from FAT file header."""
        sensor = stations.get(station_lbl, {}).get(blade, {}).get(comp)
        if sensor is None or sensor not in sensor_cols:
            return None
        if fat_folder is None:
            return None
        return read_fat_del_header(fat_folder, sensor, m, method='rfc', log=log)

    def worst_across_blades(family_order, station_lbl, comp, stat,
                            use_plf=True, higher_is_worse=True):
        """
        Find worst value across all blades across all families.
        Returns (worst_value, best_family, best_blade, best_plf)
        or (None, None, None, None).
        For Mres: no PLF returned (computed, not read from file).
        """
        best_val   = None
        best_fam   = None
        best_blade = None
        best_plf   = None
        for fam in family_order:
            for blade in range(1, n_blades + 1):
                val = get_val(fam, station_lbl, blade, comp, stat, use_plf)
                if val is None:
                    continue
                if best_val is None:
                    best_val, best_fam, best_blade = val, fam, blade
                    # Get PLF from Mx sensor for this blade/station
                    sensor = stations.get(station_lbl, {}).get(blade, {}).get(
                        'Mx' if comp == 'Mres' else comp)
                    if sensor and sensor in sensor_cols and ext_folder:
                        ext_type = ('max' if stat == 'Max'
                                    else 'min' if stat == 'Min' else 'abs')
                        _, plf_v, _ = read_ext_design_load(
                            ext_folder, sensor, ext_type,
                            with_plf=True, log=log)
                        best_plf = plf_v
                elif higher_is_worse and val > best_val:
                    best_val, best_fam, best_blade = val, fam, blade
                    sensor = stations.get(station_lbl, {}).get(blade, {}).get(
                        'Mx' if comp == 'Mres' else comp)
                    if sensor and sensor in sensor_cols and ext_folder:
                        ext_type = ('max' if stat == 'Max'
                                    else 'min' if stat == 'Min' else 'abs')
                        _, plf_v, _ = read_ext_design_load(
                            ext_folder, sensor, ext_type,
                            with_plf=True, log=log)
                        best_plf = plf_v
                elif not higher_is_worse and val < best_val:
                    best_val, best_fam, best_blade = val, fam, blade
                    sensor = stations.get(station_lbl, {}).get(blade, {}).get(
                        'Mx' if comp == 'Mres' else comp)
                    if sensor and sensor in sensor_cols and ext_folder:
                        ext_type = ('max' if stat == 'Max'
                                    else 'min' if stat == 'Min' else 'abs')
                        _, plf_v, _ = read_ext_design_load(
                            ext_folder, sensor, ext_type,
                            with_plf=True, log=log)
                        best_plf = plf_v
        return best_val, best_fam, best_blade, best_plf

    def worst_del_across_blades(family_order, station_lbl, comp, m):
        """Find worst (highest) DEL across all blades and families."""
        best_val, best_blade = None, None
        for fam in family_order:
            for blade in range(1, n_blades + 1):
                val = get_del(fam, station_lbl, blade, comp, m)
                if val is None:
                    continue
                if best_val is None or val > best_val:
                    best_val, best_blade = val, blade
        return best_val, best_blade

    def fmt_val_blade(val, blade):
        """Format value with blade tag: '1234.56 [B1]'"""
        if val is None:
            return f"{'N/A [--]':>{_W_VAL}}"
        tag = f"[B{blade}]" if blade else "[--]"
        s = f"{val:.2f} {tag}"
        return f"{s:>{_W_VAL}}"

    def fmt_src(fam):
        """Format DLC source."""
        if fam is None:
            return f"{'N/A':>{_W_SRC}}"
        return f"{fam:>{_W_SRC}}"

    with open(output_path, 'w', encoding='utf-8') as f:

        # ── File header ───────────────────────────────────────────────────────
        f.write(f'# {"=" * 70}\n')
        f.write(f'# BldLoads.sum — Blade Load Summary\n')
        f.write(f'# {"=" * 70}\n')
        f.write(f'# Lifetime    : {lifetime_years:.0f} years\n')
        f.write(f'# Neq (RFC)   : {neq_lifetime:.3g}\n')
        f.write(f'# Blades      : {n_blades}\n')
        f.write(f'# Stations    : {n_stations} '
                f'(Root + {n_stations - 1} spanwise)\n')
        f.write(f'# Note        : Worst case across all {n_blades} blades shown\n')
        f.write(f'#               Values include PLF where stated\n')
        f.write(f'#               N/A = sensor not found in OpenFAST output\n')
        f.write(f'# {"=" * 70}\n\n')

        def write_extreme_section(stat, section_num, label, higher_is_worse):
            f.write(f'# {"═" * 70}\n')
            f.write(f'# SECTION {section_num} — {label} EXTREME LOADS (with PLF)\n')
            f.write(f'# {"═" * 70}\n#\n')
            f.write(_header_line('Radial_[m]', _W_STATION,
                                 _COMPONENTS, units, _W_VAL) + '\n')
            f.write(f'# {div}\n')

            # Collect worst values and sources per station
            val_rows = []
            src_rows = []
            for lbl in station_labels:
                r = radial_pos.get(lbl)
                row_label = format_station_label(lbl, r)
                val_parts = []
                src_parts = []
                for comp in _COMPONENTS:
                    val, fam, blade, plf = worst_across_blades(
                        family_order, lbl, comp, stat,
                        use_plf=True,
                        higher_is_worse=higher_is_worse)
                    val_parts.append(fmt_val_blade(val, blade))
                    # DLC source row: 'DLC_name (PLF=x.xx) [Bx]'
                    plf_s = f'(PLF={plf:.2f})' if plf else '(PLF=—)'
                    bld_s = f'[B{blade}]' if blade else '[--]'
                    src_str = (f'{fam} {plf_s} {bld_s}'
                               if fam else 'N/A')
                    src_parts.append(fmt_src(src_str))
                val_rows.append((row_label, val_parts))
                src_rows.append((row_label, src_parts))

            # Write value rows (top 10)
            for row_label, parts in val_rows:
                f.write(f"   {row_label:<{_W_STATION}}")
                for p in parts:
                    f.write(p)
                f.write('\n')

            # Dotted separator
            f.write(f'# {subdiv_dot}\n')

            # Write DLC source rows (bottom 10)
            for row_label, parts in src_rows:
                f.write(f"   {row_label:<{_W_STATION}}")
                for p in parts:
                    f.write(p)
                f.write('\n')

            f.write(f'# {div}\n#\n#\n')

        # ── Section 1: Maximum ────────────────────────────────────────────────
        write_extreme_section('Max', 1, 'MAXIMUM', higher_is_worse=True)

        # ── Section 2: Minimum ────────────────────────────────────────────────
        write_extreme_section('Min', 2, 'MINIMUM', higher_is_worse=False)

        # ── Sections 3-N: DEL for each m value ───────────────────────────────
        for sec_idx, m in enumerate(m_values_sum, start=3):
            f.write(f'# {"═" * 70}\n')
            f.write(f'# SECTION {sec_idx} — LIFETIME DEL  m = {m}'
                    f'  (Neq = {neq_lifetime:.3g}, RFC method)\n')
            f.write(f'# {"═" * 70}\n#\n')
            f.write(_header_line('Radial_[m]', _W_STATION,
                                 _COMPONENTS, units, _W_VAL) + '\n')
            f.write(f'# {div}\n')

            for lbl in station_labels:
                r = radial_pos.get(lbl)
                row_label = format_station_label(lbl, r)
                f.write(f"   {row_label:<{_W_STATION}}")
                for comp in _COMPONENTS:
                    val, blade = worst_del_across_blades(
                        family_order, lbl, comp, m)
                    f.write(fmt_val_blade(val, blade))
                f.write('\n')

            f.write(f'# {div}\n#\n#\n')

        f.write(f'# {"=" * 70}\n')
        f.write('# END OF FILE\n')
        f.write(f'# {"=" * 70}\n')


# =============================================================================
# HubLoads.sum writer
# =============================================================================

def write_hub_loads_sum(output_path, hub_sensor_map,
                        family_order, sensor_cols,
                        m_values_hub, lifetime_years, neq_lifetime,
                        ext_folder=None, fat_folder=None, log=None,
                        n_blades=3):
    """
    Write HubLoads.sum — hub load summary file.

    Uses blade root sensors in hub coordinate system (RootMxb1/2/3 etc.).
    Worst case across all 3 blades shown for both extreme and fatigue.

    Parameters
    ----------
    hub_sensor_map   : dict — from get_hub_config(), maps component to sensors
                       e.g. {'Mx': {'B1': 'RootMxb1_[kN-m]', 'B2': ..., 'B3': ...}, ...}
    ext_folder       : str — path to EXT/ folder
    family_order     : list of str
    sensor_cols      : list of str — all available sensors
    m_values_hub     : list of int — DEL slopes (e.g. [3, 6])
    lifetime_years   : float
    neq_lifetime     : float
    n_blades         : int
    """
    # Components and their units. Mres is included only if the user mapped
    # at least one HUB_Mres_B* in sensorList.txt; otherwise it's hidden so
    # legacy configs (no Mres) keep producing the same 6-row tables.
    base_components = ['Mx', 'My', 'Mz', 'Fx', 'Fy', 'Fz']
    has_mres = any(
        hub_sensor_map.get('Mres', {}).get(f'B{b}')
        for b in (1, 2, 3)
    )
    components = (['Mres'] + base_components) if has_mres else base_components
    comp_units = {
        'Mres': 'kN-m',
        'Mx': 'kN-m', 'My': 'kN-m', 'Mz': 'kN-m',
        'Fx': 'kN',   'Fy': 'kN',   'Fz': 'kN'
    }
    # Override Mres unit from the actual configured sensor so case is correct
    if has_mres:
        for b in (1, 2, 3):
            s = hub_sensor_map.get('Mres', {}).get(f'B{b}')
            if s:
                comp_units['Mres'] = _get_unit(s)
                break

    # Column widths
    w_sensor = 14
    w_val    = 24
    w_plf    = 8
    w_src    = 24
    div      = '=' * (w_sensor + w_val + w_plf + w_src + 6)
    subdiv   = '-' * (w_sensor + w_val + w_plf + w_src + 6)

    def get_absmax_across_blades(comp, family_order):
        """
        Find worst AbsMax across all blades and families.

        For each blade, reads the .abs design extreme from the EXT file.
        Returns the highest absolute value across all blades × families,
        with the corresponding family name and blade number.

        Returns (best_value, best_family, best_blade) or (None, None, None).
        """
        best_val   = None
        best_fam   = None
        best_blade = None
        for fam_loop in family_order:
            for b in range(1, n_blades + 1):
                sensor = hub_sensor_map.get(comp, {}).get(f'B{b}')
                # if not sensor or sensor not in sensor_cols:
                #     continue

                if not sensor or ext_folder is None:
                    continue
                
                # Check if the .abs file exists physically in the EXT folder
                abs_file_path = os.path.join(ext_folder, f"{sensor}.abs")
                
                # Allow the code to proceed if the file exists OR if it's in the column list
                if not (os.path.exists(abs_file_path) or sensor in sensor_cols):
                    continue
                
                val, plf, fam_from_file = read_ext_design_load(
                    ext_folder, sensor, 'abs', with_plf=True, log=log)
                if val is None:
                    continue
                if best_val is None or abs(val) > abs(best_val):
                    best_val   = val
                    best_fam   = fam_from_file or fam_loop
                    best_blade = b
        return best_val, best_fam, best_blade

    def get_worst_del_across_blades(comp, m, family_order):
        """Find worst (highest) DEL across all blades and families."""
        best_val   = None
        best_blade = None
        for fam in family_order:
            for b in range(1, n_blades + 1):
                sensor = hub_sensor_map.get(comp, {}).get(f'B{b}')
                # if not sensor or sensor not in sensor_cols:
                #     continue
                if not sensor or fat_folder is None:
                    continue
                
                # Construct the expected filename: sensor.rfc
                rfc_file_path = os.path.join(fat_folder, f"{sensor}.rfc")
                
                # Check if file exists OR sensor is in sensor_cols
                if not (os.path.exists(rfc_file_path) or sensor in sensor_cols):
                    continue
                val = (read_fat_del_header(
                    fat_folder, sensor, m, method='rfc', log=log)
                    if fat_folder else None)
                if val is None:
                    continue
                if best_val is None or val > best_val:
                    best_val, best_blade = val, b
        return best_val, best_blade

    def fmt_val(val):
        if val is None:
            return f"{'N/A':>{w_val}}"
        return f"{val:>{w_val}.2f}"

    def fmt_val_blade(val, blade):
        if val is None:
            return f"{'N/A [--]':>{w_val}}"
        s = f"{val:.2f} [B{blade}]"
        return f"{s:>{w_val}}"

    def fmt_plf(plf):
        if plf is None:
            return f"{'[-]':>{w_plf}}"
        return f"{plf:>{w_plf}.2f}"

    def fmt_src(fam, blade):
        if fam is None:
            return f"{'N/A':>{w_src}}"
        return f"{fam} / B{blade}"

    # Get PLF from first family
    plf_val = None
    if family_order:
        from config import FILE_METADATA, FATIGUE_FILES
        for fname in FATIGUE_FILES:
            meta = FILE_METADATA.get(fname, {})
            plf_val = meta.get('PLF')
            if plf_val:
                break

    with open(output_path, 'w', encoding='utf-8') as f:
        # ── File header ───────────────────────────────────────────────────────
        f.write(f'# {"=" * 70}\n')
        f.write(f'# HubLoads.sum — Hub Load Summary\n')
        f.write(f'# {"=" * 70}\n')
        f.write(f'# Lifetime    : {lifetime_years:.0f} years\n')
        f.write(f'# Neq (RFC)   : {neq_lifetime:.3g}\n')
        f.write(f'# Blades      : {n_blades}\n')
        f.write(f'# Frame       : Hub coordinate system\n')
        f.write(f'#               Rotates with rotor (azimuth)\n')
        f.write(f'#               Does NOT rotate with blade pitch\n')
        f.write(f'#               x-axis = radially outward along blade span\n')
        f.write(f'#               y-axis = perpendicular to blade, in rotor plane\n')
        f.write(f'#               z-axis = perpendicular to rotor plane (along rotor axis)\n')
        f.write(f'# Sensors     : RootMxb/Myb/Mzb/Fxb/Fyb/Fzb — blades 1, 2, 3\n')
        f.write(f'# Note        : Worst case across all {n_blades} blades shown\n')
        f.write(f'#               AbsMax = worst of |Max| and |Min|, sign preserved\n')
        f.write(f'# {"=" * 70}\n\n')

        # ── Section 1: Extreme ────────────────────────────────────────────────
        f.write(f'# {"═" * 70}\n')
        f.write(f'# SECTION 1 — EXTREME LOADS (AbsMax with PLF)\n')
        f.write(f'# {"═" * 70}\n#\n')
        hdr = (f"#  {'Sensor':<{w_sensor}}"
               f"{'Value_PLF':>{w_val}}"
               f"{'PLF':>{w_plf}}"
               f"  {'Driving_DLC / Blade':<{w_src}}")
        f.write(hdr + '\n')
        f.write(f'# {subdiv}\n')

        for comp in components:
            unit = comp_units[comp]
            label = f"{comp}_[{unit}]"
            val, fam, blade = get_absmax_across_blades(comp, family_order)
            f.write(f"   {label:<{w_sensor}}"
                    f"{fmt_val(val)}"
                    f"{fmt_plf(plf_val)}"
                    f"  {fmt_src(fam, blade)}\n")

        f.write(f'# {subdiv}\n\n\n')

        # ── Section 2: Fatigue ────────────────────────────────────────────────
        f.write(f'# {"═" * 70}\n')
        f.write(f'# SECTION 2 — FATIGUE LOADS (Lifetime DEL, RFC method)\n')
        f.write(f'# {"═" * 70}\n#\n')

        # Build header with one column per m value
        w_del = 22
        del_hdr = f"#  {'Sensor':<{w_sensor}}"
        for m in m_values_hub:
            del_hdr += f"{'DEL_m'+str(m)+' [kN/kN-m]':>{w_del}}"
        f.write(del_hdr + '\n')
        del_div = '-' * (w_sensor + len(m_values_hub) * w_del + 3)
        f.write(f'# {del_div}\n')

        for comp in components:
            unit = comp_units[comp]
            label = f"{comp}_[{unit}]"
            f.write(f"   {label:<{w_sensor}}")
            for m in m_values_hub:
                val, blade = get_worst_del_across_blades(comp, m, family_order)
                # f.write(fmt_val_blade(val, blade).replace(' ' * (w_val - w_del), ''))
                # reformat to w_del width
                if val is None:
                    s = f"{'N/A [--]':>{w_del}}"
                else:
                    s = f"{val:.2f} [B{blade}]"
                    s = f"{s:>{w_del}}"
                f.write(s)
            f.write('\n')

        f.write(f'# {del_div}\n\n')
        f.write(f'# {"=" * 70}\n')
        f.write('# END OF FILE\n')
        f.write(f'# {"=" * 70}\n')


# =============================================================================
# TwrLoads.sum writer
# =============================================================================

def write_twr_loads_sum(output_path, twr_config,
                        family_order, sensor_cols,
                        lifetime_years, neq_lifetime,
                        ext_folder=None, fat_folder=None, log=None):
    """
    Write TwrLoads.sum — tower load summary file.

    Section 1: AbsMax extreme loads with PLF
                11 rows (Top→Base) + 11 DLC source rows
    Section 2: Lifetime RFC DEL for each m value in del_slopes

    Parameters
    ----------
    twr_config         : dict from get_tower_config()
    ext_folder         : str — path to EXT/ folder
    family_order       : list of str
    sensor_cols        : list of str — all available sensor columns
    lifetime_years     : float
    neq_lifetime       : float
    """
    stations   = twr_config['stations']
    del_slopes = twr_config['del_slopes']
    components = ['Mres', 'Mx', 'My', 'Mz', 'Fx', 'Fy', 'Fz']
    comp_units = {
        'Mres': 'kN-m', 'Mx': 'kN-m', 'My': 'kN-m', 'Mz': 'kN-m',
        'Fx': 'kN', 'Fy': 'kN', 'Fz': 'kN'
    }

    # Column widths
    w_station = 18
    w_val     = 16
    w_src     = 16
    n_comp    = len(components)
    div       = '=' * (w_station + n_comp * w_val)
    subdiv    = '-' * (w_station + n_comp * w_val)
    dotdiv    = '.' * (w_station + n_comp * w_val)

    def get_absmax(station, comp, family_order):
        """AbsMax with PLF across all families for this station/component."""
        sensor = station.get(comp)
        if not sensor or sensor not in sensor_cols:
            return None, None
        if ext_folder is None:
            return None, None
        # Tower sensors are not blade-dependent, so a single read of the
        # EXT .abs file gives the design driving family directly.
        val, plf, fam = read_ext_design_load(
            ext_folder, sensor, 'abs', with_plf=True, log=log)
        if val is None:
            return None, None
        return val, fam

    def get_del(station, comp, m, family_order):
        """Worst lifetime DEL across all families."""
        sensor = station.get(comp)
        if not sensor or sensor not in sensor_cols:
            return None
        if fat_folder is None:
            return None
        return read_fat_del_header(fat_folder, sensor, m, method='rfc', log=log)

    def fmt_val(val):
        if val is None:
            return f"{'N/A':>{w_val}}"
        return f"{val:>{w_val}.2f}"

    def fmt_src(fam):
        if fam is None:
            return f"{'N/A':>{w_src}}"
        return f"{fam:>{w_src}}"

    def col_header():
        hdr = f"#  {'Station':<{w_station}}"
        for comp in components:
            unit = comp_units[comp]
            hdr += f"{comp+'_['+unit+']':>{w_val}}"
        return hdr

    with open(output_path, 'w', encoding='utf-8') as f:
        # ── File header ───────────────────────────────────────────────────────
        f.write(f'# {"=" * 70}\n')
        f.write(f'# TwrLoads.sum — Tower Load Summary\n')
        f.write(f'# {"=" * 70}\n')
        f.write(f'# Lifetime    : {lifetime_years:.0f} years\n')
        f.write(f'# Neq (RFC)   : {neq_lifetime:.3g}\n')
        f.write(f'# Frame       : Tower fixed coordinate system (t suffix)\n')
        f.write(f'#               Mx = side-side bending\n')
        f.write(f'#               My = fore-aft bending\n')
        f.write(f'#               Mz = torsion\n')
        f.write(f'#               Mres = sqrt(Mx^2 + My^2)\n')
        twr_ht  = twr_config.get('tower_ht')
        base_ht = twr_config.get('base_ht')
        if twr_ht is not None:
            f.write(f'# Tower height : {twr_ht:.1f} m '
                    f'(base = {base_ht:.1f} m)\n')
        else:
            f.write('# Note         : Heights not available — '
                    'set TWR_ED_FILE in sensorList.txt\n')
        f.write(f'# Stations     : 11 (Tower Top to Tower Base, top-to-bottom)\n')
        f.write(f'# Note        : N/A = sensor not found in OpenFAST output\n')
        f.write(f'#               Loads increase from top to base (cantilever)\n')
        f.write(f'# {"=" * 70}\n\n')

        # ── Section 1: AbsMax extreme ─────────────────────────────────────────
        f.write(f'# {"═" * 70}\n')
        f.write(f'# SECTION 1 — ABSOLUTE MAXIMUM EXTREME LOADS (with PLF)\n')
        f.write(f'# {"═" * 70}\n#\n')
        f.write(col_header() + '\n')
        f.write(f'# {div}\n')

        # Collect values and sources
        val_rows = []
        src_rows = []
        for station in stations:
            lbl = station['label']
            vals = []
            srcs = []
            for comp in components:
                val, fam = get_absmax(station, comp, family_order)
                vals.append(fmt_val(val))
                srcs.append(fmt_src(fam))
            row_lbl = format_station_label_twr(lbl, station.get('height_m'))
            val_rows.append((row_lbl, vals))
            src_rows.append((row_lbl, srcs))

        # Write value rows
        for lbl, vals in val_rows:
            f.write(f"   {lbl:<{w_station}}")
            for v in vals:
                f.write(v)
            f.write('\n')

        f.write(f'# {dotdiv}\n')

        # Write DLC source rows
        for lbl, srcs in src_rows:
            f.write(f"   {lbl:<{w_station}}")
            for s in srcs:
                f.write(s)
            f.write('\n')

        f.write(f'# {div}\n\n\n')

        # ── Section 2: Lifetime RFC DEL ───────────────────────────────────────
        for sec_idx, m in enumerate(del_slopes, start=2):
            f.write(f'# {"═" * 70}\n')
            f.write(f'# SECTION {sec_idx} — LIFETIME DEL  m = {m}'
                    f'  (Neq = {neq_lifetime:.3g}, RFC method)\n')
            f.write(f'# {"═" * 70}\n#\n')
            f.write(col_header() + '\n')
            f.write(f'# {div}\n')

            for station in stations:
                lbl = station['label']
                row_lbl = format_station_label_twr(
                    lbl, station.get('height_m'))
                f.write(f"   {row_lbl:<{w_station}}")
                for comp in components:
                    val = get_del(station, comp, m, family_order)
                    f.write(fmt_val(val))
                f.write('\n')

            f.write(f'# {div}\n\n\n')

        f.write(f'# {"=" * 70}\n')
        f.write('# END OF FILE\n')
        f.write(f'# {"=" * 70}\n')




# =============================================================================
# YawLoads.sum writer
# =============================================================================

def read_ext_design_load(ext_folder, sensor_name, ext_type='abs',
                          with_plf=True, log=None):
    """
    Read the design load from an EXT file header.

    Parses the header lines written by write_ext_file():
      # Design Extreme (with PLF)    : value  unit  PLF [x.xx]  family
      # Design Extreme (without PLF) : value  unit  PLF [-]     family

    Parameters
    ----------
    ext_folder  : str  — path to EXT/ folder
    sensor_name : str  — exact sensor name
    ext_type    : str  — 'abs', 'max', or 'min'
    with_plf    : bool — True = with PLF row, False = without PLF row
    log         : PostProcessLogger or None

    Returns (value, plf, family) or (None, None, None)
    """
    cache_key = (ext_folder, sensor_name, ext_type, with_plf)
    if cache_key in _EXT_LOAD_CACHE:
        return _EXT_LOAD_CACHE[cache_key]
    
    safe  = sanitize_for_filename(sensor_name)
    fpath = os.path.join(ext_folder, safe + f'.{ext_type}')

    if not os.path.isfile(fpath):
        if log:
            log.warning(f'{safe}.{ext_type} not found — N/A in .sum', tag='SUM')
        _EXT_LOAD_CACHE[cache_key] = (None, None, None)  # Cache the empty result
        return None, None, None

    marker = 'with PLF' if with_plf else 'without PLF'
    try:
        with open(fpath, 'r', encoding='utf-8') as fh:
            for line in fh:
                if 'Design Extreme' not in line:
                    continue
                if marker.lower() not in line.lower():
                    continue
                # Format: # Design Extreme (with PLF)    : value  unit  PLF [x.xx]  family
                # Split on ':' to get the value part
                parts = line.split(':', 1)
                if len(parts) < 2:
                    continue
                tokens = parts[1].split()
                if not tokens:
                    continue
                try:
                    val = float(tokens[0])
                except ValueError:
                    continue
                # Extract PLF: token like 'PLF' followed by '[x.xx]' or '[-]'
                plf = 1.0
                fam = None
                for i, tok in enumerate(tokens):
                    if tok == 'PLF' and i + 1 < len(tokens):
                        plf_str = tokens[i + 1].strip('[]')
                        try:
                            plf = float(plf_str)
                        except ValueError:
                            plf = 1.0  # '-' means no PLF
                    # Family name is the last token
                    fam = tokens[-1]
                    
                _EXT_LOAD_CACHE[cache_key] = (val, plf, fam)
                return val, plf, fam
    except Exception as e:
        if log:
            log.warning(f'Error reading {os.path.basename(fpath)}: {e}', tag='SUM')
    _EXT_LOAD_CACHE[cache_key] = (None, None, None)
    return None, None, None


def write_yaw_loads_sum(output_path, yaw_config,
                        family_order, sensor_cols,
                        lifetime_years, neq_lifetime,
                        ext_folder=None, fat_folder=None, log=None):
    """
    Write YawLoads.sum — yaw system load summary.

    Section 1: AbsMax extreme with PLF (14 rows — includes Mz* without PLF)
    Section 2: Lifetime RFC DEL for all 13 sensors (loads + accelerations)

    DEL values read from FAT/<sensor>.rfc header — single source of truth.

    Parameters
    ----------
    yaw_config         : dict from get_yaw_config()
    ext_folder         : str — path to EXT/ folder (for Mz* without PLF)
    ext_folder         : str — path to EXT/ folder
    family_order       : list of str
    sensor_cols        : list of str
    lifetime_years     : float
    neq_lifetime       : float
    fat_folder         : str — path to FAT/ folder (reads DEL from .rfc headers)
    log                : PostProcessLogger or None
    """
    del_slopes  = yaw_config['del_slopes']
    loads       = yaw_config['loads']
    accel_trans = yaw_config['accel_trans']
    accel_ang   = yaw_config['accel_ang']
    mz_sensor   = yaw_config.get('mz_sensor')

    w_label = 22
    w_val   = 16
    w_plf   = 8
    w_dlc   = 20
    subdiv  = '-' * (w_label + w_val + w_plf + w_dlc)
    dotdiv  = '.' * (w_label + w_val)

    def get_absmax_plf(sensor):
        """AbsMax with PLF — reads design driving family from EXT .abs header."""
        if not sensor or sensor not in sensor_cols:
            return None, None, None
        if ext_folder is None:
            return None, None, None
        val, plf, fam = read_ext_design_load(
            ext_folder, sensor, 'abs', with_plf=True, log=log)
        if val is None:
            return None, None, None
        return val, plf, fam

    def get_del(sensor, m):
        """Read DEL from FAT file."""
        if not sensor or sensor not in sensor_cols or fat_folder is None:
            return None
        return read_fat_del_header(fat_folder, sensor, m, method='rfc', log=log)

    def fmt_val(v):
        return f"{'N/A':>{w_val}}" if v is None else f"{v:>{w_val}.2f}"

    def fmt_plf(p):
        return f"{'—':>{w_plf}}" if p is None else f"{p:>{w_plf}.2f}"

    def fmt_dlc(d):
        return f"{'N/A':>{w_dlc}}" if d is None else f"{d:>{w_dlc}}"

    def write_row(f, label, val, plf, dlc):
        f.write(f"   {label:<{w_label}}{fmt_val(val)}{fmt_plf(plf)}{fmt_dlc(dlc)}\n")

    def write_del_row(f, label, val):
        f.write(f"   {label:<{w_label}}{fmt_val(val)}\n")

    with open(output_path, 'w', encoding='utf-8') as f:
        # ── Header ───────────────────────────────────────────────────────────
        f.write(f'# {"=" * 70}\n')
        f.write(f'# YawLoads.sum — Yaw System Load Summary\n')
        f.write(f'# {"=" * 70}\n')
        f.write(f'# Lifetime     : {lifetime_years:.0f} years\n')
        f.write(f'# Neq (RFC)    : {neq_lifetime:.3g}\n')
        f.write(f'# Frame        : Nacelle coordinate system (p suffix)\n')
        f.write(f'#                x = fore-aft, y = side-side, z = vertical\n')
        f.write(f'# Mx           : Rolling moment\n')
        f.write(f'# My           : Pitching moment\n')
        f.write(f'# Mz           : Yaw drive torque (× PLF — structural design)\n')
        f.write(f'# Mz*          : Yaw drive torque (× 1.00 — motor sizing)\n')
        f.write(f'# Source       : DEL values read from FAT folder .rfc headers\n')
        f.write(f'# Note         : N/A = sensor not found or FAT file missing\n')
        f.write(f'# {"=" * 70}\n\n')

        # ── Section 1: AbsMax extreme ─────────────────────────────────────────
        f.write(f'# {"═" * 70}\n')
        f.write(f'# SECTION 1 — ABSOLUTE MAXIMUM EXTREME LOADS (with PLF)\n')
        f.write(f'# {"═" * 70}\n#\n')
        f.write(f"#  {'Component':<{w_label}}{'Value_PLF':>{w_val}}{'PLF':>{w_plf}}{'Driving_DLC':>{w_dlc}}\n")
        f.write(f'# {subdiv}\n')

        # Moment loads (Mres, Mx, My, Mz with Mz* after Mz)
        moment_map = [
            ('Mres_[kN-m]', loads.get('Mres')),
            ('Mx_[kN-m]',   loads.get('Mx')),
            ('My_[kN-m]',   loads.get('My')),
            ('Mz_[kN-m]',   loads.get('Mz')),
        ]
        for label, sensor in moment_map:
            val, plf, dlc = get_absmax_plf(sensor)
            write_row(f, label, val, plf, dlc)
            if sensor == mz_sensor:
                # Mz* — without PLF from EXT file
                val_np, _plf_np, dlc_np = read_ext_design_load(
                    ext_folder, sensor, 'abs', with_plf=False, log=log) \
                    if (sensor and ext_folder) else (None, None, None)
                write_row(f, 'Mz*_[kN-m]', val_np, 1.00, dlc_np)

        # Force loads
        for label, sensor in [
            ('Fx_[kN]', loads.get('Fx')),
            ('Fy_[kN]', loads.get('Fy')),
            ('Fz_[kN]', loads.get('Fz')),
        ]:
            val, plf, dlc = get_absmax_plf(sensor)
            write_row(f, label, val, plf, dlc)

        f.write(f'# {dotdiv}\n')

        # Translational accelerations
        for label, sensor in [
            ('TAxp_[m/s^2]', accel_trans.get('TAxp')),
            ('TAyp_[m/s^2]', accel_trans.get('TAyp')),
            ('TAzp_[m/s^2]', accel_trans.get('TAzp')),
        ]:
            val, plf, dlc = get_absmax_plf(sensor)
            write_row(f, label, val, plf, dlc)

        f.write(f'# {dotdiv}\n')

        # Angular accelerations
        for label, sensor in [
            ('RAxp_[deg/s^2]', accel_ang.get('RAxp')),
            ('RAyp_[deg/s^2]', accel_ang.get('RAyp')),
            ('RAzp_[deg/s^2]', accel_ang.get('RAzp')),
        ]:
            val, plf, dlc = get_absmax_plf(sensor)
            write_row(f, label, val, plf, dlc)

        f.write(f'# {subdiv}\n\n\n')

        # ── Section 2: Lifetime RFC DEL (from FAT files) ──────────────────────
        all_del_rows = [
            ('Mres_[kN-m]',   loads.get('Mres')),
            ('Mx_[kN-m]',     loads.get('Mx')),
            ('My_[kN-m]',     loads.get('My')),
            ('Mz_[kN-m]',     loads.get('Mz')),
            ('Fx_[kN]',       loads.get('Fx')),
            ('Fy_[kN]',       loads.get('Fy')),
            ('Fz_[kN]',       loads.get('Fz')),
            ('TAxp_[m/s^2]',  accel_trans.get('TAxp')),
            ('TAyp_[m/s^2]',  accel_trans.get('TAyp')),
            ('TAzp_[m/s^2]',  accel_trans.get('TAzp')),
            ('RAxp_[deg/s^2]', accel_ang.get('RAxp')),
            ('RAyp_[deg/s^2]', accel_ang.get('RAyp')),
            ('RAzp_[deg/s^2]', accel_ang.get('RAzp')),
        ]

        for sec_idx, m in enumerate(del_slopes, start=2):
            f.write(f'# {"═" * 70}\n')
            f.write(f'# SECTION {sec_idx} — LIFETIME DEL  m = {m}'
                    f'  (Neq = {neq_lifetime:.3g}, RFC method)\n')
            f.write(f'# Source: FAT/<sensor>.rfc header\n')
            f.write(f'# {"═" * 70}\n#\n')
            f.write(f"#  {'Component':<{w_label}}{'DEL':>{w_val}}\n")
            f.write(f'# {"-" * (w_label + w_val)}\n')

            for i, (label, sensor) in enumerate(all_del_rows):
                val = get_del(sensor, m)
                write_del_row(f, label, val)
                # Dotted separator between loads and accelerations
                if i == 6:
                    f.write(f'# {dotdiv}\n')
                if i == 9:
                    f.write(f'# {dotdiv}\n')

            f.write(f'# {"-" * (w_label + w_val)}\n\n\n')

        f.write(f'# {"=" * 70}\n')
        f.write('# END OF FILE\n')
        f.write(f'# {"=" * 70}\n')

    if log:
        log.file_written('SUM/YawLoads.sum', tag='SUM')


# =============================================================================
# Complementary file Design Driving reader
# =============================================================================

def _short_comp_and_unit(label):
    """
    Split a column label like '*Mres_[kn-m]' or 'Mx_[kN-m]' or 'Fz_[kN]'
    into (short_component, unit_string, is_driving).

    Returns ('Mres', 'kn-m', True) for '*Mres_[kn-m]'
            ('Mx',   'kN-m', False) for 'Mx_[kN-m]'
            ('Fz',   'kN',   False) for 'Fz_[kN]'
            (label,  None,   False) if not parseable.
    """
    is_driving = label.startswith('*')
    s = label.lstrip('*')
    m = re.match(r'^([A-Za-z]+)_\[(.+)\]$', s)
    if m:
        return m.group(1), m.group(2), is_driving
    return s, None, is_driving


def _normalise_unit(unit):
    """
    Display normalisation:  'kn-m' -> 'kN-m', 'KN-M' -> 'kN-m', 'kn' -> 'kN'.
    Leaves m/s^2, deg, etc. untouched.
    """
    if unit is None:
        return None
    u = unit.strip()
    if u.lower() == 'kn-m':
        return 'kN-m'
    if u.lower() == 'kn':
        return 'kN'
    return u


def read_comp_design_driving(ext_folder, sensor_name, with_plf=True, log=None):
    """
    Read the Design Driving row from a _comp.abs file header.

    Parses:
      # With PLF     val1   val2   ...   PLF   Family   File   Time_[s]
      # Without PLF  val1   val2   ...   PLF   Family   File   Time_[s]

    Column labels are read from the column header line above the data rows:
      #  Rank   Mres_[kn-m]   *Mx_[kN-m]   ...   PLF   Family   File   Time_[s]

    Returns dict:
    {
      'values'   : OrderedDict {short_comp: float}     # 'Mres','Mx','My',...
      'units'    : OrderedDict {short_comp: unit_str}  # 'kn-m','kN-m',...
      'driving'  : str or None  # short comp tagged with '*' in this file
      'plf'      : float
      'family'   : str
      'filename' : str
      'time_s'   : float
    }
    or None if file not found or unreadable.
    """
    from collections import OrderedDict
    safe  = sanitize_for_filename(sensor_name)
    fpath = os.path.join(ext_folder, safe + '_comp.abs')
    if not os.path.isfile(fpath):
        if log:
            log.warning(f'{safe}_comp.abs not found — N/A in complementary table',
                        tag='SUM')
        return None

    marker = '# With PLF' if with_plf else '# Without PLF'
    try:
        with open(fpath, 'r', encoding='utf-8') as fh:
            lines = fh.readlines()

        # Find column header line — starts with '#  Rank'
        col_labels = []
        for line in lines:
            s = line.strip()
            if s.startswith('#') and 'Rank' in s and ('_[' in s or 'PLF' in s):
                parts = s.lstrip('#').split()
                col_labels = parts[1:]  # Mres_[..], *Mx_[..], ..., PLF, Family, File, Time_[s]
                break

        if not col_labels:
            return None

        # Find the With PLF / Without PLF data row
        for line in lines:
            if not line.startswith(marker):
                continue
            parts = line[len(marker):].split()
            if not parts:
                continue
            # The data row begins with the integer Rank (e.g. '1') which is
            # NOT in col_labels (we stripped 'Rank' from the header). Drop it
            # so positional alignment with col_labels is correct.
            if parts and parts[0].isdigit():
                parts = parts[1:]
            values    = OrderedDict()
            units     = OrderedDict()
            driving   = None
            plf_val   = None
            family    = None
            filename  = None
            time_s    = None

            for i, lbl in enumerate(col_labels):
                if i >= len(parts):
                    break
                v = parts[i]
                if lbl == 'PLF':
                    try:
                        plf_val = float(v)
                    except ValueError:
                        plf_val = 1.0
                elif lbl == 'Family':
                    family = v
                elif lbl == 'File':
                    filename = v
                elif lbl in ('Time_[s]', 'Time'):
                    try:
                        time_s = float(v)
                    except ValueError:
                        time_s = None
                else:
                    short, unit, is_drv = _short_comp_and_unit(lbl)
                    try:
                        fval = float(v)
                    except ValueError:
                        fval = None
                    values[short] = fval
                    units[short]  = unit
                    if is_drv and driving is None:
                        driving = short

            return {
                'values'  : values,
                'units'   : units,
                'driving' : driving,
                'plf'     : plf_val if plf_val is not None else 1.0,
                'family'  : family or '',
                'filename': filename or '',
                'time_s'  : time_s,
            }
    except Exception as e:
        if log:
            log.warning(f'Error reading {os.path.basename(fpath)}: {e}',
                        tag='SUM')
    return None


# =============================================================================
# DRTLoads.sum writer
# =============================================================================

def write_drt_loads_sum(output_path, drt_config,
                        family_order, sensor_cols,
                        lifetime_years, neq_lifetime,
                        ext_folder=None, fat_folder=None, log=None):
    """
    Write DRTLoads.sum — drivetrain load summary.

    Table 1A : Design extreme AbsMax with PLF  (both frames)
    Table 1B : Lifetime RFC DEL               (both frames, DRT_DEL_SLOPES)
    Table 1C : Lifetime LDD/LRD DEL           (DRT_LDD_LRD_SENSORS)
    Table 2  : Complementary loads with PLF   (both frames, from _comp.abs)

    Parameters
    ----------
    drt_config    : dict from get_drivetrain_config()
    family_order  : list of str
    sensor_cols   : list of str — all available sensor columns
    lifetime_years: float
    neq_lifetime  : float
    ext_folder    : str
    fat_folder    : str
    log           : PostProcessLogger or None
    """
    del_slopes      = drt_config['del_slopes']
    ldd_slopes      = drt_config['ldd_slopes']
    lrd_slopes      = drt_config['lrd_slopes']
    ldd_lrd_sensors = drt_config['ldd_lrd_sensors']
    a_frame         = drt_config['a_frame']
    s_frame         = drt_config['s_frame']
    hss             = drt_config.get('hss', {})
    hss_del_slopes  = drt_config.get('hss_del_slopes', [4.0, 8.0])

    FRAME_ORDER = ['Mres', 'Mx', 'My', 'Mz', 'Fx', 'Fy', 'Fz']
    FRAME_LABELS = {
        'a': '── Non-rotating frame (a suffix) ──',
        's': '── Rotating frame (s suffix) ──────',
    }

    w_comp = 26
    w_val  = 16
    w_plf  =  8
    w_dlc  = 20
    div    = '=' * 70
    subdiv = '─' * 70
    subdiv_len = w_comp + w_val + w_plf + w_dlc

    def fmt_val(v):
        return f"{'N/A':>{w_val}}" if v is None else f"{v:>{w_val}.4f}"

    def fmt_plf(p):
        return f"{'—':>{w_plf}}" if p is None else f"{p:>{w_plf}.2f}"

    def fmt_dlc(d):
        return f"{'N/A':>{w_dlc}}" if d is None else f"{d:>{w_dlc}}"

    def get_absmax(sensor):
        # """Read AbsMax with PLF from EXT .abs header."""
        # if not sensor or sensor not in sensor_cols or ext_folder is None:
        #     return None, None, None
        # return read_ext_design_load(ext_folder, sensor, 'abs',
        #                             with_plf=True, log=log)

        if not sensor or ext_folder is None:
            return None, None, None
    
        # 2. Check if the file exists on disk (Case-Sensitive check)
        # This bypasses the 'sensor_cols' requirement
        file_path = os.path.join(ext_folder, f"{sensor}.abs")
        
        if os.path.exists(file_path):
            return read_ext_design_load(ext_folder, sensor, 'abs',
                                        with_plf=True, log=log)
        
        # 3. Fallback: Only check sensor_cols if the file wasn't found directly
        if sensor not in sensor_cols:
            return None, None, None

        return read_ext_design_load(ext_folder, sensor, 'abs',
                                with_plf=True, log=log)

    def get_rfc_del(sensor, m):
        # """Read RFC DEL from FAT .rfc header."""
        # if not sensor or sensor not in sensor_cols or fat_folder is None:
        #     return None
        # return read_fat_del_header(fat_folder, sensor, m, method='rfc', log=log)

        if not sensor or fat_folder is None:
            return None
    
        # 1. Try to find the file first, regardless of sensor_cols
        # This ensures that even if Mres isn't in the 'out' file, 
        # we still read it if the .rfc file exists.
        filepath = os.path.join(fat_folder, f"{sensor}.rfc")
        
        if os.path.exists(filepath):
            return read_fat_del_header(fat_folder, sensor, m, method='rfc', log=log)
        
        # 2. Fallback check for sensor_cols if the direct file check fails
        if sensor not in sensor_cols:
            return None
            
        return read_fat_del_header(fat_folder, sensor, m, method='rfc', log=log)

    def get_ldd_del(sensor, m):
        """Read LDD DEL from FAT .ldd header."""
        if not sensor or sensor not in sensor_cols or fat_folder is None:
            return None
        return read_fat_del_header(fat_folder, sensor, m, method='ldd', log=log)

    def get_lrd_del(sensor, m):
        """Read LRD DEL from FAT .lrd header."""
        if not sensor or sensor not in sensor_cols or fat_folder is None:
            return None
        return read_fat_del_header(fat_folder, sensor, m, method='lrd', log=log)

    def write_frame_separator(f, frame_key):
        f.write(f'# {FRAME_LABELS[frame_key]}\n')

    def _sensor_frame(sensor_name):
        """Infer frame from sensor name: 'a' or 's' or None."""
        n = sensor_name.lower()
        if any(p in n for p in ('mxa_','mya_','mza_','fxa_','fya_','fza_',
                                 'mres_lsshft_a')):
            return 'a'
        if any(p in n for p in ('mxs_','mys_','mzs_','fxs_','fys_','fzs_',
                                 'mres_lsshft_s')):
            return 's'
        return None

    with open(output_path, 'w', encoding='utf-8') as f:
        # ── File header ───────────────────────────────────────────────────────
        f.write(f'# {"=" * 70}\n')
        f.write(f'# DRTLoads.sum — Drivetrain Load Summary\n')
        f.write(f'# {"=" * 70}\n')
        f.write(f'# Lifetime     : {lifetime_years:.0f} years\n')
        f.write(f'# Neq (RFC)    : {neq_lifetime:.3g}\n')
        f.write(f'# Frame a      : Non-rotating — fixed to nacelle\n')
        f.write(f'# Frame s      : Rotating — rotates with rotor\n')
        f.write(f'# Table 1A     : Design extreme AbsMax with PLF\n')
        f.write(f'# Table 1B     : Lifetime RFC DEL'
                f'  m = {[fmt_slope(m) for m in del_slopes]}\n')
        f.write(f'# Table 1C     : Lifetime LDD/LRD DEL'
                f'  LDD m = {[fmt_slope(m) for m in ldd_slopes]}'
                f'  LRD m = {[fmt_slope(m) for m in lrd_slopes]}\n')
        f.write(f'# Table 2      : Complementary loads (with PLF)\n')
        f.write(f'# Table 3      : High-speed shaft — extreme + RFC DEL\n')
        f.write(f'# Source EXT   : EXT/<sensor>.abs / _comp.abs headers\n')
        f.write(f'# Source FAT   : FAT/<sensor>.rfc / .ldd / .lrd headers\n')
        f.write(f'# Note         : N/A = sensor not found or file missing\n')
        f.write(f'# {"=" * 70}\n\n\n')

        # ── TABLE 1A ─────────────────────────────────────────────────────────
        f.write(f'# {"═" * 70}\n')
        f.write(f'# TABLE 1A — DESIGN EXTREME LOADS (with PLF)\n')
        f.write(f'# Source : EXT/<sensor>.abs — Design Extreme (with PLF) header\n')
        f.write(f'# {"═" * 70}\n#\n')
        f.write(f"#  {'Component':<{w_comp}}{'AbsMax_PLF':>{w_val}}"
                f"{'PLF':>{w_plf}}{'Driving_DLC':>{w_dlc}}\n")
        f.write(f'# {subdiv}\n')
        for frame_key, frame in [('a', a_frame), ('s', s_frame)]:
            write_frame_separator(f, frame_key)
            for lbl in FRAME_ORDER:
                sensor = frame.get(lbl)
                val, plf, dlc = get_absmax(sensor)
                f.write(f"   {(lbl+'_['+_get_unit(sensor)+']') if sensor else lbl:<{w_comp}}"
                        f"{fmt_val(val)}{fmt_plf(plf)}{fmt_dlc(dlc)}\n")
        f.write(f'# {subdiv}\n\n\n')

        # ── TABLE 1B ─────────────────────────────────────────────────────────
        del_hdrs = ''.join(f"{'DEL_m'+fmt_slope(m):>{w_val}}" for m in del_slopes)
        f.write(f'# {"═" * 70}\n')
        f.write(f'# TABLE 1B — LIFETIME RFC DEL\n')
        f.write(f'# Source : FAT/<sensor>.rfc — Lifetime DEL header\n')
        f.write(f'# Neq    : {neq_lifetime:.3g}    Method : RFC\n')
        f.write(f'# {"═" * 70}\n#\n')
        f.write(f"#  {'Component':<{w_comp}}{del_hdrs}\n")
        f.write(f'# {subdiv}\n')
        for frame_key, frame in [('a', a_frame), ('s', s_frame)]:
            write_frame_separator(f, frame_key)
            for lbl in FRAME_ORDER:
                sensor = frame.get(lbl)
                vals = ''.join(fmt_val(get_rfc_del(sensor, m)) for m in del_slopes)
                label = (lbl+'_['+_get_unit(sensor)+']') if sensor else lbl
                f.write(f"   {label:<{w_comp}}{vals}\n")
        f.write(f'# {subdiv}\n\n\n')

        # ── TABLE 1C ─────────────────────────────────────────────────────────
        ldd_hdrs = ''.join(f"{'LDD_m'+fmt_slope(m):>{w_val}}" for m in ldd_slopes)
        lrd_hdrs = ''.join(f"{'LRD_m'+fmt_slope(m):>{w_val}}" for m in lrd_slopes)
        f.write(f'# {"═" * 70}\n')
        f.write(f'# TABLE 1C — LIFETIME LDD / LRD DEL\n')
        f.write(f'# Source : FAT/<sensor>.ldd / .lrd — Lifetime DEL headers\n')
        f.write(f'# LDD Neq: lifetime seconds  LRD Neq: lifetime revolutions\n')
        f.write(f'# {"═" * 70}\n#\n')
        f.write(f"#  {'Component':<{w_comp}}{ldd_hdrs}{lrd_hdrs}\n")
        f.write(f'# {subdiv}\n')
        prev_frame = None
        for sensor in ldd_lrd_sensors:
            if sensor not in sensor_cols:
                if log:
                    log.warning(f'{sensor} not in output — N/A in Table 1C',
                                tag='SUM')
            frame = _sensor_frame(sensor)
            if frame and frame != prev_frame:
                write_frame_separator(f, frame)
                prev_frame = frame
            ldd_vals = ''.join(fmt_val(get_ldd_del(sensor, m))
                               for m in ldd_slopes)
            lrd_vals = ''.join(fmt_val(get_lrd_del(sensor, m))
                               for m in lrd_slopes)
            label = sensor if sensor else 'N/A'
            f.write(f"   {label:<{w_comp}}{ldd_vals}{lrd_vals}\n")
        f.write(f'# {subdiv}\n\n\n')

        # ── TABLE 2 — COMPLEMENTARY (per-frame) ──────────────────────────────
        # Layout per frame (a / s):
        #   - Columns Mres, Mx, My, Mz, Fx, Fy, Fz with units from the
        #     configured DRT_<comp>_<frame> sensor.
        #   - Diagonal cell = EXT/<driving>.abs (Table 1A) value, with PLF.
        #   - Off-diagonals = values of the column sensor at the instant
        #     row's driving sensor attained its design extreme.
        w_drv  = 12
        w_cval = 14
        w_fam  = 18
        w_file = 30
        w_time =  9
        w_cplf =  7

        # Build canonical column units per frame so each frame's row uses
        # the units from that frame's configured sensors.
        def _col_units_for_frame(frame_dict):
            cu = {}
            for lbl in FRAME_ORDER:
                s = frame_dict.get(lbl)
                cu[lbl] = _get_unit(s) if s else '-'
            return cu

        comp_total = (w_drv + len(FRAME_ORDER)*w_cval +
                      w_cplf + w_fam + w_file + w_time + 4)
        comp_subdiv = '─' * comp_total

        def comp_header(col_units):
            h = f"#  {'Driving':<{w_drv}}"
            for c in FRAME_ORDER:
                lbl = f"{c}_[{col_units[c]}]"
                h += f"  {lbl:>{w_cval}}"
            h += (f"  {'PLF':>{w_cplf}}"
                  f"  {'Family':<{w_fam}}"
                  f"  {'File':<{w_file}}"
                  f"  {'Time_[s]':>{w_time}}")
            return h

        def fmt_cell(v):
            return (f"  {v:>{w_cval}.4f}" if v is not None
                    else f"  {'N/A':>{w_cval}}")

        f.write(f'# {"═" * 70}\n')
        f.write(f'# TABLE 2 — COMPLEMENTARY LOADS (with PLF)\n')
        f.write(f'# Source : EXT/<driving>_comp.abs — Design Driving Loads (Rank 1)\n')
        f.write(f'# Diag.  : value from EXT/<driving>.abs (Table 1A) — with PLF\n')
        f.write(f'# Off-d. : value of column-sensor at the instant the row\'s\n')
        f.write(f'#          driving sensor attained its design extreme\n')
        f.write(f'# {"═" * 70}\n#\n')
        # Header is the SAME for both frames if units are uniform; we use
        # the a-frame's units since drivetrain is conventionally uniform.
        # (Each frame's configured sensors typically use the same unit.)
        f.write(comp_header(_col_units_for_frame(a_frame)) + '\n')
        f.write(f'# {comp_subdiv}\n')

        for frame_key, frame in [('a', a_frame), ('s', s_frame)]:
            write_frame_separator(f, frame_key)
            for drv_lbl in FRAME_ORDER:
                drv_sensor = frame.get(drv_lbl)
                drv_label  = f'{drv_lbl}_{frame_key}'
                row = f"  {drv_label:<{w_drv}}"
                dd  = (read_comp_design_driving(ext_folder, drv_sensor,
                                                with_plf=True, log=log)
                       if drv_sensor and ext_folder else None)
                diag_val = None
                diag_plf = None
                diag_dlc = None
                # if drv_sensor and drv_sensor in sensor_cols and ext_folder:
                #     diag_val, diag_plf, diag_dlc = read_ext_design_load(
                #         ext_folder, drv_sensor, 'abs',
                #         with_plf=True, log=log)

                if drv_sensor and ext_folder:
                    # Construct the path to the .abs file to see if it exists
                    abs_file_path = os.path.join(ext_folder, f"{drv_sensor}.abs")
                    
                    # If the file exists OR it's in sensor_cols, try to read it
                    if os.path.exists(abs_file_path) or drv_sensor in sensor_cols:
                        diag_val, diag_plf, diag_dlc = read_ext_design_load(
                            ext_folder, drv_sensor, 'abs',
                            with_plf=True, log=log)

                for col_lbl in FRAME_ORDER:
                    if col_lbl == drv_lbl:
                        row += fmt_cell(diag_val)
                    else:
                        v = dd['values'].get(col_lbl) if dd else None
                        row += fmt_cell(v)

                if dd is not None:
                    ts = dd.get('time_s')
                    row += (f"  {dd['plf']:>{w_cplf}.2f}"
                            f"  {dd['family']:<{w_fam}}"
                            f"  {dd['filename']:<{w_file}}"
                            + (f"  {ts:>{w_time}.2f}" if ts is not None
                               else f"  {'N/A':>{w_time}}"))
                elif diag_plf is not None:
                    row += (f"  {diag_plf:>{w_cplf}.2f}"
                            f"  {(diag_dlc or 'N/A'):<{w_fam}}"
                            f"  {'N/A':<{w_file}}"
                            f"  {'N/A':>{w_time}}")
                else:
                    row += (f"  {'—':>{w_cplf}}"
                            f"  {'N/A':<{w_fam}}"
                            f"  {'N/A':<{w_file}}"
                            f"  {'N/A':>{w_time}}")
                f.write(row + '\n')
        f.write(f'# {comp_subdiv}\n\n')
        # ── TABLE 3 — High-speed shaft ───────────────────────────────────
        hss_Tq  = hss.get('Tq')
        hss_Pwr = hss.get('Pwr')
        hss_V   = hss.get('V')
        hss_del_hdrs = ''.join(f"{'DEL_m'+fmt_slope(m):>{w_val}}"
                               for m in hss_del_slopes)
        f.write(f'# {"═" * 70}\n')
        f.write(f'# TABLE 3 — HIGH-SPEED SHAFT\n')
        f.write(f'# Source : EXT/<sensor>.abs and FAT/<sensor>.rfc headers\n')
        f.write(f'# {"═" * 70}\n#\n')

        # Extreme with PLF — torque only
        f.write(f'# ── Extreme Loads (with PLF) ─────────────────────────────────────────\n')
        f.write(f"#  {'Component':<{w_comp}}{'Value_PLF':>{w_val}}{'PLF':>{w_plf}}{'Driving_DLC':>{w_dlc}}\n")
        f.write(f'# {"-" * subdiv_len}\n')
        if hss_Tq and hss_Tq in sensor_cols and ext_folder:
            val, plf, dlc = read_ext_design_load(
                ext_folder, hss_Tq, 'abs', with_plf=True, log=log)
            f.write(f"   {hss_Tq:<{w_comp}}{fmt_val(val)}{fmt_plf(plf)}{fmt_dlc(dlc)}\n")
        else:
            f.write(f"   {'HSShftTq_[kN-m]':<{w_comp}}{fmt_val(None)}{fmt_plf(None)}{fmt_dlc(None)}\n")
        f.write(f'# {"-" * subdiv_len}\n\n')

        # Extreme without PLF — torque, power, speed
        f.write(f'# ── Extreme Operating (without PLF) ─────────────────────────────────\n')
        f.write(f"#  {'Component':<{w_comp}}{'Value':>{w_val}}{'Driving_DLC':>{w_dlc}}\n")
        f.write(f'# {"-" * (w_comp + w_val + w_dlc)}\n')
        for label, sensor, ext_type in [
            (hss_Tq  or 'HSShftTq_[kN-m]',  hss_Tq,  'abs'),
            (hss_Pwr or 'HSShftPwr_[kW]',    hss_Pwr, 'abs'),
            (hss_V   or 'HSShftV_[rpm]',     hss_V,   'max'),
        ]:
            if sensor and sensor in sensor_cols and ext_folder:
                val, _plf, dlc = read_ext_design_load(
                    ext_folder, sensor, ext_type, with_plf=False, log=log)
            else:
                val = dlc = None
            f.write(f"   {label:<{w_comp}}{fmt_val(val)}{fmt_dlc(dlc)}\n")
        f.write(f'# {"-" * (w_comp + w_val + w_dlc)}\n\n')

        # RFC DEL — torque only
        f.write(f'# ── Lifetime RFC DEL ────────────────────────────────────────────────\n')
        f.write(f"#  {'Component':<{w_comp}}{hss_del_hdrs}\n")
        f.write(f'# {"-" * (w_comp + len(hss_del_slopes)*w_val)}\n')
        lbl = hss_Tq or 'HSShftTq_[kN-m]'
        vals = ''.join(
            fmt_val(read_fat_del_header(fat_folder, hss_Tq, m, 'rfc', log)
                    if hss_Tq and hss_Tq in sensor_cols and fat_folder
                    else None)
            for m in hss_del_slopes)
        f.write(f"   {lbl:<{w_comp}}{vals}\n")
        f.write(f'# {"-" * (w_comp + len(hss_del_slopes)*w_val)}\n\n')

        f.write(f'# {"=" * 70}\n')
        f.write('# END OF FILE\n')
        f.write(f'# {"=" * 70}\n')

    if log:
        log.file_written('SUM/DRTLoads.sum', tag='SUM')


def _get_unit(sensor_name):
    """
    Extract unit string from sensor name e.g.:
      'LSShftMxa_[kN-m]' -> 'kN-m'
      'mres_twht9_[kn-m]' -> 'kN-m'  (case-normalised for display)
      'RootMzc1_[kN-m]'   -> 'kN-m'
    """
    if not sensor_name:
        return '-'
    m = re.search(r'\[(.+?)\]', sensor_name)
    if not m:
        return '-'
    return _normalise_unit(m.group(1))


# =============================================================================
# FNDLoads.sum writer
# =============================================================================

def write_fnd_loads_sum(output_path, fnd_config,
                        family_order, sensor_cols,
                        lifetime_years, neq_lifetime,
                        ext_folder=None, fat_folder=None, log=None):
    """
    Write FNDLoads.sum — foundation load summary.

    Table 1A : Design extreme AbsMax with PLF    (EXT .abs header)
    Table 1B : Design characteristic no PLF      (EXT .abs header)
    Table 1C : Complementary loads with PLF      (_comp.abs Design Driving)
    Table 2A : Lifetime RFC DEL                  (FAT .rfc header)
    Table 2B : Lifetime LDD DEL                  (FAT .ldd header)

    Parameters
    ----------
    fnd_config    : dict from get_foundation_config()
    family_order  : list of str
    sensor_cols   : list of str
    lifetime_years: float
    neq_lifetime  : float
    ext_folder    : str
    fat_folder    : str
    log           : PostProcessLogger or None
    """
    reference   = fnd_config['reference']
    sensors     = fnd_config['sensors']
    rfc_slopes  = fnd_config['rfc_slopes']
    ldd_slopes  = fnd_config['ldd_slopes']
    ldd_sensors = fnd_config['ldd_sensors']

    COMP_ORDER  = ['Mres', 'Mx', 'My', 'Mz', 'Fx', 'Fy', 'Fz']

    w_comp = 24
    w_val  = 16
    w_plf  =  8
    w_dlc  = 20
    div    = '═' * 70
    subdiv = '─' * (w_comp + w_val + w_plf + w_dlc)

    def fmt_val(v):
        return f"{'N/A':>{w_val}}" if v is None else f"{v:>{w_val}.4f}"

    def fmt_plf(p):
        return f"{'—':>{w_plf}}" if p is None else f"{p:>{w_plf}.2f}"

    def fmt_dlc(d):
        return f"{'N/A':>{w_dlc}}" if d is None else f"{d:>{w_dlc}}"

    def write_ext_row(f, label, sensor, with_plf=True):
        if not sensor or sensor not in sensor_cols or ext_folder is None:
            f.write(f"   {label:<{w_comp}}"
                    f"{fmt_val(None)}{fmt_plf(None)}{fmt_dlc(None)}\n")
            return
        val, plf, dlc = read_ext_design_load(
            ext_folder, sensor, 'abs', with_plf=with_plf, log=log)
        f.write(f"   {label:<{w_comp}}{fmt_val(val)}{fmt_plf(plf)}{fmt_dlc(dlc)}\n")

    def write_del_row(f, label, sensor, m, method='rfc'):
        if not sensor or sensor not in sensor_cols or fat_folder is None:
            f.write(f"   {label:<{w_comp}}{fmt_val(None)}\n")
            return
        val = read_fat_del_header(fat_folder, sensor, m,
                                  method=method, log=log)
        f.write(f"   {label:<{w_comp}}{fmt_val(val)}\n")

    def label_for(comp, sensor):
        """Build display label like 'Mres_[kN-m]' from comp name and sensor."""
        unit = _get_unit(sensor) if sensor else '-'
        return f"{comp}_[{unit}]"

    with open(output_path, 'w', encoding='utf-8') as f:
        # ── File header ───────────────────────────────────────────────────────
        f.write(f'# {"=" * 70}\n')
        f.write(f'# FNDLoads.sum — Foundation Load Summary\n')
        f.write(f'# {"=" * 70}\n')
        f.write(f'# Lifetime    : {lifetime_years:.0f} years\n')
        f.write(f'# Neq (RFC)   : {neq_lifetime:.3g}\n')
        f.write(f'# Reference   : {reference}\n')
        f.write(f'# Frame       : Tower fixed coordinate system (t suffix)\n')
        f.write(f'#               Mx = side-side   My = fore-aft   Mz = torsion\n')
        f.write(f'#               Fx = fore-aft    Fy = side-side  Fz = axial\n')
        f.write(f'# Table 1A    : Design extreme with PLF\n')
        f.write(f'# Table 1B    : Characteristic loads without PLF\n')
        f.write(f'# Table 1C    : Complementary loads (with PLF)\n')
        f.write(f'# Table 2A    : Lifetime RFC DEL'
                f'  m = {[fmt_slope(m) for m in rfc_slopes]}\n')
        f.write(f'# Table 2B    : Lifetime LDD DEL'
                f'  m = {[fmt_slope(m) for m in ldd_slopes]}\n')
        f.write(f'# Source EXT  : EXT/<sensor>.abs / _comp.abs headers\n')
        f.write(f'# Source FAT  : FAT/<sensor>.rfc / .ldd headers\n')
        f.write(f'# Note        : N/A = sensor not found or file missing\n')
        f.write(f'# {"=" * 70}\n\n\n')

        hdr_line = (f"#  {'Component':<{w_comp}}"
                    f"{'Value':>{w_val}}"
                    f"{'PLF':>{w_plf}}"
                    f"{'Driving_DLC':>{w_dlc}}\n")

        # ── TABLE 1A — Extreme with PLF ───────────────────────────────────────
        f.write(f'# {div}\n')
        f.write(f'# TABLE 1A — DESIGN EXTREME LOADS (with PLF)\n')
        f.write(f'# Source : EXT/<sensor>.abs — Design Extreme (with PLF) header\n')
        f.write(f'# {div}\n#\n')
        f.write(hdr_line)
        f.write(f'# {subdiv}\n')
        for comp in COMP_ORDER:
            sensor = sensors.get(comp)
            write_ext_row(f, label_for(comp, sensor), sensor, with_plf=True)
        f.write(f'# {subdiv}\n\n\n')

        # ── TABLE 1B — Characteristic loads without PLF ───────────────────────
        f.write(f'# {div}\n')
        f.write(f'# TABLE 1B — DESIGN CHARACTERISTIC LOADS (without PLF)\n')
        f.write(f'# Source : EXT/<sensor>.abs — Design Extreme (without PLF) header\n')
        f.write(f'# Used for geotechnical design (pile capacity, p-y curves)\n')
        f.write(f'# {div}\n#\n')
        f.write(hdr_line.replace('Value', 'Value_noPLF'))
        f.write(f'# {subdiv}\n')
        for comp in COMP_ORDER:
            sensor = sensors.get(comp)
            write_ext_row(f, label_for(comp, sensor), sensor, with_plf=False)
        f.write(f'# {subdiv}\n\n\n')

        # ── TABLE 1C — Complementary loads ───────────────────────────────────
        # Layout requested by user:
        #   - Column header units come from the configured FND sensor for each
        #     component (canonical order Mres, Mx, My, Mz, Fx, Fy, Fz)
        #   - Diagonal cell of row X (driving sensor X)  = Table 1A value of X
        #     (with PLF). Identical to the EXT/<X>.abs Design Extreme value.
        #   - Off-diagonal cell of row X, column Y       = Y-value at the
        #     time row X attained its peak, read from EXT/<X>_comp.abs.
        # Build canonical column labels with units from configured sensors.
        col_units = {}
        for comp in COMP_ORDER:
            s = sensors.get(comp)
            col_units[comp] = _get_unit(s) if s else '-'

        wc  = 12     # driving label width
        wcv = 14     # comp value width
        wcp =  7     # PLF width
        wcf = 18     # family width
        wcfl= 30     # file width
        wct =  9     # time width
        c_total = wc + len(COMP_ORDER)*wcv + wcp + wcf + wcfl + wct + 4
        c_subdiv = '─' * c_total

        def comp_hdr():
            h = f"#  {'Driving':<{wc}}"
            for c in COMP_ORDER:
                lbl = f"{c}_[{col_units[c]}]"
                h += f"  {lbl:>{wcv}}"
            h += (f"  {'PLF':>{wcp}}"
                  f"  {'Family':<{wcf}}"
                  f"  {'File':<{wcfl}}"
                  f"  {'Time_[s]':>{wct}}")
            return h

        def fmt_cell(v):
            return (f"  {v:>{wcv}.4f}" if v is not None
                    else f"  {'N/A':>{wcv}}")

        f.write(f'# {div}\n')
        f.write(f'# TABLE 1C — COMPLEMENTARY LOADS (with PLF)\n')
        f.write(f'# Source : EXT/<driving>_comp.abs — Design Driving Loads (Rank 1)\n')
        f.write(f'# Diag.  : value from EXT/<driving>.abs (Table 1A) — with PLF\n')
        f.write(f'# Off-d. : value of column-sensor at the instant the row\'s\n')
        f.write(f'#          driving sensor attained its design extreme\n')
        f.write(f'# {div}\n#\n')
        f.write(comp_hdr() + '\n')
        f.write(f'# {c_subdiv}\n')

        for drv_comp in COMP_ORDER:
            drv_sensor = sensors.get(drv_comp)
            row = f"  {drv_comp:<{wc}}"
            # Read this row's complementary record (off-diagonals) and its
            # Table-1A driving value (diagonal).
            dd = (read_comp_design_driving(ext_folder, drv_sensor,
                                           with_plf=True, log=log)
                  if drv_sensor and ext_folder else None)
            diag_val = None
            diag_plf = None
            diag_dlc = None
            if drv_sensor and drv_sensor in sensor_cols and ext_folder:
                diag_val, diag_plf, diag_dlc = read_ext_design_load(
                    ext_folder, drv_sensor, 'abs', with_plf=True, log=log)

            for col_comp in COMP_ORDER:
                if col_comp == drv_comp:
                    row += fmt_cell(diag_val)   # diagonal
                else:
                    val = dd['values'].get(col_comp) if dd else None
                    row += fmt_cell(val)

            if dd is not None:
                ts = dd.get('time_s')
                row += (f"  {dd['plf']:>{wcp}.2f}"
                        f"  {dd['family']:<{wcf}}"
                        f"  {dd['filename']:<{wcfl}}"
                        + (f"  {ts:>{wct}.2f}" if ts is not None
                           else f"  {'N/A':>{wct}}"))
            else:
                # No comp file: still try to show diagonal PLF/DLC if known
                if diag_plf is not None:
                    row += (f"  {diag_plf:>{wcp}.2f}"
                            f"  {(diag_dlc or 'N/A'):<{wcf}}"
                            f"  {'N/A':<{wcfl}}"
                            f"  {'N/A':>{wct}}")
                else:
                    row += (f"  {'—':>{wcp}}"
                            f"  {'N/A':<{wcf}}"
                            f"  {'N/A':<{wcfl}}"
                            f"  {'N/A':>{wct}}")
            f.write(row + '\n')
        f.write(f'# {c_subdiv}\n\n\n')

        # ── TABLE 2A — RFC DEL ────────────────────────────────────────────────
        rfc_hdrs = ''.join(f"{'DEL_m'+fmt_slope(m):>{w_val}}"
                           for m in rfc_slopes)
        f.write(f'# {div}\n')
        f.write(f'# TABLE 2A — LIFETIME RFC DEL\n')
        f.write(f'# Source : FAT/<sensor>.rfc — Lifetime DEL header\n')
        f.write(f'# Neq    : {neq_lifetime:.3g}    Method : RFC\n')
        f.write(f'# {div}\n#\n')
        f.write(f"#  {'Component':<{w_comp}}{rfc_hdrs}\n")
        f.write(f'# {"─" * (w_comp + len(rfc_slopes)*w_val)}\n')
        for comp in COMP_ORDER:
            sensor = sensors.get(comp)
            lbl    = label_for(comp, sensor)
            vals   = ''.join(
                fmt_val(read_fat_del_header(fat_folder, sensor, m,
                                            method='rfc', log=log)
                        if sensor and sensor in sensor_cols and fat_folder
                        else None)
                for m in rfc_slopes)
            f.write(f"   {lbl:<{w_comp}}{vals}\n")
        f.write(f'# {"─" * (w_comp + len(rfc_slopes)*w_val)}\n\n\n')

        # ── TABLE 2B — LDD DEL ────────────────────────────────────────────────
        ldd_hdrs = ''.join(f"{'LDD_m'+fmt_slope(m):>{w_val}}"
                           for m in ldd_slopes)
        f.write(f'# {div}\n')
        f.write(f'# TABLE 2B — LIFETIME LDD DEL\n')
        f.write(f'# Source : FAT/<sensor>.ldd — Lifetime DEL header\n')
        f.write(f'# Neq    : LIFETIME_SECS    Method : LDD (1Hz time-based)\n')
        f.write(f'# {div}\n#\n')
        f.write(f"#  {'Component':<{w_comp}}{ldd_hdrs}\n")
        f.write(f'# {"─" * (w_comp + len(ldd_slopes)*w_val)}\n')
        for sensor in ldd_sensors:
            if sensor not in sensor_cols:
                if log:
                    log.warning(f'{sensor} not in output — N/A in Table 2B',
                                tag='SUM')
            vals = ''.join(
                fmt_val(read_fat_del_header(fat_folder, sensor, m,
                                            method='ldd', log=log)
                        if sensor in sensor_cols and fat_folder
                        else None)
                for m in ldd_slopes)
            f.write(f"   {sensor:<{w_comp}}{vals}\n")
        f.write(f'# {"─" * (w_comp + len(ldd_slopes)*w_val)}\n\n')

        f.write(f'# {"=" * 70}\n')
        f.write('# END OF FILE\n')
        f.write(f'# {"=" * 70}\n')

    if log:
        log.file_written('SUM/FNDLoads.sum', tag='SUM')


# =============================================================================
# PitchBearing.sum writer
# =============================================================================

def write_pitch_bearing_sum(output_path, ptb_config,
                             family_order, sensor_cols,
                             lifetime_years, neq_lifetime,
                             ext_folder=None, fat_folder=None, log=None):
    """
    Write PitchBearing.sum — pitch bearing load summary.

    Table 1A : Design extreme AbsMax with PLF — worst of 3 blades
    Table 1B : Complementary loads with PLF   — worst blade _comp.abs
    Table 1C : Lifetime RFC DEL  m=3.3        — worst blade
    Table 1D : Lifetime LDD DEL  m=3.3        — worst blade

    Frame: Hub/Coned (c suffix) — rotates with rotor, NOT with blade pitch.

    Parameters
    ----------
    ptb_config    : dict from get_pitch_bearing_config()
    family_order  : list of str
    sensor_cols   : list of str
    lifetime_years: float
    neq_lifetime  : float
    ext_folder    : str
    fat_folder    : str
    log           : PostProcessLogger or None
    """
    rfc_slopes = ptb_config['rfc_slopes']
    ldd_slopes = ptb_config['ldd_slopes']
    blades     = ptb_config['blades']     # {B1: {comp: sensor}, ...}
    comps      = ptb_config['components'] # ['Mres','Mx','My','Mz','Fx','Fy','Fz']
    N_BLADES   = 3

    w_comp = 24
    w_val  = 16
    w_plf  =  8
    w_dlc  = 20
    w_bld  =  6
    div    = '═' * 70
    subdiv = '─' * (w_comp + w_val + w_plf + w_dlc + w_bld)

    def fmt_val(v):
        return f"{'N/A':>{w_val}}" if v is None else f"{v:>{w_val}.4f}"

    def fmt_plf(p):
        return f"{'—':>{w_plf}}" if p is None else f"{p:>{w_plf}.2f}"

    def fmt_dlc(d):
        return f"{'N/A':>{w_dlc}}" if d is None else f"{d:>{w_dlc}}"

    def fmt_bld(b):
        return f"{'N/A':>{w_bld}}" if b is None else f"{'[B'+str(b)+']':>{w_bld}}"

    def get_unit(sensor):
        return _get_unit(sensor) if sensor else '-'

    def label_for(comp, blade_key='B1'):
        sensor = blades.get(blade_key, {}).get(comp)
        return f"{comp}_[{get_unit(sensor)}]"

    def best_absmax_across_blades(comp):
        """
        Return (best_val, best_plf, best_dlc, best_blade) across all blades.
        Reads from EXT .abs header — picks highest AbsMax.
        """
        best_val = best_plf = best_dlc = best_blade = None
        for b in range(1, N_BLADES + 1):
            sensor = blades.get(f'B{b}', {}).get(comp)
            if not sensor or sensor not in sensor_cols or ext_folder is None:
                continue
            val, plf, dlc = read_ext_design_load(
                ext_folder, sensor, 'abs', with_plf=True, log=log)
            if val is None:
                continue
            if best_val is None or abs(val) > abs(best_val):
                best_val, best_plf, best_dlc, best_blade = val, plf, dlc, b
        return best_val, best_plf, best_dlc, best_blade

    def best_del_across_blades(comp, m, method='rfc'):
        """Return (best_del, best_blade) across all blades from FAT header."""
        best_val = best_blade = None
        for b in range(1, N_BLADES + 1):
            sensor = blades.get(f'B{b}', {}).get(comp)
            if not sensor or sensor not in sensor_cols or fat_folder is None:
                continue
            val = read_fat_del_header(fat_folder, sensor, m,
                                      method=method, log=log)
            if val is None:
                continue
            if best_val is None or val > best_val:
                best_val, best_blade = val, b
        return best_val, best_blade

    def best_comp_across_blades(comp):
        """
        Return (design_driving_dict, best_blade, diag_value) — reads
        EXT/<sensor>_comp.abs Design Driving block for each blade and picks
        the blade whose driving (column ``comp``) value has the largest
        magnitude. Also returns the corresponding Table-1A (with PLF)
        diagonal value via EXT/<sensor>.abs.
        """
        best_dd = best_blade = None
        best_drv = None
        best_diag = None
        for b in range(1, N_BLADES + 1):
            sensor = blades.get(f'B{b}', {}).get(comp)
            # if not sensor or sensor not in sensor_cols or ext_folder is None:
            #     continue
            if not sensor or ext_folder is None:
                continue
                
            # Check if the .abs file exists on disk
            abs_file_path = os.path.join(ext_folder, f"{sensor}.abs")
            
            # Bypass sensor_cols check for Mres/Resultant files
            if not (os.path.exists(abs_file_path) or sensor in sensor_cols):
                continue
            dd = read_comp_design_driving(ext_folder, sensor,
                                          with_plf=True, log=None)
            # Diagonal value: read Table 1A (.abs) for the SAME sensor
            diag_val, diag_plf, diag_dlc = read_ext_design_load(
                ext_folder, sensor, 'abs', with_plf=True, log=None)
            # Magnitude for ranking:
            mag = abs(diag_val) if diag_val is not None else None
            if mag is None and dd is not None:
                v = dd['values'].get(comp)
                mag = abs(v) if v is not None else None
            if mag is None:
                continue
            if best_drv is None or mag > best_drv:
                best_drv  = mag
                best_dd   = dd
                best_blade = b
                best_diag  = diag_val
        return best_dd, best_blade, best_diag

    with open(output_path, 'w', encoding='utf-8') as f:
        # ── File header ───────────────────────────────────────────────────────
        f.write(f'# {"=" * 70}\n')
        f.write(f'# PitchBearing.sum — Pitch Bearing Load Summary\n')
        f.write(f'# {"=" * 70}\n')
        f.write(f'# Lifetime    : {lifetime_years:.0f} years\n')
        f.write(f'# Neq (RFC)   : {neq_lifetime:.3g}\n')
        f.write(f'# Frame       : Hub/Coned coordinate system (c suffix)\n')
        f.write(f'#               Rotates with rotor, does NOT rotate with blade pitch\n')
        f.write(f'#               Mx = in-plane (edge)   My = out-of-plane (flap)\n')
        f.write(f'#               Mz = pitch drive torque\n')
        f.write(f'#               Mres = sqrt(Mxc^2 + Myc^2)\n')
        f.write(f'# Blades      : 3 — worst blade shown per component\n')
        f.write(f'# Table 1A    : Design extreme AbsMax with PLF\n')
        f.write(f'# Table 1B    : Complementary loads (with PLF)\n')
        f.write(f'# Table 1C    : Lifetime RFC DEL'
                f'  m = {[fmt_slope(m) for m in rfc_slopes]}\n')
        f.write(f'# Table 1D    : Lifetime LDD DEL'
                f'  m = {[fmt_slope(m) for m in ldd_slopes]}\n')
        f.write(f'# Table 2     : Pitch drive loads (Mz only — extreme + RFC + LDD)\n')
        f.write(f'# Fatigue m   : 3.3 — Hertzian contact S-N (rolling element bearing)\n')
        f.write(f'# Source EXT  : EXT/<sensor>.abs / _comp.abs headers\n')
        f.write(f'# Source FAT  : FAT/<sensor>.rfc / .ldd headers\n')
        f.write(f'# Note        : N/A = sensor not found or file missing\n')
        f.write(f'# {"=" * 70}\n\n\n')

        # ── TABLE 1A — Design extreme ─────────────────────────────────────────
        f.write(f'# {div}\n')
        f.write(f'# TABLE 1A — DESIGN EXTREME LOADS (with PLF)\n')
        f.write(f'# Source : EXT/<sensor>.abs — Design Extreme (with PLF) header\n')
        f.write(f'# Worst  : highest AbsMax across all 3 blades\n')
        f.write(f'# {div}\n#\n')
        f.write(f"#  {'Component':<{w_comp}}"
                f"{'AbsMax_PLF':>{w_val}}"
                f"{'PLF':>{w_plf}}"
                f"{'Driving_DLC':>{w_dlc}}"
                f"{'Blade':>{w_bld}}\n")
        f.write(f'# {subdiv}\n')
        for comp in comps:
            val, plf, dlc, bld = best_absmax_across_blades(comp)
            lbl = label_for(comp)
            f.write(f"   {lbl:<{w_comp}}"
                    f"{fmt_val(val)}"
                    f"{fmt_plf(plf)}"
                    f"{fmt_dlc(dlc)}"
                    f"{fmt_bld(bld)}\n")
        f.write(f'# {subdiv}\n\n\n')

        # ── TABLE 1B — Complementary loads ────────────────────────────────────
        # Layout per row:
        #   - Pick worst blade for the driving component (Table 1A magnitude).
        #   - Diagonal cell    = that blade's Table-1A (.abs) value, with PLF.
        #   - Off-diagonal cells = column-component values from
        #                         EXT/<that_blade_sensor>_comp.abs at the
        #                         instant of the driving extreme.
        # Column units come from configured PTB_<comp>_B1 sensors.
        col_units_ptb = {}
        for comp in comps:
            s = blades.get('B1', {}).get(comp)
            col_units_ptb[comp] = _get_unit(s) if s else '-'

        wc  = 10   # driving label width
        wcv = 14   # value width
        wcp =  7   # PLF width
        wcb =  5   # blade width
        wcf = 18   # family width
        wcfl= 30   # file width
        wct =  9   # time width
        c_total = wc + len(comps)*wcv + wcb + wcp + wcf + wcfl + wct + 4
        c_sub   = '─' * c_total

        def comp_hdr_ptb():
            h = f"#  {'Driving':<{wc}}"
            for c in comps:
                lbl = f"{c}_[{col_units_ptb[c]}]"
                h += f"  {lbl:>{wcv}}"
            h += (f"  {'Bld':>{wcb}}"
                  f"  {'PLF':>{wcp}}"
                  f"  {'Family':<{wcf}}"
                  f"  {'File':<{wcfl}}"
                  f"  {'Time_[s]':>{wct}}")
            return h

        def fmt_cell_ptb(v):
            return (f"  {v:>{wcv}.4f}" if v is not None
                    else f"  {'N/A':>{wcv}}")

        f.write(f'# {div}\n')
        f.write(f'# TABLE 1B — COMPLEMENTARY LOADS (with PLF)\n')
        f.write(f'# Source : EXT/<sensor>_comp.abs — Design Driving Loads (Rank 1)\n')
        f.write(f'# Diag.  : value from EXT/<sensor>.abs (Table 1A) — with PLF\n')
        f.write(f'# Off-d. : value of column-sensor at the driving extreme instant\n')
        f.write(f'# Worst  : blade with highest driving sensor magnitude\n')
        f.write(f'# {div}\n#\n')
        f.write(comp_hdr_ptb() + '\n')
        f.write(f'# {c_sub}\n')

        for drv_comp in comps:
            dd, bld, diag_val = best_comp_across_blades(drv_comp)
            row = f"  {drv_comp:<{wc}}"
            for col_comp in comps:
                if col_comp == drv_comp:
                    row += fmt_cell_ptb(diag_val)
                else:
                    v = dd['values'].get(col_comp) if dd else None
                    row += fmt_cell_ptb(v)
            bld_str = f'[B{bld}]' if bld else 'N/A'
            if dd is not None:
                ts = dd.get('time_s')
                row += (f"  {bld_str:>{wcb}}"
                        f"  {dd['plf']:>{wcp}.2f}"
                        f"  {dd['family']:<{wcf}}"
                        f"  {dd['filename']:<{wcfl}}"
                        + (f"  {ts:>{wct}.2f}" if ts is not None
                           else f"  {'N/A':>{wct}}"))
            else:
                row += (f"  {bld_str:>{wcb}}"
                        f"  {'—':>{wcp}}"
                        f"  {'N/A':<{wcf}}"
                        f"  {'N/A':<{wcfl}}"
                        f"  {'N/A':>{wct}}")
            f.write(row + '\n')
        f.write(f'# {c_sub}\n\n\n')

        # ── TABLE 1C — RFC DEL ────────────────────────────────────────────────
        rfc_hdrs = ''.join(f"{'DEL_m'+fmt_slope(m):>{w_val}}"
                           for m in rfc_slopes)
        f.write(f'# {div}\n')
        f.write(f'# TABLE 1C — LIFETIME RFC DEL\n')
        f.write(f'# Source : FAT/<sensor>.rfc — Lifetime DEL header\n')
        f.write(f'# Neq    : {neq_lifetime:.3g}    m : {[fmt_slope(m) for m in rfc_slopes]}\n')
        f.write(f'# Worst  : highest DEL across all 3 blades\n')
        f.write(f'# {div}\n#\n')
        f.write(f"#  {'Component':<{w_comp}}{rfc_hdrs}{'Blade':>{w_bld}}\n")
        f.write(f'# {"─" * (w_comp + len(rfc_slopes)*w_val + w_bld)}\n')
        for comp in comps:
            lbl = label_for(comp)
            vals_str = ''
            worst_bld = None
            for m in rfc_slopes:
                val, bld = best_del_across_blades(comp, m, method='rfc')
                vals_str += fmt_val(val)
                if worst_bld is None and bld is not None:
                    worst_bld = bld
            f.write(f"   {lbl:<{w_comp}}{vals_str}{fmt_bld(worst_bld)}\n")
        f.write(f'# {"─" * (w_comp + len(rfc_slopes)*w_val + w_bld)}\n\n\n')

        # ── TABLE 1D — LDD DEL ────────────────────────────────────────────────
        ldd_hdrs = ''.join(f"{'DEL_m'+fmt_slope(m):>{w_val}}"
                           for m in ldd_slopes)
        f.write(f'# {div}\n')
        f.write(f'# TABLE 1D — LIFETIME LDD DEL\n')
        f.write(f'# Source : FAT/<sensor>.ldd — Lifetime DEL header\n')
        f.write(f'# Neq    : LIFETIME_SECS    m : {[fmt_slope(m) for m in ldd_slopes]}\n')
        f.write(f'# Worst  : highest DEL across all 3 blades\n')
        f.write(f'# {div}\n#\n')
        f.write(f"#  {'Component':<{w_comp}}{ldd_hdrs}{'Blade':>{w_bld}}\n")
        f.write(f'# {"─" * (w_comp + len(ldd_slopes)*w_val + w_bld)}\n')
        for comp in comps:
            lbl = label_for(comp)
            vals_str = ''
            worst_bld = None
            for m in ldd_slopes:
                val, bld = best_del_across_blades(comp, m, method='ldd')
                vals_str += fmt_val(val)
                if worst_bld is None and bld is not None:
                    worst_bld = bld
            f.write(f"   {lbl:<{w_comp}}{vals_str}{fmt_bld(worst_bld)}\n")
        f.write(f'# {"─" * (w_comp + len(ldd_slopes)*w_val + w_bld)}\n\n\n')

        # ── TABLE 2 — Pitch drive (Mz only) ──────────────────────────────────
        # Pitch drive sees the pitch-axis torque, which in OpenFAST is the
        # blade-root in-pitch-axis moment Mz_c (configured PTB_Mz_B*).
        # Reports: extreme AbsMax with PLF + RFC DEL + LDD DEL for the worst
        # blade, on the same Mz sensor as Table 1A.
        f.write(f'# {div}\n')
        f.write(f'# TABLE 2 — PITCH DRIVE LOADS  (Mz only — pitch-axis torque)\n')
        f.write(f'# Source : EXT/<RootMzc#>.abs (extreme) and FAT/<RootMzc#>.rfc/.ldd (DEL)\n')
        f.write(f'# Worst  : blade with highest |Mz_c| / highest DEL\n')
        f.write(f'# {div}\n#\n')

        # ── 2A: Extreme Mz with PLF (worst blade) ────────────────────────────
        f.write(f'# ── 2A: Extreme Mz (with PLF) ─────────────────────────────\n')
        f.write(f"#  {'Component':<{w_comp}}"
                f"{'AbsMax_PLF':>{w_val}}"
                f"{'PLF':>{w_plf}}"
                f"{'Driving_DLC':>{w_dlc}}"
                f"{'Blade':>{w_bld}}\n")
        f.write(f'# {"─" * (w_comp + w_val + w_plf + w_dlc + w_bld)}\n')
        val, plf, dlc, bld = best_absmax_across_blades('Mz')
        lbl = label_for('Mz')
        f.write(f"   {lbl:<{w_comp}}"
                f"{fmt_val(val)}{fmt_plf(plf)}{fmt_dlc(dlc)}{fmt_bld(bld)}\n")
        f.write(f'# {"─" * (w_comp + w_val + w_plf + w_dlc + w_bld)}\n\n')

        # ── 2B: RFC DEL on Mz (worst blade) ──────────────────────────────────
        rfc_hdrs = ''.join(f"{'DEL_m'+fmt_slope(m):>{w_val}}" for m in rfc_slopes)
        f.write(f'# ── 2B: RFC DEL on Mz ─────────────────────────────────────\n')
        f.write(f"#  {'Component':<{w_comp}}{rfc_hdrs}{'Blade':>{w_bld}}\n")
        f.write(f'# {"─" * (w_comp + len(rfc_slopes)*w_val + w_bld)}\n')
        vals_str  = ''
        worst_bld = None
        for m in rfc_slopes:
            v, b = best_del_across_blades('Mz', m, method='rfc')
            vals_str += fmt_val(v)
            if worst_bld is None and b is not None:
                worst_bld = b
        f.write(f"   {label_for('Mz'):<{w_comp}}{vals_str}{fmt_bld(worst_bld)}\n")
        f.write(f'# {"─" * (w_comp + len(rfc_slopes)*w_val + w_bld)}\n\n')

        # ── 2C: LDD DEL on Mz (worst blade) ──────────────────────────────────
        ldd_hdrs = ''.join(f"{'DEL_m'+fmt_slope(m):>{w_val}}" for m in ldd_slopes)
        f.write(f'# ── 2C: LDD DEL on Mz ─────────────────────────────────────\n')
        f.write(f"#  {'Component':<{w_comp}}{ldd_hdrs}{'Blade':>{w_bld}}\n")
        f.write(f'# {"─" * (w_comp + len(ldd_slopes)*w_val + w_bld)}\n')
        vals_str  = ''
        worst_bld = None
        for m in ldd_slopes:
            v, b = best_del_across_blades('Mz', m, method='ldd')
            vals_str += fmt_val(v)
            if worst_bld is None and b is not None:
                worst_bld = b
        f.write(f"   {label_for('Mz'):<{w_comp}}{vals_str}{fmt_bld(worst_bld)}\n")
        f.write(f'# {"─" * (w_comp + len(ldd_slopes)*w_val + w_bld)}\n\n')

        f.write(f'# {"=" * 70}\n')
        f.write('# END OF FILE\n')
        f.write(f'# {"=" * 70}\n')

    if log:
        log.file_written('SUM/PitchBearing.sum', tag='SUM')
