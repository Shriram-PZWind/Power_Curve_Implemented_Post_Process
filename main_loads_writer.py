# =============================================================================
# main_loads_writer.py — MainLoads.sum master turbine load summary writer
# =============================================================================
#
# Reads ALL values from SUM folder .sum files + EXT folder (Section 9 only).
# All slopes and sensor names read from config at runtime — nothing hardcoded.
# =============================================================================

import os
import re
import numpy as np

from blade_spline import interpolate_blade_loads, nearest_idx
from config import (get_blade_config, get_hub_config,
                    get_pitch_bearing_config, get_drivetrain_config,
                    get_yaw_config, get_tower_config, get_foundation_config,
                    get_tower_clearance_config, fmt_slope)
from sum_writer import read_ext_design_load


# =============================================================================
# Column widths
# =============================================================================
W_SENS  = 30   # sensor/component label
W_VAL   = 16   # value
W_EXTRA = 30   # DLC / blade / extra info


# =============================================================================
# Generic .sum file helpers
# =============================================================================

def _read_lines(sum_folder, filename, log=None):
    path = os.path.join(sum_folder, filename)
    if not os.path.isfile(path):
        if log:
            log.warning(f'{filename} not found in SUM folder — section skipped',
                        tag='MainLoads')
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return f.readlines()


# def _find_section(lines, marker):
#     """
#     Find the index of the actual section/table header line that contains
#     ``marker`` (case-insensitive). The match must be on a line that ALSO
#     looks like a real header — i.e. it begins with '# <marker> — ...' or
#     '# <marker> ...' as opposed to mere docstring mentions like
#     '# Table 1A : Design extreme with PLF' that may appear in the file's
#     intro paragraph above the actual tables.

#     Returns -1 if nothing matched.
#     """
#     ml = marker.lower()
#     fallback = -1
#     for i, line in enumerate(lines):
#         s = line.strip()
#         sl = s.lower()
#         if ml not in sl:
#             continue
#         if not s.startswith('#'):
#             continue
#         # Strip leading '#' and whitespace
#         body = s.lstrip('#').strip()
#         bl = body.lower()
#         # Real header lines start with the marker keyword (e.g. 'TABLE 1A').
#         if bl.startswith(ml + ' ') or bl.startswith(ml + '\t') or \
#            bl.startswith(ml + '—') or bl == ml:
#             return i
#         # Keep first match as a last-resort fallback
#         if fallback == -1:
#             fallback = i
#     return fallback

def _find_section(lines, marker):
    """
    Finds the actual table header while ignoring the intro/summary list.
    """
    ml = marker.lower()
    for i, line in enumerate(lines):
        s = line.strip()
        if not s.startswith('#'):
            continue
            
        # Strip '#' and whitespace to get the clean title
        body = s.lstrip('#').strip()
        bl = body.lower()
        
        if bl.startswith(ml):
            # 1. Get the text immediately following the marker (e.g., after 'TABLE 1A')
            remainder = bl[len(ml):].lstrip()
            
            # 2. CRITICAL: If it's a colon ':', it's just the intro list. SKIP IT.
            if remainder.startswith(':'):
                continue
            
            # 3. Real headers usually have a dash (— or -) or are the end of the line
            if remainder.startswith('—') or remainder.startswith('-') or not remainder:
                return i
                
    return -1


def _is_banner_only(line_stripped):
    """
    True iff the line is a 'banner' rule line — only '#' and box-drawing
    characters / '=' / '-' / whitespace. Such lines BORDER section/table
    headers but carry no data and no new section keyword; they must be
    skipped, not used as a stop signal.
    """
    if not line_stripped.startswith('#'):
        return False
    body = line_stripped[1:].strip()
    if not body:
        return True
    # Allow only '═', '=', '─', '-', '·', whitespace
    return all(c in ' \t═=─-·' for c in body)


def _is_new_section_marker(line_stripped):
    """
    True iff the line is a NEW section/table/end marker that should stop
    the data-row scan. Recognises words SECTION / TABLE / END and the
    'END OF FILE' phrase.
    """
    if not line_stripped.startswith('#'):
        return False
    upper = line_stripped.upper()
    if 'END OF FILE' in upper:
        return True
    # Strip leading '#' and any banner characters; look for keywords.
    body = line_stripped.lstrip('#').strip(' \t═=─-·')
    upper_body = body.upper()
    return (upper_body.startswith('SECTION ') or
            upper_body.startswith('TABLE ')   or
            upper_body == 'END' or
            upper_body.startswith('END '))


def _parse_data_rows(lines, start_idx, max_rows=50):
    """
    Parse data rows after start_idx until a NEW section/table/end marker or
    max_rows. Banner-only comment lines (the '═══' or '───' rule lines that
    bracket section headers) are skipped, not used as stops.
    Returns list of (raw_line, parts) for non-comment, non-empty data lines.
    """
    rows = []
    for line in lines[start_idx + 1:]:
        s = line.strip()
        if not s:
            continue
        if s.startswith('#'):
            # Banner rule line — skip silently
            if _is_banner_only(s):
                continue
            # Real new section / table marker — stop
            if _is_new_section_marker(s):
                break
            # Other comment lines (column headers, dotted dividers) — skip
            continue
        parts = s.split()
        if parts:
            rows.append((s, parts))
        if len(rows) >= max_rows:
            break
    return rows


def _val(parts, idx=0):
    try:
        return float(parts[idx])
    except (IndexError, ValueError):
        return None


def _str(parts, idx=0):
    try:
        return parts[idx]
    except IndexError:
        return None


# =============================================================================
# Writer helpers
# =============================================================================

def _banner(f, title, subtitle='', source=''):
    f.write(f'\n\n# {"═" * 76}\n')
    f.write(f'# {title}\n')
    if subtitle:
        f.write(f'# {subtitle}\n')
    if source:
        f.write(f'# Source : {source}\n')
    f.write(f'# {"═" * 76}\n#\n')


def _sub(f, title):
    f.write(f'#\n# ── {title} {"─" * max(0, 68 - len(title))}\n#\n')


def _col_hdr(f, label='Sensor/Component', extra=''):
    f.write(f"#  {label:<{W_SENS}}{'Value':>{W_VAL}}{extra}\n")
    f.write(f'# {"─" * (W_SENS + W_VAL + len(extra))}\n')


def _vrow(f, label, value):
    val_s = f"{'N/A':>{W_VAL}}" if value is None else f"{value:>{W_VAL}.4f}"
    f.write(f"   {label:<{W_SENS}}{val_s}\n")


def _drow(f, label, dlc_str):
    f.write(f"   {label:<{W_SENS}}{dlc_str}\n")


def _dotdiv(f):
    f.write(f'# {"." * (W_SENS + W_VAL)}\n')


def _subdiv(f):
    f.write(f'# {"─" * (W_SENS + W_VAL)}\n')


# =============================================================================
# Section parsers — read from individual .sum files
# =============================================================================

# def _parse_bld_section(lines, section_marker):
#     """
#     Parse a BldLoads.sum section.
#     Returns {labels, radii, values, dlcs, blades, plfs}.
#     """
#     idx = _find_section(lines, section_marker)
#     if idx == -1:
#         return None

#     # Read value rows until dotted separator or NEW section/table marker.
#     # Banner-only rule lines must be SKIPPED, not used as stop signals.
#     val_rows = []
#     dlc_rows = []
#     in_dlc   = False
#     for line in lines[idx + 1:]:
#         s = line.strip()
#         if not s:
#             continue
#         if s.startswith('#'):
#             if _is_banner_only(s):
#                 continue
#             if _is_new_section_marker(s):
#                 break
#             if '...' in s or '···' in s:
#                 in_dlc = True
#             continue
#         parts = s.split()
#         if not parts:
#             continue
#         if not in_dlc:
#             val_rows.append(parts)
#         else:
#             dlc_rows.append(parts)

#     # Extract radii from label e.g. 'Root' → 0.0, '25%' → use blade_length later
#     # Format: label  radius_str  value
#     labels = []; radii = []; values = []
#     for parts in val_rows:
#         labels.append(parts[0])
#         # Try to extract radius from second column e.g. '15.4m' or '0.0m'
#         r = None
#         if len(parts) > 1:
#             try:
#                 r = float(re.sub(r'[^\d.]', '', parts[1]))
#             except ValueError:
#                 pass
#         radii.append(r)
#         values.append(_val(parts, 2) if len(parts) > 2 else _val(parts, 1))

#     # DLC rows format: label  radius  DLC_name  (PLF=x.xx)  [Bx]
#     dlcs = []; blades = []; plfs = []
#     for parts in dlc_rows:
#         dlc = _str(parts, 2)
#         plf_s = next((p for p in parts if 'PLF' in p.upper()), None)
#         plf = None
#         if plf_s:
#             try:
#                 plf = float(re.sub(r'[^0-9.]', '', plf_s))
#             except ValueError:
#                 pass
#         bld = next((p for p in parts if p.startswith('[B')), None)
#         dlcs.append(dlc); plfs.append(plf); blades.append(bld)

#     return {'labels': labels, 'radii': radii, 'values': values,
#             'dlcs': dlcs, 'blades': blades, 'plfs': plfs}

def _parse_bld_section(lines, section_marker, comp):
    """
    Parse a BldLoads.sum section dynamically tracking column headers.
    Returns {radii, values, dlcs, blades, plfs}.
    """
    idx = _find_section(lines, section_marker)
    if idx == -1:
        return None

    val_rows = []
    dlc_rows = []
    in_dlc   = False
    header_line = None

    # Dynamically find the header row containing "Radial_"
    for line in lines[idx + 1:]:
        s = line.strip()
        if "Radial_" in s:
            header_line = s
            break
            
    if not header_line:
        return None

    # Determine column index for the requested component (Mx or My)
    hdr_parts = [p.upper() for p in header_line.replace('#', '').split()]
    comp_col_idx = -1
    for i, part in enumerate(hdr_parts):
        if comp.upper() + '_' in part:
            comp_col_idx = i
            break

    if comp_col_idx == -1:
        return None

    # Separate values block from DLC/PLF block
    for line in lines[idx + 1:]:
        s = line.strip()
        if not s:
            continue
        if s.startswith('#'):
            if _is_banner_only(s):
                continue
            if _is_new_section_marker(s):
                if section_marker not in s:
                    break
            if '...' in s or '···' in s:
                in_dlc = True
            continue
        parts = s.split()
        if not parts or len(parts) < 2:
            continue
        if not in_dlc:
            val_rows.append(parts)
        else:
            dlc_rows.append(parts)

    radii, values, dlcs, blades, plfs = [], [], [], [], []

    # Extract data columns accounting for label-induced spacing shifts
    for v_parts in val_rows:
        try:
            r_val = float(v_parts[0])
            radii.append(r_val)
            
            val_str = v_parts[2 * comp_col_idx]
            values.append(None if val_str.upper() == 'N/A' else float(val_str))
        except (ValueError, IndexError):
            continue

    # Extract metadata blocks (Extreme tables only)
    if dlc_rows:
        for d_parts in dlc_rows:
            try:
                dlc_start = 2 + 3 * (comp_col_idx - 1)
                dlc_str = d_parts[dlc_start]
                plf_str = d_parts[dlc_start + 1]
                bld_str = d_parts[dlc_start + 2]

                dlcs.append(None if dlc_str.upper() == 'N/A' else dlc_str)
                
                if 'PLF=' in plf_str:
                    plf_val = float(plf_str.split('PLF=')[1].replace(')', ''))
                else:
                    plf_val = None
                plfs.append(plf_val)
                blades.append(bld_str)
            except (ValueError, IndexError):
                dlcs.append(None)
                plfs.append(None)
                blades.append(None)
    else:
        dlcs = [None] * len(radii)
        blades = [None] * len(radii)
        plfs = [None] * len(radii)

    return {
        'radii': radii,
        'values': values,
        'dlcs': dlcs,
        'blades': blades,
        'plfs': plfs
    }

def _parse_hub_section1(lines):
    """
    Parse HubLoads.sum Section 1.
    Returns list of {label, val, plf, dlc_blade}.
    """
    idx = _find_section(lines, 'SECTION 1')
    if idx == -1:
        return []
    rows = _parse_data_rows(lines, idx)
    result = []
    for _, parts in rows:
        # Format: label  val_plf  plf  dlc  [Bx]
        dlc_blade = ' '.join(parts[3:]) if len(parts) > 3 else ''
        result.append({'label': parts[0], 'val': _val(parts, 1),
                       'plf': _val(parts, 2), 'dlc_blade': dlc_blade})
        
    # print("result is this", result)
    return result


def _parse_hub_section2(lines, slopes):
    """
    Parse HubLoads.sum Section 2 DEL.
    Returns {slope: {label: (val, blade)}}.
    """
    idx = _find_section(lines, 'SECTION 2')
    if idx == -1:
        return {}
    rows = _parse_data_rows(lines, idx)
    result = {}
    for _, parts in rows:
        label = parts[0]
        # Columns: DEL_m3.0 [B1]  DEL_m6.0 [B2]  ...
        for i, m in enumerate(slopes):
            col_base = 1 + i * 2  # val at col_base, blade at col_base+1
            val   = _val(parts, col_base)
            blade = _str(parts, col_base + 1)
            if m not in result:
                result[m] = {}
            result[m][label] = (val, blade)
    return result


# def _parse_ptb_table(lines, table_marker):
#     """Parse a PitchBearing.sum table. Returns list of {label, cols}."""
#     idx = _find_section(lines, table_marker)
#     if idx == -1:
#         return []
#     rows = _parse_data_rows(lines, idx,
#                              max_rows=20)
#     return [{'label': r[1][0], 'cols': r[1][1:]} for r in rows]

def _parse_ptb_table(lines, table_marker):
    idx = _find_section(lines, table_marker)
    if idx == -1:
        return []
    
    # Increase max_rows if your tables are long
    rows = _parse_data_rows(lines, idx, max_rows=30) 
    
    # .strip() the label here so the "if 'MRES' in label" check is cleaner
    return [{'label': r[1][0].strip(), 'cols': r[1][1:]} for r in rows]


def _parse_twr_section(lines, marker, component_idx=0):
    """
    Parse a TwrLoads.sum section.
    Returns list of {label, val, dlc} — one component (default Mres) only.
    Handles value rows and DLC rows (separated by dotted line).
    Skips banner-only rule lines so the parser does not stop on them.
    """
    idx = _find_section(lines, marker)
    if idx == -1:
        return []

    val_rows = []; dlc_rows = []; in_dlc = False
    for line in lines[idx + 1:]:
        s = line.strip()
        if not s:
            continue
        if s.startswith('#'):
            # Banner rule — skip
            if _is_banner_only(s):
                continue
            # NEW section / table / end marker — stop
            if _is_new_section_marker(s):
                break
            # Dotted divider between value rows and DLC rows
            if '...' in s or '···' in s or '----' in s.replace(' ', ''):
                in_dlc = True
            # Column header etc — skip
            continue
        parts = s.split()
        if not parts:
            continue
        # First token of a station row may include parens, e.g. "Top  ( 87.6m)".
        # Coalesce label tokens until we hit a numeric token.
        # Numeric detection: float(token) succeeds.
        lbl_tokens = [parts[0]]
        i = 1
        while i < len(parts):
            try:
                float(parts[i])
                break
            except ValueError:
                lbl_tokens.append(parts[i])
                i += 1
        # Reconstruct: label = first token only (ignore parens text);
        #              numeric tokens follow at index i.
        label = lbl_tokens[0]
        numeric = parts[i:]
        if not in_dlc:
            val_rows.append((label, numeric))
        else:
            dlc_rows.append((label, numeric))

    result = []
    for i, (label, numeric) in enumerate(val_rows):
        # value at component_idx position in the numeric list
        val = None
        if 0 <= component_idx < len(numeric):
            try:
                val = float(numeric[component_idx])
            except ValueError:
                val = None
        # DLC row mirrors structure
        dlc = None
        if i < len(dlc_rows):
            dlc_lbl, dlc_num = dlc_rows[i]
            if 0 <= component_idx < len(dlc_num):
                dlc = dlc_num[component_idx]
        result.append({'label': label, 'val': val, 'dlc': dlc})
    return result


# def _parse_fnd_table(lines, table_marker):
#     """Parse FNDLoads.sum table. Returns {label: {val, plf, dlc}}.
#     Row layout:  parts[0]=label  parts[1]=val  parts[2]=plf  parts[3]=dlc
#     """
#     idx = _find_section(lines, table_marker)
#     if idx == -1:
#         return {}
#     rows = _parse_data_rows(lines, idx, max_rows=15)
#     result = {}
#     for _, parts in rows:
#         label = parts[0]
#         result[label] = {
#             'val': _val(parts, 1),
#             'plf': _val(parts, 2),
#             'dlc': _str(parts, 3),
#         }
#     return result

def _parse_fnd_table(lines, table_marker):
    """Parse FNDLoads.sum table. Returns {label: [cols]}."""
    idx = _find_section(lines, table_marker)
    if idx == -1:
        return {}
    rows = _parse_data_rows(lines, idx, max_rows=15)
    result = {}
    for _, parts in rows:
        # Store all numeric columns as a list
        result[parts[0]] = parts[1:]
    return result


# def _parse_drt_table(lines, table_marker):
#     """Parse DRTLoads.sum table. Returns list of {label, cols}."""
#     idx = _find_section(lines, table_marker)
#     if idx == -1:
#         return []
#     rows = _parse_data_rows(lines, idx, max_rows=30)
#     # Split on frame separator comments but keep them as markers
#     result = []
#     for _, parts in rows:
#         result.append({'label': parts[0], 'cols': parts[1:]})
#     return result

def _parse_drt_table(lines, table_marker):
    """Parse DRTLoads.sum table. Returns list of {label, cols}."""
    idx = _find_section(lines, table_marker)
    if idx == -1:
        return []
    
    result = []
    # We loop manually to catch the # frame markers
    for i in range(idx + 1, len(lines)):
        line = lines[i].strip()
        if not line: continue
        # Stop if we hit the next table
        if 'TABLE' in line.upper() and i > idx + 5: break 
        
        if line.startswith('#'):
            # Keep the comment as a label so _frame_rows can find keywords
            result.append({'label': line, 'cols': []})
        else:
            parts = line.split()
            if parts:
                result.append({'label': parts[0], 'cols': parts[1:]})
    return result


def _parse_yaw_section(lines, marker):
    """
    Parse YawLoads.sum section.
    Returns {val_rows: [(label, val, plf, dlc)], ...}
    Split by dotted separators into loads / trans_accel / ang_accel.
    """
    idx = _find_section(lines, marker)
    if idx == -1:
        return {'loads': [], 'trans': [], 'ang': []}

    blocks = [[], [], []]
    block_idx = 0
    for line in lines[idx + 1:]:
        s = line.strip()
        if not s:
            continue
        if s.startswith('#'):
            if _is_banner_only(s):
                continue
            if _is_new_section_marker(s):
                break
            if '...' in s or '···' in s:
                block_idx = min(block_idx + 1, 2)
            continue
        parts = s.split()
        if parts:
            blocks[block_idx].append(parts)

    def _parse_block(rows):
        return [{'label': p[0], 'val': _val(p, 1),
                 'plf': _val(p, 2), 'dlc': _str(p, 3)}
                for p in rows]

    return {'loads': _parse_block(blocks[0]),
            'trans': _parse_block(blocks[1]),
            'ang'  : _parse_block(blocks[2])}


# =============================================================================
# Section writers
# =============================================================================

# def _write_blade(f, bld_lines, bld_cfg):
#     """Write SECTION 1 — Blade."""
#     blade_length = bld_cfg.get('BLADE_LENGTH', 61.5)
#     del_slopes   = bld_cfg.get('SUM_DEL_SLOPES', [10.0, 12.0, 25.0])
#     fractions    = bld_cfg.get('MAIN_BLD_FRACTIONS', [0.0, 0.25, 0.50, 0.75])

#     _banner(f, 'SECTION 1 — BLADE LOADS',
#             f'Blade length : {blade_length:.1f} m   '
#             f'Frame : Blade (b suffix)   '
#             f'Mx = edgewise   My = flapwise',
#             'SUM/BldLoads.sum')
#     f.write(f'# Stations : cubic spline interpolation at '
#             f'{[f"{int(round(fr*100))}%" for fr in fractions]}\n')
#     f.write(f'# DLC      : from nearest spanwise station\n#\n')

#     EXT_TABLES = [
#         ('TABLE 1A', 'SECTION 1', 'Mx', 'MAX'),
#         ('TABLE 1B', 'SECTION 2', 'Mx', 'MIN'),
#         ('TABLE 1C', 'SECTION 3', 'My', 'MAX'),
#         ('TABLE 1D', 'SECTION 4', 'My', 'MIN'),
#     ]
#     DEL_TABLES = [
#         ('TABLE 1E', 'Mx', 'SECTION 5'),
#         ('TABLE 1F', 'My', 'SECTION 6'),
#     ]

#     for tbl_id, sec_marker, comp, stat in EXT_TABLES:
#         sec = _parse_bld_section(bld_lines, sec_marker)
#         f.write(f'# {tbl_id} — {comp}  {stat} (with PLF)\n#\n')
#         _col_hdr(f, 'Station')

#         pts = []
#         if sec and sec['radii']:
#             valid = [(r, v, d, b, p)
#                      for r, v, d, b, p in zip(
#                          sec['radii'], sec['values'],
#                          sec['dlcs'],  sec['blades'], sec['plfs'])
#                      if r is not None]
#             if valid:
#                 radii_v  = [x[0] for x in valid]
#                 values_v = [x[1] for x in valid]
#                 dlcs_v   = [x[2] for x in valid]
#                 blades_v = [x[3] for x in valid]
#                 plfs_v   = [x[4] for x in valid]
#                 pts = interpolate_blade_loads(
#                     radii_v, values_v, dlcs_v, blades_v, plfs_v,
#                     blade_length, fractions)

#         if not pts:
#             for fr in fractions:
#                 lbl = 'Root' if fr == 0.0 else f'{int(round(fr*100))}%'
#                 _vrow(f, lbl, None)
#             _dotdiv(f)
#             for fr in fractions:
#                 lbl = 'Root' if fr == 0.0 else f'{int(round(fr*100))}%'
#                 _drow(f, lbl, 'N/A')
#         else:
#             for pt in pts:
#                 _vrow(f, pt['label'], pt['value'])
#             _dotdiv(f)
#             for pt in pts:
#                 plf_s = f"PLF={pt['plf']:.2f}" if pt['plf'] else 'PLF=—'
#                 bld_s = pt['blade_tag'] or ''
#                 dlc_s = pt['dlc'] or 'N/A'
#                 _drow(f, pt['label'], f"{dlc_s}  ({plf_s})  {bld_s}")
#         _subdiv(f)
#         f.write('#\n')

#     # DEL tables — stacked m blocks
#     for tbl_id, comp, sec_base in DEL_TABLES:
#         f.write(f'# {tbl_id} — {comp}  RFC DEL\n#\n')
#         f.write(f"#  {'Station':<{W_SENS}}{'DEL_[kN-m]':>{W_VAL}}\n")
#         f.write(f'# {"─" * (W_SENS + W_VAL)}\n')
#         for slope_idx, m in enumerate(del_slopes):
#             # BldLoads.sum DEL sections follow EXT sections
#             # Section numbering: EXT = 1-4, DEL starts at 5
#             # For Mx DEL: sections 5, 6, 7... for each slope
#             # For My DEL: sections after Mx
#             sec_num  = (5 + slope_idx) if comp == 'Mx' \
#                        else (5 + len(del_slopes) + slope_idx)
#             sec_marker = f'SECTION {sec_num}'
#             sec = _parse_bld_section(bld_lines, sec_marker)
#             f.write(f'# m = {fmt_slope(m)}\n')
#             pts = []
#             if sec and sec['radii']:
#                 valid = [(r, v) for r, v in zip(sec['radii'], sec['values'])
#                          if r is not None]
#                 if valid:
#                     rv = [x[0] for x in valid]
#                     vv = [x[1] for x in valid]
#                     pts = interpolate_blade_loads(
#                         rv, vv, [None]*len(rv), [None]*len(rv),
#                         [None]*len(rv), blade_length, fractions)
#             if not pts:
#                 for fr in fractions:
#                     lbl = 'Root' if fr == 0.0 else f'{int(round(fr*100))}%'
#                     _vrow(f, lbl, None)
#             else:
#                 for pt in pts:
#                     _vrow(f, pt['label'], pt['value'])
#         _subdiv(f)
#         f.write('#\n')

def _write_blade(f, bld_lines, bld_cfg):
    """Write SECTION 1 — Blade."""
    blade_length = bld_cfg.get('BLADE_LENGTH', 61.5)
    del_slopes   = bld_cfg.get('SUM_DEL_SLOPES', [10.0, 12.0, 25.0])
    fractions    = bld_cfg.get('MAIN_BLD_FRACTIONS', [0.0, 0.25, 0.50, 0.75])

    _banner(f, 'SECTION 1 — BLADE LOADS',
            f'Blade length : {blade_length:.1f} m   '
            f'Frame : Blade (b suffix)   '
            f'Mx = edgewise   My = flapwise',
            'SUM/BldLoads.sum')
    f.write(f'# Stations : cubic spline interpolation at '
            f'{[f"{int(round(fr*100))}%" for fr in fractions]}\n')
    f.write(f'# DLC      : from nearest spanwise station\n#\n')

    # FIX: Map components to correct shared Sections (1 and 2)
    EXT_TABLES = [
        ('TABLE 1A', 'SECTION 1', 'Mx', 'MAX'),
        ('TABLE 1B', 'SECTION 2', 'Mx', 'MIN'),
        ('TABLE 1C', 'SECTION 1', 'My', 'MAX'),
        ('TABLE 1D', 'SECTION 2', 'My', 'MIN'),
    ]
    DEL_TABLES = [
        ('TABLE 1E', 'Mx', 'SECTION 3'),
        ('TABLE 1F', 'My', 'SECTION 3'),
    ]

    for tbl_id, sec_marker, comp, stat in EXT_TABLES:
        # Pass 'comp' to the parser so it looks up the right column
        sec = _parse_bld_section(bld_lines, sec_marker, comp)
        f.write(f'# {tbl_id} — {comp}  {stat} (with PLF)\n#\n')
        _col_hdr(f, 'Station')

        pts = []
        if sec and sec['radii']:
            valid = [(r, v, d, b, p)
                     for r, v, d, b, p in zip(
                         sec['radii'], sec['values'],
                         sec['dlcs'],  sec['blades'], sec['plfs'])
                     if r is not None]
            if valid:
                radii_v  = [x[0] for x in valid]
                values_v = [x[1] for x in valid]
                dlcs_v   = [x[2] for x in valid]
                blades_v = [x[3] for x in valid]
                plfs_v   = [x[4] for x in valid]
                pts = interpolate_blade_loads(
                    radii_v, values_v, dlcs_v, blades_v, plfs_v,
                    blade_length, fractions)

        if not pts:
            for fr in fractions:
                lbl = 'Root' if fr == 0.0 else f'{int(round(fr*100))}%'
                _vrow(f, lbl, None)
            _dotdiv(f)
            for fr in fractions:
                lbl = 'Root' if fr == 0.0 else f'{int(round(fr*100))}%'
                _drow(f, lbl, 'N/A')
        else:
            for pt in pts:
                _vrow(f, pt['label'], pt['value'])
            _dotdiv(f)
            for pt in pts:
                plf_s = f"PLF={pt['plf']:.2f}" if pt['plf'] else 'PLF=—'
                bld_s = pt['blade_tag'] or ''
                dlc_s = pt['dlc'] or 'N/A'
                _drow(f, pt['label'], f"{dlc_s}  ({plf_s})  {bld_s}")
        _subdiv(f)
        f.write('#\n')

    # DEL tables — stacked m blocks
    for tbl_id, comp, sec_base in DEL_TABLES:
        f.write(f'# {tbl_id} — {comp}  RFC DEL\n#\n')
        f.write(f"#  {'Station':<{W_SENS}}{'DEL_[kN-m]':>{W_VAL}}\n")
        f.write(f'# {"─" * (W_SENS + W_VAL)}\n')
        for slope_idx, m in enumerate(del_slopes):
            # FIX: Base DEL table index starts at SECTION 3 for both Mx and My
            sec_num  = 3 + slope_idx
            sec_marker = f'SECTION {sec_num}'
            sec = _parse_bld_section(bld_lines, sec_marker, comp)
            f.write(f'# m = {fmt_slope(m)}\n')
            pts = []
            if sec and sec['radii']:
                valid = [(r, v) for r, v in zip(sec['radii'], sec['values'])
                         if r is not None]
                if valid:
                    rv = [x[0] for x in valid]
                    vv = [x[1] for x in valid]
                    pts = interpolate_blade_loads(
                        rv, vv, [None]*len(rv), [None]*len(rv),
                        [None]*len(rv), blade_length, fractions)
            if not pts:
                for fr in fractions:
                    lbl = 'Root' if fr == 0.0 else f'{int(round(fr*100))}%'
                    _vrow(f, lbl, None)
            else:
                for pt in pts:
                    _vrow(f, pt['label'], pt['value'])
        _subdiv(f)
        f.write('#\n')

def _write_hub(f, hub_lines, hub_cfg):
    """Write SECTION 2 — Hub."""
    del_slopes   = hub_cfg.get('del_slopes', [3.0, 6.0])
    sensor_map   = hub_cfg.get('sensor_map', {})
    COMPS        = ['Mx', 'My', 'Mz', 'Fx']

    _banner(f, 'SECTION 2 — HUB LOADS',
            'Frame : Hub/Coned (c suffix)   '
            'Mx = in-plane   My = out-of-plane   Mz = torsion   Fx = thrust',
            'SUM/HubLoads.sum')

    # TABLE 2A — Extreme
    f.write('# TABLE 2A — Extreme AbsMax (with PLF)\n#\n')
    _col_hdr(f, 'Sensor', f"  {'[Bx]  DLC  (PLF)'}")
    ext_rows = _parse_hub_section1(hub_lines)
    # Filter to COMPS only
    ext_map = {r['label'].split('_')[0]: r for r in ext_rows}
    val_rows = []
    dlc_rows = []
    for comp in COMPS:
        # row = next((r for r in ext_rows
        #             if r['label'].upper().startswith(comp + '_')), None)

        row = next((r for r in ext_rows 
                if r['label'].strip().upper().startswith(comp.upper() + '_')), None)
        

        val_rows.append((row['label'] if row else f'{comp}_[kN-m]',
                         row['val'] if row else None))
        plf_s = f"PLF={row['plf']:.2f}" if row and row['plf'] else 'PLF=—'
        dlc_rows.append((row['label'] if row else f'{comp}_[kN-m]',
                         f"{row['dlc_blade']}  ({plf_s})" if row else 'N/A'))
        # print("row is this ", row)
        
        # print("row label ", row['label'], "val ", row['val'])
    for lbl, val in val_rows:
        _vrow(f, lbl, val)
    _dotdiv(f)
    for lbl, dlc in dlc_rows:
        _drow(f, lbl, dlc)
    _subdiv(f)
    f.write('#\n')

    # TABLE 2B — DEL stacked
    f.write('# TABLE 2B — RFC DEL\n#\n')
    f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
    f.write(f'# {"─" * (W_SENS + W_VAL)}\n')
    del_data = _parse_hub_section2(hub_lines, del_slopes)
    for m in del_slopes:
        f.write(f'# m = {fmt_slope(m)}\n')
        m_data = del_data.get(m, {})
        for comp in COMPS:
            # row = next((r for r in _parse_hub_section1(hub_lines)
            #             if r['label'].upper().startswith(comp + '_')), None)
            row = next((r for r in ext_rows 
                if r['label'].strip().upper().startswith(comp.upper() + '_')), None)
            lbl = row['label'] if row else f'{comp}_[kN-m]'
            vals = m_data.get(lbl)
            val  = vals[0] if vals else None
            bld  = vals[1] if vals else None
            _vrow(f, lbl, val)
    _subdiv(f)
    f.write('#\n')


def _write_pitch_bearing(f, ptb_lines, ptb_cfg):
    """Write SECTION 3 — Pitch Bearing."""
    rfc_slopes = ptb_cfg.get('rfc_slopes', [3.3])
    ldd_slopes = ptb_cfg.get('ldd_slopes', [3.3])
    blades     = ptb_cfg.get('blades', {})
    mres_b1    = blades.get('B1', {}).get('Mres', 'Mres_RootBld1_[kN-m]')

    _banner(f, 'SECTION 3 — PITCH BEARING LOADS',
            'Frame : Hub/Coned (c suffix)   Mres = sqrt(Mxc^2 + Myc^2)',
            'SUM/PitchBearing.sum  Table 1A / 1C / 1D')

    # TABLE 3A — Extreme
    f.write('# TABLE 3A — Mres Extreme AbsMax (with PLF)\n#\n')
    _col_hdr(f)
    tbl1a = _parse_ptb_table(ptb_lines, 'TABLE 1A')
    # mres_row = next((r for r in tbl1a if 'Mres' in r['label']), None)
    mres_row = next((r for r in tbl1a if 'MRES' in r['label'].strip().upper()), None)
    lbl  = mres_row['label'] if mres_row else (mres_b1 or 'Mres_[kN-m]')
    val  = _val(mres_row['cols'], 0) if mres_row else None
    plf  = _val(mres_row['cols'], 1) if mres_row else None
    dlc  = _str(mres_row['cols'], 2) if mres_row else None
    bld  = _str(mres_row['cols'], 3) if mres_row else None
    _vrow(f, lbl, val)
    _dotdiv(f)
    plf_s = f"PLF={plf:.2f}" if plf else 'PLF=—'
    bld_s = bld or ''
    _drow(f, lbl, f"{dlc or 'N/A'}  ({plf_s})  {bld_s}")
    _subdiv(f)
    f.write('#\n')

    # TABLE 3B — RFC DEL
    f.write('# TABLE 3B — RFC DEL\n#\n')
    f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
    f.write(f'# {"─" * (W_SENS + W_VAL)}\n')
    tbl1c = _parse_ptb_table(ptb_lines, 'TABLE 1C')
    # mres_rfc = next((r for r in tbl1c if 'Mres' in r['label']), None)
    mres_rfc = next((r for r in tbl1c if 'MRES' in r['label'].strip().upper()), None)
    for m in rfc_slopes:
        f.write(f'# m = {fmt_slope(m)}\n')
        _vrow(f, lbl, _val(mres_rfc['cols'], 0) if mres_rfc else None)
    _subdiv(f)
    f.write('#\n')

    # TABLE 3C — LDD DEL
    f.write('# TABLE 3C — LDD DEL\n#\n')
    f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
    f.write(f'# {"─" * (W_SENS + W_VAL)}\n')
    tbl1d = _parse_ptb_table(ptb_lines, 'TABLE 1D')
    # mres_ldd = next((r for r in tbl1d if 'Mres' in r['label']), None)
    mres_ldd = next((r for r in tbl1d if 'MRES' in r['label'].strip().upper()), None)
    for m in ldd_slopes:
        f.write(f'# m = {fmt_slope(m)}\n')
        _vrow(f, lbl, _val(mres_ldd['cols'], 0) if mres_ldd else None)
    _subdiv(f)
    f.write('#\n')


def _write_pitch_drive(f, ptb_lines, ptb_cfg):
    """Write SECTION 4 — Pitch Drive (Mz from Table 2)."""
    rfc_slopes = ptb_cfg.get('rfc_slopes', [3.3])
    ldd_slopes = ptb_cfg.get('ldd_slopes', [3.3])
    blades     = ptb_cfg.get('blades', {})
    mz_b1      = blades.get('B1', {}).get('Mz', 'RootMzc1_[kN-m]')

    _banner(f, 'SECTION 4 — PITCH DRIVE LOADS',
            'Sensor : Mz (pitch torque) — worst of 3 blades',
            'SUM/PitchBearing.sum  Table 2')

    tbl2 = _parse_ptb_table(ptb_lines, 'TABLE 2')
    # Table 2 sub-sections: extreme with PLF, extreme without PLF, RFC DEL, LDD DEL
    # Find Mz row in extreme block
    mz_rows = [r for r in tbl2 if r['label'].upper().startswith('MZ')]

    def _write_ext_block(title, row_idx, with_plf):
        f.write(f'# TABLE 4{"A" if with_plf else "B"} — '
                f'Extreme AbsMax ({"with" if with_plf else "without"} PLF)\n#\n')
        _col_hdr(f)
        row = mz_rows[row_idx] if row_idx < len(mz_rows) else None
        lbl = row['label'] if row else (mz_b1 or 'Mz_[kN-m]')
        val = _val(row['cols'], 0) if row else None
        plf = _val(row['cols'], 1) if row else None
        dlc = _str(row['cols'], 2) if row else None
        bld = _str(row['cols'], 3) if row else None
        _vrow(f, lbl, val)
        _dotdiv(f)
        plf_v = plf if plf else (1.35 if with_plf else 1.00)
        _drow(f, lbl,
              f"{dlc or 'N/A'}  (PLF={plf_v:.2f})  {bld or ''}")
        _subdiv(f)
        f.write('#\n')
        return lbl

    lbl = _write_ext_block('with PLF',    0, True)
    _write_ext_block('without PLF', 1, False)

    # # RFC DEL
    # f.write('# TABLE 4C — RFC DEL\n#\n')
    # f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
    # f.write(f'# {"─" * (W_SENS + W_VAL)}\n')
    # rfc_rows = [r for r in tbl2 if any('DEL' in c.upper() for c in r['cols'])
    #             and 'RFC' in ' '.join(r['cols']).upper()]
    # for m in rfc_slopes:
    #     f.write(f'# m = {fmt_slope(m)}\n')
    #     row = rfc_rows[0] if rfc_rows else None
    #     _vrow(f, lbl, _val(row['cols'], 0) if row else None)
    # _subdiv(f)
    # f.write('#\n')

    f.write('# TABLE 4C — RFC DEL\n#\n')
    f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
    f.write(f'# {"─" * (W_SENS + W_VAL)}\n')

    # FIX: Use index 1 from tbl2 (the RFC row)
    rfc_row = tbl2[1] if len(tbl2) > 1 else None

    for m in rfc_slopes:
        f.write(f'# m = {fmt_slope(m)}\n')
        # Use the label from the extreme row (lbl) and the value from the rfc row
        _vrow(f, lbl, _val(rfc_row['cols'], 0) if rfc_row else None)
    _subdiv(f)
    f.write('#\n')

    # LDD DEL
    # f.write('# TABLE 4D — LDD DEL\n#\n')
    # f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
    # f.write(f'# {"─" * (W_SENS + W_VAL)}\n')
    # ldd_rows = [r for r in tbl2 if any('DEL' in c.upper() for c in r['cols'])
    #             and 'LDD' in ' '.join(r['cols']).upper()]
    # for m in ldd_slopes:
    #     f.write(f'# m = {fmt_slope(m)}\n')
    #     row = ldd_rows[0] if ldd_rows else None
    #     _vrow(f, lbl, _val(row['cols'], 0) if row else None)
    # _subdiv(f)
    # f.write('#\n')

    f.write('# TABLE 4D — LDD DEL\n#\n')
    f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
    f.write(f'# {"─" * (W_SENS + W_VAL)}\n')

    # FIX: Use index 2 from tbl2 (the LDD row)
    ldd_row = tbl2[2] if len(tbl2) > 2 else None

    for m in ldd_slopes:
        f.write(f'# m = {fmt_slope(m)}\n')
        # Use the label from the extreme row (lbl) and the value from the ldd row
        _vrow(f, lbl, _val(ldd_row['cols'], 0) if ldd_row else None)
    _subdiv(f)
    f.write('#\n')


def _write_drivetrain(f, drt_lines, drt_cfg):
    """Write SECTION 5 — Drivetrain."""
    del_slopes = drt_cfg.get('del_slopes', [4.0, 8.0])
    ldd_slopes = drt_cfg.get('ldd_slopes', [3.3, 6.7])
    lrd_slopes = drt_cfg.get('lrd_slopes', [3.3, 6.7])
    a_frame    = drt_cfg.get('a_frame', {})
    s_frame    = drt_cfg.get('s_frame', {})
    hss        = drt_cfg.get('hss', {})
    hss_slopes = drt_cfg.get('hss_del_slopes', [4.0, 8.0])
    ldd_sensors= drt_cfg.get('ldd_lrd_sensors', [])
    COMPS      = ['Mres', 'Mx', 'My', 'Mz', 'Fx', 'Fy', 'Fz']
    # My and Mz for LDD/LRD
    LDD_COMPS  = ['My', 'Mz']

    _banner(f, 'SECTION 5 — DRIVETRAIN LOADS',
            'LSS Non-rotating (a suffix) + Rotating (s suffix) + HSS',
            'SUM/DRTLoads.sum  Tables 1A / 1B / 1C')

    tbl1a = _parse_drt_table(drt_lines, 'TABLE 1A')
    tbl1b = _parse_drt_table(drt_lines, 'TABLE 1B')
    tbl1c = _parse_drt_table(drt_lines, 'TABLE 1C')
    tbl3  = _parse_drt_table(drt_lines, 'TABLE 3')

    def _frame_rows(rows, frame_marker):
        """Get rows belonging to a frame block."""
        collecting = False
        result = []
        for r in rows:
            lbl = r['label']
            if 'Non-rotating' in lbl or 'a suffix' in lbl:
                collecting = ('a' in frame_marker)
                continue
            if 'Rotating' in lbl or 's suffix' in lbl:
                collecting = ('s' in frame_marker)
                continue
            if collecting:
                result.append(r)
        return result

    def _write_frame_block(rows, frame_key, frame_name):
        f.write(f'# ── {frame_name} {"─"*max(0,60-len(frame_name))}\n')
        for r in rows:
            _vrow(f, r['label'], _val(r['cols'], 0))

    def _write_frame_dlc(rows, frame_name):
        f.write(f'# ── {frame_name} {"─"*max(0,60-len(frame_name))}\n')
        for r in rows:
            plf  = _val(r['cols'], 1)
            dlc  = _str(r['cols'], 2)
            plf_s = f"PLF={plf:.2f}" if plf else 'PLF=—'
            _drow(f, r['label'], f"{dlc or 'N/A'}  ({plf_s})")

    # TABLE 5A — Extreme
    f.write('# TABLE 5A — Extreme AbsMax (with PLF)\n#\n')
    _col_hdr(f)
    a_vals = _frame_rows(tbl1a, 'a') or \
             [{'label': f'{c}_a', 'cols': []} for c in COMPS]
    s_vals = _frame_rows(tbl1a, 's') or \
             [{'label': f'{c}_s', 'cols': []} for c in COMPS]
    _write_frame_block(a_vals, 'a', 'Non-rotating frame (a suffix)')
    _write_frame_block(s_vals, 's', 'Rotating frame (s suffix)')
    _dotdiv(f)
    _write_frame_dlc(a_vals, 'Non-rotating frame (a suffix)')
    _write_frame_dlc(s_vals, 'Rotating frame (s suffix)')
    _subdiv(f)
    f.write('#\n')

    # TABLE 5B — RFC DEL stacked
    f.write('# TABLE 5B — RFC DEL\n#\n')
    f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
    f.write(f'# {"─" * (W_SENS + W_VAL)}\n')
    a_del = _frame_rows(tbl1b, 'a') or [{'label': f'{c}_a', 'cols': []} for c in COMPS]
    s_del = _frame_rows(tbl1b, 's') or [{'label': f'{c}_s', 'cols': []} for c in COMPS]
    for i, m in enumerate(del_slopes):
        f.write(f'# ── Non-rotating frame (a suffix)  m = {fmt_slope(m)} ─────────────────────\n')
        for r in a_del:
            _vrow(f, r['label'], _val(r['cols'], i))
        f.write(f'# ── Rotating frame (s suffix)  m = {fmt_slope(m)} ──────────────────────────\n')
        for r in s_del:
            _vrow(f, r['label'], _val(r['cols'], i))
    _subdiv(f)
    f.write('#\n')

    # TABLE 5C — LDD/LRD DEL — My and Mz only
    f.write('# TABLE 5C — LDD / LRD DEL  (My and Mz only)\n#\n')
    f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
    f.write(f'# {"─" * (W_SENS + W_VAL)}\n')
    # Filter tbl1c rows to My/Mz sensors
    # ldd_rows_a = [r for r in tbl1c if any(
    #     f.endswith('a') and comp in r['label'].upper()
    #     for comp in LDD_COMPS) or
    #     any(r['label'].upper().startswith('LSSHFTMY') or
    #         r['label'].upper().startswith('LSSHFTMZ')
    #         for _ in [1]) if 'a_[' in r['label'].lower()]
    # Simpler: filter by My_a and Mz_a / My_s and Mz_s pattern
    ldd_my_mz = [r for r in tbl1c if
                 any(p in r['label'] for p in
                     ['Mya_', 'Mza_', 'Mys_', 'Mzs_',
                      'LSShftMya', 'LSShftMza', 'LSShftMys', 'LSShftMzs'])]
    # a_ldd = [r for r in ldd_my_mz if 'a_[' in r['label'] or r['label'].endswith('a')]
    # s_ldd = [r for r in ldd_my_mz if 's_[' in r['label'] or r['label'].endswith('s')]

    a_ldd = [r for r in ldd_my_mz if 'a_[' in r['label'].lower() or r['label'].lower().split('_')[0].endswith('a')]
    s_ldd = [r for r in ldd_my_mz if 's_[' in r['label'].lower() or r['label'].lower().split('_')[0].endswith('s')]

    for ldd_i, ldd_m in enumerate(ldd_slopes):
        f.write(f'# ── Non-rotating (a)  LDD m = {fmt_slope(ldd_m)} ──────────────────────────────\n')
        for r in a_ldd:
            _vrow(f, r['label'], _val(r['cols'], ldd_i))
        f.write(f'# ── Rotating (s)  LDD m = {fmt_slope(ldd_m)} ─────────────────────────────────\n')
        for r in s_ldd:
            _vrow(f, r['label'], _val(r['cols'], ldd_i))
    for lrd_i, lrd_m in enumerate(lrd_slopes):
        n_ldd = len(ldd_slopes)
        f.write(f'# ── Non-rotating (a)  LRD m = {fmt_slope(lrd_m)} ──────────────────────────────\n')
        for r in a_ldd:
            _vrow(f, r['label'], _val(r['cols'], n_ldd + lrd_i))
        f.write(f'# ── Rotating (s)  LRD m = {fmt_slope(lrd_m)} ─────────────────────────────────\n')
        for r in s_ldd:
            _vrow(f, r['label'], _val(r['cols'], n_ldd + lrd_i))
    _subdiv(f)
    f.write('#\n')

    # TABLE 5D — HSS (from Table 3)
    if tbl3:
        f.write('# TABLE 5D — High-Speed Shaft\n#\n')
        _col_hdr(f)
        hss_tq = hss.get('Tq') or 'HSShftTq_[kN-m]'
        hss_pw = hss.get('Pwr') or 'HSShftPwr_[kW]'
        hss_v  = hss.get('V')   or 'HSShftV_[rpm]'
        # Find rows by label
        def _hss_row(label):
            return next((r for r in tbl3 if label in r['label']), None)
        tq_row = _hss_row('Tq') or _hss_row('HSShft')
        pw_row = _hss_row('Pwr')
        v_row  = _hss_row('rpm') or _hss_row('HSShftV')
        # Extreme with PLF — Tq
        f.write(f'# ── Extreme with PLF ───────────────────────────────────────────────────\n')
        _vrow(f, hss_tq, _val(tq_row['cols'], 0) if tq_row else None)
        # Extreme without PLF — Tq, Pwr, V
        _dotdiv(f)
        f.write(f'# ── Extreme without PLF ────────────────────────────────────────────────\n')
        for lbl, row in [(hss_tq, tq_row), (hss_pw, pw_row), (hss_v, v_row)]:
            _vrow(f, lbl, _val(row['cols'], 0) if row else None)
        # RFC DEL — Tq
        _dotdiv(f)
        f.write(f'# ── RFC DEL ────────────────────────────────────────────────────────────\n')
        f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
        for i, m in enumerate(hss_slopes):
            f.write(f'# m = {fmt_slope(m)}\n')
            _vrow(f, hss_tq, _val(tq_row['cols'], i) if tq_row else None)
        _subdiv(f)
        f.write('#\n')

# def _write_drivetrain(f, drt_lines, drt_cfg):
#     """Write SECTION 5 — Drivetrain."""
#     del_slopes = drt_cfg.get('del_slopes', [4.0, 8.0])
#     ldd_slopes = drt_cfg.get('ldd_slopes', [3.3, 6.7])
#     lrd_slopes = drt_cfg.get('lrd_slopes', [3.3, 6.7])
#     a_frame    = drt_cfg.get('a_frame', {})
#     s_frame    = drt_cfg.get('s_frame', {})
#     hss        = drt_cfg.get('hss', {})
#     hss_slopes = drt_cfg.get('hss_del_slopes', [4.0, 8.0])
#     COMPS      = ['Mres', 'Mx', 'My', 'Mz', 'Fx', 'Fy', 'Fz']
#     LDD_COMPS  = ['My', 'Mz']

#     _banner(f, 'SECTION 5 — DRIVETRAIN LOADS',
#             'LSS Non-rotating (a suffix) + Rotating (s suffix) + HSS',
#             'SUM/DRTLoads.sum  Tables 1A / 1B / 1C')

#     # Use the robust parser we fixed earlier
#     tbl1a = _parse_drt_table(drt_lines, 'TABLE 1A')
#     tbl1b = _parse_drt_table(drt_lines, 'TABLE 1B')
#     tbl1c = _parse_drt_table(drt_lines, 'TABLE 1C')
#     tbl3  = _parse_drt_table(drt_lines, 'TABLE 3')

#     def _frame_rows(rows, frame_marker):
#         """Get rows belonging to a frame block using robust label checking."""
#         collecting = False
#         result = []
#         for r in rows:
#             lbl_up = r['label'].strip().upper()
#             if 'NON-ROTATING' in lbl_up or 'A SUFFIX' in lbl_up:
#                 collecting = (frame_marker.lower() == 'a')
#                 continue
#             if 'ROTATING' in lbl_up or 'S SUFFIX' in lbl_up:
#                 collecting = (frame_marker.lower() == 's')
#                 continue
#             if collecting:
#                 result.append(r)
#         return result

#     def _write_frame_block(rows, frame_name):
#         f.write(f'# ── {frame_name} {"─"*max(0,60-len(frame_name))}\n')
#         for r in rows:
#             _vrow(f, r['label'], _val(r['cols'], 0))

#     def _write_frame_dlc(rows, frame_name):
#         f.write(f'# ── {frame_name} {"─"*max(0,60-len(frame_name))}\n')
#         for r in rows:
#             plf   = _val(r['cols'], 1)
#             dlc   = _str(r['cols'], 2)
#             plf_s = f"PLF={plf:.2f}" if plf else 'PLF=—'
#             _drow(f, r['label'], f"{dlc or 'N/A'}  ({plf_s})")

#     # --- TABLE 5A — Extreme ---
#     f.write('# TABLE 5A — Extreme AbsMax (with PLF)\n#\n')
#     _col_hdr(f)
#     a_vals = _frame_rows(tbl1a, 'a')
#     s_vals = _frame_rows(tbl1a, 's')
    
#     _write_frame_block(a_vals, 'Non-rotating frame (a suffix)')
#     _write_frame_block(s_vals, 'Rotating frame (s suffix)')
#     _dotdiv(f)
#     _write_frame_dlc(a_vals, 'Non-rotating frame (a suffix)')
#     _write_frame_dlc(s_vals, 'Rotating frame (s suffix)')
#     _subdiv(f)
#     f.write('#\n')

#     # --- TABLE 5B — RFC DEL ---
#     f.write('# TABLE 5B — RFC DEL\n#\n')
#     f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
#     f.write(f'# {"─" * (W_SENS + W_VAL)}\n')
#     a_del = _frame_rows(tbl1b, 'a')
#     s_del = _frame_rows(tbl1b, 's')
    
#     for i, m in enumerate(del_slopes):
#         f.write(f'# ── Non-rotating frame (a)  m = {fmt_slope(m)} ─────────────────────\n')
#         for r in a_del: _vrow(f, r['label'], _val(r['cols'], i))
#         f.write(f'# ── Rotating frame (s)  m = {fmt_slope(m)} ──────────────────────────\n')
#         for r in s_del: _vrow(f, r['label'], _val(r['cols'], i))
#     _subdiv(f)
#     f.write('#\n')

#     # --- TABLE 5C — LDD/LRD DEL (My and Mz only) ---
#     f.write('# TABLE 5C — LDD / LRD DEL  (My and Mz only)\n#\n')
#     f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
#     f.write(f'# {"─" * (W_SENS + W_VAL)}\n')
    
#     # FIXED: Replaced the broken 'f.endswith' logic with clean pattern matching
#     ldd_my_mz = [r for r in tbl1c if any(p.upper() in r['label'].upper() for p in 
#                  ['MYA_', 'MZA_', 'MYS_', 'MZS_', 'LSSHFTMY', 'LSSHFTMZ'])]
    
#     a_ldd = [r for r in ldd_my_mz if 'a' in r['label'].lower().split('_')[0][-1:] or '_a' in r['label'].lower()]
#     s_ldd = [r for r in ldd_my_mz if 's' in r['label'].lower().split('_')[0][-1:] or '_s' in r['label'].lower()]

#     for ldd_i, ldd_m in enumerate(ldd_slopes):
#         f.write(f'# ── Non-rotating (a)  LDD m = {fmt_slope(ldd_m)} ──────────────────────────────\n')
#         for r in a_ldd: _vrow(f, r['label'], _val(r['cols'], ldd_i))
#         f.write(f'# ── Rotating (s)  LDD m = {fmt_slope(ldd_m)} ─────────────────────────────────\n')
#         for r in s_ldd: _vrow(f, r['label'], _val(r['cols'], ldd_i))
        
#     for lrd_i, lrd_m in enumerate(lrd_slopes):
#         n_ldd = len(ldd_slopes)
#         f.write(f'# ── Non-rotating (a)  LRD m = {fmt_slope(lrd_m)} ──────────────────────────────\n')
#         for r in a_ldd: _vrow(f, r['label'], _val(r['cols'], n_ldd + lrd_i))
#         f.write(f'# ── Rotating (s)  LRD m = {fmt_slope(lrd_m)} ─────────────────────────────────\n')
#         for r in s_ldd: _vrow(f, r['label'], _val(r['cols'], n_ldd + lrd_i))
#     _subdiv(f)
#     f.write('#\n')

#     # --- TABLE 5D — HSS ---
#     if tbl3:
#         f.write('# TABLE 5D — High-Speed Shaft\n#\n')
#         _col_hdr(f)
#         hss_tq = hss.get('Tq') or 'HSShftTq_[kN-m]'
#         hss_pw = hss.get('Pwr') or 'HSShftPwr_[kW]'
#         hss_v  = hss.get('V')   or 'HSShftV_[rpm]'

#         # FIXED: Robust HSS row finding using upper() and strip()
#         def _hss_row(marker):
#             return next((r for r in tbl3 if marker.upper() in r['label'].strip().upper()), None)

#         tq_row = _hss_row('TQ') or _hss_row('HSSHFT')
#         pw_row = _hss_row('PWR')
#         v_row  = _hss_row('RPM') or _hss_row('HSSHFTV')

#         f.write(f'# ── Extreme with PLF ───────────────────────────────────────────────────\n')
#         _vrow(f, hss_tq, _val(tq_row['cols'], 0) if tq_row else None)
#         _dotdiv(f)
#         f.write(f'# ── Extreme without PLF ────────────────────────────────────────────────\n')
#         for lbl, row in [(hss_tq, tq_row), (hss_pw, pw_row), (hss_v, v_row)]:
#             _vrow(f, lbl, _val(row['cols'], 0) if row else None)
#         _dotdiv(f)
#         f.write(f'# ── RFC DEL ────────────────────────────────────────────────────────────\n')
#         f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
#         for i, m in enumerate(hss_slopes):
#             f.write(f'# m = {fmt_slope(m)}\n')
#             _vrow(f, hss_tq, _val(tq_row['cols'], i) if tq_row else None)
#         _subdiv(f)
#         f.write('#\n')

def _write_yaw(f, yaw_lines, yaw_cfg):
    """Write SECTION 6 — Yaw."""
    del_slopes = yaw_cfg.get('del_slopes', [3.0])

    _banner(f, 'SECTION 6 — YAW BEARING LOADS',
            'Frame : Nacelle (p suffix)   Mx=rolling  My=pitching  Mz=yaw torque',
            'SUM/YawLoads.sum')

    s1 = _parse_yaw_section(yaw_lines, 'SECTION 1')

    # TABLE 6A — Extreme
    f.write('# TABLE 6A — Extreme AbsMax (with PLF)\n#\n')
    _col_hdr(f)

    def _write_yaw_block(rows, with_dlc=True):
        for row in rows:
            _vrow(f, row['label'], row.get('val'))
        if with_dlc:
            _dotdiv(f)
            for row in rows:
                plf_s = f"PLF={row['plf']:.2f}" if row.get('plf') else 'PLF=—'
                dlc_s = row.get('dlc') or 'N/A'
                _drow(f, row['label'], f"{dlc_s}  ({plf_s})")

    _write_yaw_block(s1['loads'])
    if s1['trans']:
        _dotdiv(f)
        _write_yaw_block(s1['trans'])
    if s1['ang']:
        _dotdiv(f)
        _write_yaw_block(s1['ang'])
    _subdiv(f)
    f.write('#\n')

    # TABLE 6B — RFC DEL
    f.write('# TABLE 6B — RFC DEL\n#\n')
    f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
    f.write(f'# {"─" * (W_SENS + W_VAL)}\n')
    for m in del_slopes:
        sec_marker = f'SECTION 2'
        s2 = _parse_yaw_section(yaw_lines, sec_marker)
        f.write(f'# m = {fmt_slope(m)}\n')
        for row in s2['loads']:
            _vrow(f, row['label'], row.get('val'))
        if s2['trans']:
            _dotdiv(f)
            for row in s2['trans']:
                _vrow(f, row['label'], row.get('val'))
        if s2['ang']:
            _dotdiv(f)
            for row in s2['ang']:
                _vrow(f, row['label'], row.get('val'))
    _subdiv(f)
    f.write('#\n')


def _write_tower(f, twr_lines, twr_cfg):
    """Write SECTION 7 — Tower (Mres only, all 11 stations)."""
    del_slopes = twr_cfg.get('del_slopes', [4.0])
    stations   = twr_cfg.get('stations', [])

    _banner(f, 'SECTION 7 — TOWER LOADS',
            'Frame : Tower fixed (t suffix)   Mres = sqrt(Mx^2 + My^2)   All 11 stations',
            'SUM/TwrLoads.sum')

    from tower_reader import format_station_label_twr

    # TABLE 7A — Extreme Mres
    f.write('# TABLE 7A — Mres Extreme AbsMax (with PLF)\n#\n')
    f.write(f"#  {'Station':<{W_SENS}}{'Value_[kN-m]':>{W_VAL}}\n")
    f.write(f'# {"─" * (W_SENS + W_VAL)}\n')

    # Parse Section 1 — Mres is first component (col index 1 after station label)
    twr_s1 = _parse_twr_section(twr_lines, 'SECTION 1', component_idx=0)

    val_rows = []
    dlc_rows = []
    for st in stations:
        lbl   = st['label']
        ht    = st.get('height_m')
        slbl  = format_station_label_twr(lbl, ht)
        # Find matching row in parsed data
        row = next((r for r in twr_s1 if lbl in r['label']), None)
        val_rows.append((slbl, row['val'] if row else None))
        dlc_rows.append((slbl, row['dlc'] if row else None))

    for slbl, val in val_rows:
        _vrow(f, slbl, val)
    _dotdiv(f)
    for slbl, dlc in dlc_rows:
        plf_s = 'PLF=1.35'  # standard tower PLF
        _drow(f, slbl, f"{dlc or 'N/A'}  ({plf_s})")
    _subdiv(f)
    f.write('#\n')

    # TABLE 7B — Mres RFC DEL
    f.write('# TABLE 7B — Mres RFC DEL\n#\n')
    f.write(f"#  {'Station':<{W_SENS}}{'DEL_[kN-m]':>{W_VAL}}\n")
    f.write(f'# {"─" * (W_SENS + W_VAL)}\n')
    for m in del_slopes:
        f.write(f'# m = {fmt_slope(m)}\n')
        twr_del = _parse_twr_section(twr_lines, f'SECTION 2', component_idx=0)
        for st in stations:
            lbl  = st['label']
            ht   = st.get('height_m')
            slbl = format_station_label_twr(lbl, ht)
            row  = next((r for r in twr_del if lbl in r['label']), None)
            _vrow(f, slbl, row['val'] if row else None)
    _subdiv(f)
    f.write('#\n')


def _write_foundation(f, fnd_lines, fnd_cfg):
    """Write SECTION 8 — Foundation."""
    rfc_slopes = fnd_cfg.get('rfc_slopes', [3.0, 5.0])
    ldd_slopes = fnd_cfg.get('ldd_slopes', [3.0, 5.0])
    COMPS_EXT  = ['Mres', 'Mx', 'My', 'Mz', 'Fx']
    COMPS_FAT  = ['Mx', 'My', 'Mz', 'Fx']

    _banner(f, 'SECTION 8 — FOUNDATION LOADS',
            'Frame : Tower fixed (t suffix)   Mx=side-side  My=fore-aft  Mz=torsion  Fx=shear',
            'SUM/FNDLoads.sum')

    tbl1a = _parse_fnd_table(fnd_lines, 'TABLE 1A')
    tbl1b = _parse_fnd_table(fnd_lines, 'TABLE 1B')
    tbl2a = _parse_fnd_table(fnd_lines, 'TABLE 2A')
    tbl2b = _parse_fnd_table(fnd_lines, 'TABLE 2B')

    def _find_fnd_row(tbl_data, comp):
        """Finds row by component, handling TwrBs and unit suffixes."""
        for lbl, cols in tbl_data.items():
            u_lbl = lbl.upper()
            u_comp = comp.upper()
            # Matches "Mx_" or "TwrBsMxt"
            if u_comp + '_' in u_lbl or (u_comp + 'T') in u_lbl:
                return lbl, cols
        return f'{comp}_[kN-m]', None

    def _write_fnd_ext(tbl_id, tbl_data, with_plf):
        f.write(f'# TABLE {tbl_id} — Extreme AbsMax '
                f'({"with" if with_plf else "without"} PLF)\n#\n')
        _col_hdr(f)
        val_rows = []; dlc_rows = []
        for comp in COMPS_EXT:
            lbl, cols = _find_fnd_row(tbl_data, comp)
            val = _val(cols, 0)
            plf = _val(cols, 1) if cols else (1.35 if with_plf else 1.00)
            dlc = _str(cols, 2) if cols else 'N/A'
            val_rows.append((lbl, val))
            dlc_rows.append((lbl, f"{dlc}  (PLF={plf:.2f})"))
        for lbl, val in val_rows:
            _vrow(f, lbl, val)
        _dotdiv(f)
        for lbl, dlc in dlc_rows:
            _drow(f, lbl, dlc)
        _subdiv(f)
        f.write('#\n')

    _write_fnd_ext('8A', tbl1a, True)
    _write_fnd_ext('8B', tbl1b, False)

    # def _write_fnd_del(tbl_id, tbl_data, slopes, method):
    #     f.write(f'# TABLE {tbl_id} — {method} DEL\n#\n')
    #     f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
    #     f.write(f'# {"─" * (W_SENS + W_VAL)}\n')
    #     for m in slopes:
    #         f.write(f'# m = {fmt_slope(m)}\n')
    #         for comp in COMPS_FAT:
    #             lbl = next((k for k in tbl_data
    #                         if k.upper().startswith(comp + '_')),
    #                        f'{comp}_[kN-m]')
    #             row = tbl_data.get(lbl)
    #             _vrow(f, lbl, row['val'] if row else None)
    #     _subdiv(f)
    #     f.write('#\n')

    # _write_fnd_del('8C', tbl2a, rfc_slopes, 'RFC')
    # _write_fnd_del('8D', tbl2b, ldd_slopes, 'LDD')

    def _write_fnd_del(tbl_id, tbl_data, slopes, method):
        f.write(f'# TABLE {tbl_id} — {method} DEL\n#\n')
        f.write(f"#  {'Sensor':<{W_SENS}}{'DEL':>{W_VAL}}\n")
        f.write(f'# {"─" * (W_SENS + W_VAL)}\n')
        
        for i, m in enumerate(slopes):
            f.write(f'# m = {fmt_slope(m)}\n')
            for comp in COMPS_FAT:
                # Flexible search to find labels like 'TwrBsMxt'
                lbl, cols = next(((k, v) for k, v in tbl_data.items() 
                                 if comp.upper() + '_' in k.upper() or 
                                 (comp.upper() + 'T') in k.upper()), 
                                (f'{comp}_[kN-m]', None))
                
                # Pick the column based on the slope index 'i'
                _vrow(f, lbl, _val(cols, i) if cols else None)
        _subdiv(f)
        f.write('#\n')

    _write_fnd_del('8C', tbl2a, rfc_slopes, 'RFC')
    _write_fnd_del('8D', tbl2b, ldd_slopes, 'LDD')

def _write_clearance(f, ext_folder, clr_cfg):
    """Write SECTION 9 — Tower Clearance from EXT files directly."""
    clrnc = clr_cfg.get('clrnc', {})
    oop   = clr_cfg.get('oop', {})

    _banner(f, 'SECTION 9 — TOWER CLEARANCE',
            'No PLF applied — geometric safety check',
            'EXT/<sensor>.min (TwrClrnc) and EXT/<sensor>.max (OoPDefl)')

    def _clr_rows(sensors_dict, ext_type, label_prefix):
        """Read values and DLCs, return (val_rows, dlc_rows, worst)."""
        val_rows = []; dlc_rows = []; best_val = None; best_b = None
        for b_key in ['B1', 'B2', 'B3']:
            sensor = sensors_dict.get(b_key)
            b_lbl  = f'[{b_key}]'
            if sensor and ext_folder:
                val, plf, dlc = read_ext_design_load(
                    ext_folder, sensor, ext_type,
                    with_plf=False)
            else:
                val = plf = dlc = None
            val_rows.append((sensor or f'{label_prefix}_{b_key}_[m]',
                              b_lbl, val))
            dlc_rows.append((sensor or f'{label_prefix}_{b_key}_[m]',
                              b_lbl, dlc or 'N/A'))
            # Track worst
            if val is not None:
                if ext_type == 'min':
                    if best_val is None or val < best_val:
                        best_val, best_b, best_dlc = val, b_key, dlc
                else:
                    if best_val is None or val > best_val:
                        best_val, best_b, best_dlc = val, b_key, dlc
        return val_rows, dlc_rows, best_val, best_b, \
               (best_dlc if best_val is not None else None)

    f.write(f"#  {'Sensor':<{W_SENS}}{'[Bx]':<8}{'Value_[m]':>{W_VAL - 8}}\n")
    f.write(f'# {"─" * (W_SENS + W_VAL)}\n')

    # Direct clearance — Min
    f.write('# ── Direct clearance (TwrClrnc) — Min value ─────────────────────────────\n')
    vr, dr, bv, bb, bd = _clr_rows(clrnc, 'min', 'TwrClrnc')
    for lbl, b_lbl, val in vr:
        val_s = f"{'N/A':>{W_VAL-8}}" if val is None else f"{val:>{W_VAL-8}.4f}"
        f.write(f"   {lbl:<{W_SENS}}{b_lbl:<8}{val_s}\n")
    if bv is not None:
        f.write(f"   {'Worst of 3':<{W_SENS}}{'[' + bb + ']':<8}{bv:>{W_VAL-8}.4f}\n")
    _dotdiv(f)
    for lbl, b_lbl, dlc in dr:
        f.write(f"   {lbl:<{W_SENS}}{b_lbl:<8}{dlc}\n")
    if bb:
        f.write(f"   {'Worst of 3':<{W_SENS}}{'[' + bb + ']':<8}{bd or 'N/A'}\n")

    _subdiv(f)
    # OoP deflection — Max
    f.write('# ── Out-of-plane tip deflection (OoPDefl) — Max value ───────────────────\n')
    vr, dr, bv, bb, bd = _clr_rows(oop, 'max', 'OoPDefl')
    for lbl, b_lbl, val in vr:
        val_s = f"{'N/A':>{W_VAL-8}}" if val is None else f"{val:>{W_VAL-8}.4f}"
        f.write(f"   {lbl:<{W_SENS}}{b_lbl:<8}{val_s}\n")
    if bv is not None:
        f.write(f"   {'Worst of 3':<{W_SENS}}{'[' + bb + ']':<8}{bv:>{W_VAL-8}.4f}\n")
    _dotdiv(f)
    for lbl, b_lbl, dlc in dr:
        f.write(f"   {lbl:<{W_SENS}}{b_lbl:<8}{dlc}\n")
    if bb:
        f.write(f"   {'Worst of 3':<{W_SENS}}{'[' + bb + ']':<8}{bd or 'N/A'}\n")
    _subdiv(f)


# =============================================================================
# Main entry point
# =============================================================================

def write_main_loads_sum(output_path, sum_folder, ext_folder,
                          lifetime_years, neq_lifetime, log=None):
    """
    Write MainLoads.sum — master turbine load summary.

    All slopes and sensor names read from config at runtime.
    Sections 1–8 read from SUM folder files.
    Section 9 reads from EXT folder directly.

    Parameters
    ----------
    output_path   : str
    sum_folder    : str
    ext_folder    : str
    lifetime_years: float
    neq_lifetime  : float
    log           : PostProcessLogger or None
    """
    # Load all configs — no hardcoding
    bld_cfg = get_blade_config()
    hub_cfg = get_hub_config()
    ptb_cfg = get_pitch_bearing_config()
    drt_cfg = get_drivetrain_config()
    yaw_cfg = get_yaw_config()
    twr_cfg = get_tower_config()
    fnd_cfg = get_foundation_config()
    clr_cfg = get_tower_clearance_config()

    # Read all SUM files
    bld_lines = _read_lines(sum_folder, 'BldLoads.sum',     log)
    hub_lines = _read_lines(sum_folder, 'HubLoads.sum',     log)
    ptb_lines = _read_lines(sum_folder, 'PitchBearing.sum', log)
    drt_lines = _read_lines(sum_folder, 'DRTLoads.sum',     log)
    yaw_lines = _read_lines(sum_folder, 'YawLoads.sum',     log)
    twr_lines = _read_lines(sum_folder, 'TwrLoads.sum',     log)
    fnd_lines = _read_lines(sum_folder, 'FNDLoads.sum',     log)

    with open(output_path, 'w', encoding='utf-8') as f:
        # File header
        f.write(f'# {"=" * 76}\n')
        f.write(f'# MainLoads.sum — Master Turbine Load Summary\n')
        f.write(f'# {"=" * 76}\n')
        f.write(f'# Lifetime  : {lifetime_years:.0f} years\n')
        f.write(f'# Neq (RFC) : {neq_lifetime:.3g}\n')
        f.write(f'# Generated : from SUM folder component .sum files\n')
        f.write(f'# Sections  : 1=Blade  2=Hub  3=PitchBearing  4=PitchDrive\n')
        f.write(f'#             5=Drivetrain  6=Yaw  7=Tower  8=Foundation\n')
        f.write(f'#             9=TowerClearance\n')
        f.write(f'# Note      : All slopes read from sensorList.txt — nothing hardcoded\n')
        f.write(f'# {"=" * 76}\n')

        _write_blade(      f, bld_lines, bld_cfg)
        _write_hub(        f, hub_lines, hub_cfg)
        _write_pitch_bearing(f, ptb_lines, ptb_cfg)
        _write_pitch_drive(f, ptb_lines, ptb_cfg)
        _write_drivetrain( f, drt_lines, drt_cfg)
        _write_yaw(        f, yaw_lines, yaw_cfg)
        _write_tower(      f, twr_lines, twr_cfg)
        _write_foundation( f, fnd_lines, fnd_cfg)
        _write_clearance(  f, ext_folder, clr_cfg)

        f.write(f'\n\n# {"=" * 76}\n')
        f.write('# END OF FILE\n')
        f.write(f'# {"=" * 76}\n')

    if log:
        log.file_written('SUM/MainLoads.sum', tag='SUM')
