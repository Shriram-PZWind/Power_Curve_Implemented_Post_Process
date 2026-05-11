# =============================================================================
# blade_reader.py
# OpenFAST Postprocessing — Blade Sensor Auto-Detection & Radial Position Reader
#
# Supports:
#   - ElastoDyn blade convention: RootMxc1, Spn1MLxb1, Spn1FLxb1 etc.
#   - BeamDyn blade convention:   B1N001Mxl, B1N001Fxl etc.
#
# Radial positions of blade gage outputs (Spn1...Spn9) are computed from the
# ElastoDyn primary input file using the OpenFAST analysis-node formula:
#
#     RNodes(J) = HubRad + (J - 0.5) * (TipRad - HubRad) / BldNodes
#
# where:
#     J        = analysis node number (1..BldNodes), 1-indexed
#     BldNodes = number of analysis nodes per blade (e.g. 17 for NREL 5MW)
#     HubRad   = hub radius (rotor apex to blade root)
#     TipRad   = distance from rotor apex to blade tip
#
# The blade-properties file (AeroDyn or ElastoDyn blade file) defines mass and
# stiffness as functions of BlFract — those fractions are NOT analysis-node
# positions and must NOT be used as gage locations. (Mirroring the situation
# for the tower, where HtFract is also a properties table, not a node list.)
#
# BldGagNd[i] (1..BldNodes) tells which analysis node the i-th gage output
# (Spn1..Spn9) was reported at; we feed that into the formula above.
#
# Station mapping returned to the rest of the pipeline:
#     Root    -> HubRad  (blade root, on rotor circumference)
#     Spn1..N -> RNodes(BldGagNd[0..N-1])
# =============================================================================

import os
import re
import numpy as np


# ─── Sensor name patterns ─────────────────────────────────────────────────────

_ED_ROOT_MOMENT = re.compile(r'^RootM([xyz])b([123])(?:_.*)?$', re.IGNORECASE)
_ED_ROOT_FORCE  = re.compile(r'^RootF([xyz])b([123])(?:_.*)?$', re.IGNORECASE)

_ED_SPN_MOMENT  = re.compile(r'^Spn(\d+)ML([xyz])b([123])(?:_.*)?$', re.IGNORECASE)
_ED_SPN_FORCE   = re.compile(r'^Spn(\d+)FL([xyz])b([123])(?:_.*)?$', re.IGNORECASE)

_BD_MOMENT      = re.compile(r'^B([123])N(\d+)M([xyz])l(?:_.*)?$', re.IGNORECASE)
_BD_FORCE       = re.compile(r'^B([123])N(\d+)F([xyz])l(?:_.*)?$', re.IGNORECASE)


_MOMENT_MAP = {'x': 'Mx', 'y': 'My', 'z': 'Mz'}
_FORCE_MAP  = {'x': 'Fx', 'y': 'Fy', 'z': 'Fz'}


def detect_blade_sensors(sensor_cols):
    """
    Auto-detect blade sensor channels from the OpenFAST output column list.
    Supports both ElastoDyn and BeamDyn naming conventions.

    Parameters
    ----------
    sensor_cols : list of str — all sensor column names from the output file

    Returns
    -------
    convention : str — 'ElastoDyn', 'BeamDyn', or 'None'
    stations   : dict — ordered dict {station_key: {blade: {component: sensor_name}}}
                 station_key: 'Root', 'Spn1' ... 'Spn9' (ED) or 'N001' ... (BD)
                 blade: 1, 2, 3
                 component: 'Mx', 'My', 'Mz', 'Fx', 'Fy', 'Fz'
    n_blades   : int — number of blades detected (1, 2, or 3)
    """
    ed_found = {}
    bd_found = {}

    for col in sensor_cols:
        col_base = col.split('_[')[0]

        m = _ED_ROOT_MOMENT.match(col_base)
        if m:
            comp, blade = m.group(1).lower(), int(m.group(2))
            ed_found[(0, blade, _MOMENT_MAP[comp])] = col
            continue
        m = _ED_ROOT_FORCE.match(col_base)
        if m:
            comp, blade = m.group(1).lower(), int(m.group(2))
            ed_found[(0, blade, _FORCE_MAP[comp])] = col
            continue

        m = _ED_SPN_MOMENT.match(col_base)
        if m:
            station = int(m.group(1))
            comp    = m.group(2).lower()
            blade   = int(m.group(3))
            ed_found[(station, blade, _MOMENT_MAP[comp])] = col
            continue
        m = _ED_SPN_FORCE.match(col_base)
        if m:
            station = int(m.group(1))
            comp    = m.group(2).lower()
            blade   = int(m.group(3))
            ed_found[(station, blade, _FORCE_MAP[comp])] = col
            continue

        m = _BD_MOMENT.match(col_base)
        if m:
            blade   = int(m.group(1))
            node    = int(m.group(2))
            comp    = m.group(3).lower()
            bd_found[(node, blade, _MOMENT_MAP[comp])] = col
            continue
        m = _BD_FORCE.match(col_base)
        if m:
            blade   = int(m.group(1))
            node    = int(m.group(2))
            comp    = m.group(3).lower()
            bd_found[(node, blade, _FORCE_MAP[comp])] = col
            continue

    if ed_found and len(ed_found) >= len(bd_found):
        convention = 'ElastoDyn'
        found = ed_found
    elif bd_found:
        convention = 'BeamDyn'
        found = bd_found
    else:
        return 'None', {}, 0

    stations_set   = sorted({k[0] for k in found.keys()})
    blades_set     = sorted({k[1] for k in found.keys()})
    n_blades       = len(blades_set)

    stations = {}
    for s in stations_set:
        if convention == 'ElastoDyn':
            label = 'Root' if s == 0 else f'Spn{s}'
        else:
            label = f'N{s:03d}'
        stations[label] = {}
        for b in blades_set:
            stations[label][b] = {}
            for comp in ('Mx', 'My', 'Mz', 'Fx', 'Fy', 'Fz'):
                stations[label][b][comp] = found.get((s, b, comp))
    return convention, stations, n_blades


# =============================================================================
# ElastoDyn primary file parsing helpers
# =============================================================================

def _parse_ed_value(lines, keyword):
    """
    Extract a numeric scalar parameter from ElastoDyn-style input lines.
    Lines look like:  3.0   HubRad   - Preconed hub radius (m)
    """
    pat = re.compile(r'^\s*([-+0-9eE.]+)\s+' + re.escape(keyword) + r'\b',
                     re.IGNORECASE)
    for line in lines:
        m = pat.match(line)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def _parse_bld_gag_nd(lines, n_gages):
    """
    Parse the BldGagNd line — a list of n_gages integers preceded by
    'BldGagNd' on the same line, comment after.

    Example line:
        1, 3, 5, 7, 9, 11, 13, 15, 17    BldGagNd  - Nodes for blade gage outputs
    """
    idx = None
    for i, line in enumerate(lines):
        if re.search(r'\bBldGagNd\b', line, re.IGNORECASE):
            idx = i
            break
    if idx is None:
        return []

    candidate_lines = [lines[idx]]
    if idx > 0:
        candidate_lines.append(lines[idx - 1])

    for cl in candidate_lines:
        head = re.split(r'\bBldGagNd\b', cl, maxsplit=1, flags=re.IGNORECASE)[0]
        tokens = head.replace(',', ' ').split()
        try:
            ints = [int(tok) for tok in tokens]
            if 1 <= len(ints) <= 9 and (n_gages == 0 or len(ints) >= n_gages):
                return ints[:n_gages] if n_gages > 0 else ints
        except ValueError:
            continue
    return []


def read_blade_radials(ed_file_path, station_labels, log=None):
    """
    Read blade analysis-node radial positions from an ElastoDyn primary file.

    Uses the OpenFAST formula:
        RNodes(J) = HubRad + (J - 0.5) * (TipRad - HubRad) / BldNodes

    Parameters
    ----------
    ed_file_path   : str — path to the ElastoDyn primary input file
    station_labels : list of str — labels to populate (Root, Spn1..Spn9, ...)
    log            : PostProcessLogger or None

    Returns
    -------
    dict {station_label: radial_distance_m_or_None}
    """
    fallback = {label: None for label in station_labels}

    if not ed_file_path or not os.path.isfile(ed_file_path):
        if ed_file_path and log:
            log.warning(f"ElastoDyn file not found: {ed_file_path}",
                        tag='BldReader')
        return fallback

    try:
        with open(ed_file_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception as e:
        if log:
            log.warning(f"Cannot read ED file '{ed_file_path}': {e}",
                        tag='BldReader')
        return fallback

    tip_rad     = _parse_ed_value(lines, 'TipRad')
    hub_rad     = _parse_ed_value(lines, 'HubRad')
    bld_nodes_f = _parse_ed_value(lines, 'BldNodes')
    bld_nodes   = int(bld_nodes_f) if bld_nodes_f is not None else 0
    n_gages_f   = _parse_ed_value(lines, 'NBlGages')
    n_gages     = int(n_gages_f) if n_gages_f is not None else 0
    gage_nodes  = _parse_bld_gag_nd(lines, n_gages)

    if tip_rad is None or hub_rad is None or bld_nodes <= 0:
        if log:
            log.warning(
                f"Insufficient ED data for blade nodes "
                f"(TipRad={tip_rad}, HubRad={hub_rad}, BldNodes={bld_nodes}). "
                f"Falling back to None radial positions.",
                tag='BldReader')
        return fallback

    span = tip_rad - hub_rad

    def rnode(j):
        if j < 1 or j > bld_nodes:
            return None
        return hub_rad + (j - 0.5) * span / bld_nodes

    result = {}
    for label in station_labels:
        if label == 'Root':
            result[label] = hub_rad
            continue
        m = re.match(r'^(?:Spn|N)(\d+)$', label, re.IGNORECASE)
        if not m:
            result[label] = None
            continue
        spn_idx = int(m.group(1)) - 1
        if 0 <= spn_idx < len(gage_nodes):
            result[label] = rnode(gage_nodes[spn_idx])
        else:
            result[label] = None

    if log:
        n_resolved = sum(1 for v in result.values() if v is not None)
        log.info(
            f"Blade radials: TipRad={tip_rad}m, HubRad={hub_rad}m, "
            f"BldNodes={bld_nodes}, {n_gages} gages, "
            f"{n_resolved}/{len(station_labels)} stations resolved",
            tag='BldReader')

    return result


# =============================================================================
# Backwards-compatible wrapper used by main.py
# =============================================================================

def read_radial_positions(blade_or_ed_file_path, blade_length, station_labels,
                          log=None):
    """
    Backwards-compatible entry point used by main.py.

    Preferred: pass the **ElastoDyn primary input file** here (it exposes
    TipRad/HubRad/BldNodes/NBlGages/BldGagNd, which gives correct gage
    radial positions via the OpenFAST formula).

    Legacy: if the file looks like a blade-properties file (BlFract table),
    we fall back to BlFract * blade_length, which is only an approximation
    and warn the user that they should switch the path.

    Parameters
    ----------
    blade_or_ed_file_path : str — preferred: ElastoDyn primary input file
    blade_length          : float — kept for backwards compatibility
    station_labels        : list of str — labels to populate
    log                   : PostProcessLogger or None

    Returns
    -------
    dict {station_label: radial_distance_m or None}
    """
    fallback = {label: None for label in station_labels}

    if not blade_or_ed_file_path or not os.path.isfile(blade_or_ed_file_path):
        if blade_or_ed_file_path and log:
            log.warning(
                f"Blade/ED file not found: {blade_or_ed_file_path}. "
                f"Radial distances will be unavailable.",
                tag='BldReader')
        return fallback

    try:
        with open(blade_or_ed_file_path, 'r',
                  encoding='utf-8', errors='replace') as f:
            head = f.read(20000)
    except Exception as e:
        if log:
            log.warning(f"Cannot read file '{blade_or_ed_file_path}': {e}",
                        tag='BldReader')
        return fallback

    looks_like_ed = bool(
        re.search(r'\bTipRad\b',   head, re.IGNORECASE) and
        re.search(r'\bHubRad\b',   head, re.IGNORECASE) and
        re.search(r'\bBldNodes\b', head, re.IGNORECASE)
    )

    if looks_like_ed:
        return read_blade_radials(blade_or_ed_file_path, station_labels,
                                  log=log)

    # ── Legacy fallback: parse BlFract column from blade-properties file ──
    if log:
        log.warning(
            f"BLADE_FILE_PATH '{os.path.basename(blade_or_ed_file_path)}' "
            f"does not look like an ElastoDyn primary file (no TipRad/HubRad). "
            f"Update sensorList.txt [BLADE].BLADE_FILE_PATH to point at the "
            f"ED primary file. Falling back to BlFract * blade_length.",
            tag='BldReader')

    try:
        fractions = []
        with open(blade_or_ed_file_path, 'r',
                  encoding='utf-8', errors='replace') as f:
            in_table = False
            for line in f:
                line = line.strip()
                if not line or line.startswith('!') or line.startswith('#'):
                    continue
                parts = line.split()
                if not in_table:
                    try:
                        val = float(parts[0])
                        if 0.0 <= val <= 1.0:
                            in_table = True
                            fractions.append(val)
                    except (ValueError, IndexError):
                        continue
                else:
                    try:
                        val = float(parts[0])
                        if 0.0 <= val <= 1.0:
                            fractions.append(val)
                        else:
                            break
                    except (ValueError, IndexError):
                        break

        if not fractions:
            return fallback

        radial = [f * blade_length for f in fractions]
        result = {}
        for i, label in enumerate(station_labels):
            if label == 'Root':
                result[label] = 0.0
            else:
                m = re.match(r'^(?:Spn|N)(\d+)$', label, re.IGNORECASE)
                if m:
                    spn_idx = int(m.group(1)) - 1
                    result[label] = radial[spn_idx] \
                        if 0 <= spn_idx < len(radial) else None
                else:
                    result[label] = None
        return result
    except Exception as e:
        if log:
            log.warning(f"Could not parse blade file: {e}", tag='BldReader')
        return fallback


def format_station_label(label, radial_pos):
    """
    Format the radial station label for output.

    Examples
    --------
    ('Root', 1.5)  -> '1.5 (Root)'
    ('Spn3', 21.3) -> '21.3 (Spn3)'
    ('Spn3', None) -> 'Spn3'
    """
    if radial_pos is not None:
        return f"{radial_pos:.1f} ({label})"
    return label
