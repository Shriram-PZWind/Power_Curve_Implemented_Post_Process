# =============================================================================
# tower_reader.py — Tower station height reader
# =============================================================================
#
# Reads tower station heights from the ElastoDyn primary input file.
#
# Per OpenFAST ElastoDyn documentation
# (https://openfast.readthedocs.io/en/main/source/user/elastodyn/input.html):
#
#   Elev(J) = TowerBsHt + (J - 0.5) * (TowerHt - TowerBsHt) / TwrNodes
#
# where:
#   J         = analysis node number (1 to TwrNodes), 1-indexed
#   TwrNodes  = number of tower analysis nodes (e.g. 20)
#   TowerHt   = total tower height above ground (or MSL for offshore)
#   TowerBsHt = tower base height above ground (or MSL for offshore)
#
# The tower is divided into TwrNodes equal-length segments. Analysis nodes
# are at the MIDPOINT of each segment - never at the base, never at the top.
#
# TwrGagNd is a list of analysis-node numbers (1..TwrNodes) where strain-gage
# outputs are reported. Up to 9 gages (NTwGages) are supported.
#
# The TwrFile (tower-properties file) is NOT used for height calculation;
# HtFract values in that file define where structural properties (mass,
# stiffness) are tabulated, not where analysis nodes lie.
#
# Station mapping (sensorList.txt [TOWER] labels):
#   Top    -> TowerBsHt + TowerHt    (top of tower / yaw bearing)
#   TwHt9  -> gage 9 -> TwrGagNd[8] -> formula above
#   ...
#   TwHt1  -> gage 1 -> TwrGagNd[0] -> formula above
#   Base   -> TowerBsHt              (tower base, typically 0)
#
# Returns heights in metres above ground (or MSL for offshore).
# =============================================================================

import os
import re


def _parse_ed_value(lines, keyword):
    """
    Extract a numeric value for a keyword from ElastoDyn file lines.
    Handles format: value   KeyWord   - comment
    Returns float or None.
    """
    kw_lower = keyword.lower()
    for line in lines:
        # Strip inline comment after '-' or '!'
        clean = re.split(r'\s+[-!]', line)[0].strip()
        parts = clean.split()
        if len(parts) >= 2 and parts[1].lower() == kw_lower:
            try:
                return float(parts[0].strip('"\''))
            except ValueError:
                return None
        # Also try: value   -- KeyWord
        if len(parts) >= 1 and any(p.lower() == kw_lower for p in parts[1:]):
            try:
                return float(parts[0].strip('"\''))
            except ValueError:
                return None
    return None


def _parse_ed_string(lines, keyword):
    """
    Extract a quoted string value for a keyword from ElastoDyn file lines.
    Returns str or None.
    """
    kw_lower = keyword.lower()
    for line in lines:
        clean = re.split(r'\s+[-!]', line)[0].strip()
        parts = clean.split()
        if len(parts) >= 2 and any(p.lower() == kw_lower for p in parts[1:]):
            return parts[0].strip('"\'')
    return None


def _parse_twr_gag_nd(lines, n_gages):
    """
    Extract TwrGagNd node numbers from ElastoDyn primary file.

    Handles both comma-separated and whitespace-separated lists, e.g.:
        1, 3, 5, 7, 9, 11, 13, 15, 17    TwrGagNd  - Tower strain-gage nodes
    or:
        1 3 5 7 9 11 13 15 17    TwrGagNd  - Tower strain-gage nodes

    Returns list of ints (length = n_gages), or empty list.
    """
    kw = 'twrgagnd'
    for line in lines:
        # Strip the trailing comment that ElastoDyn input files use
        clean = re.split(r'\s+[-!]', line)[0]
        # Normalise commas/tabs to spaces so '1, 3, 5' tokenises correctly
        clean = clean.replace(',', ' ').strip()
        parts = clean.split()
        if not parts:
            continue
        for i, p in enumerate(parts):
            if p.lower() == kw:
                vals = parts[:i]
                try:
                    return [int(v) for v in vals]
                except ValueError:
                    pass
    return []


def _parse_ht_fract(tower_file_path):
    """
    Read HtFract column from ElastoDyn tower properties file.
    The tower file has a distributed properties table.
    The first column after the header rows is HtFract (0.0 to 1.0).

    Returns list of float (one per row in the table), or empty list.
    """
    ht_fracts = []
    try:
        with open(tower_file_path, 'r') as f:
            lines = f.readlines()

        # Find the distributed properties table
        # Header typically contains: HtFract  TMassDen  FlpStff  EdgStff ...
        in_table = False
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('!') or stripped.startswith('-'):
                continue
            # Detect table header line
            if not in_table:
                if 'HtFract' in stripped or 'htfract' in stripped.lower():
                    in_table = True
                    continue   # skip header line
                # Also skip the units row that follows header
                continue
            # Skip units row (contains dashes or parentheses)
            if stripped.startswith('(') or all(
                    c in '-() \t' for c in stripped):
                continue
            # Parse data row — first column is HtFract
            parts = stripped.split()
            if not parts:
                continue
            try:
                val = float(parts[0])
                if 0.0 <= val <= 1.0:
                    ht_fracts.append(val)
                else:
                    # Reached end of table or non-fraction values
                    if ht_fracts:  # only stop if we already found some
                        break
            except ValueError:
                if ht_fracts:
                    break
    except Exception as e:
        print(f"  WARNING [tower_reader]: Error reading tower file "
              f"'{tower_file_path}': {e}")
    return ht_fracts


def read_tower_heights(ed_file_path, log=None):
    """
    Read tower station heights from ElastoDyn primary file.

    Parses:
      - TowerHt, TowerBsHt from primary ED file
      - TwrFile path (resolved relative to ED file directory)
      - TwrGagNd node numbers
      - HtFract column from tower properties file

    Computes:
      height[i] = TowerBsHt + HtFract[TwrGagNd[i] - 1] × TowerHt

    Parameters
    ----------
    ed_file_path : str — full path to ElastoDyn primary input file
    log          : PostProcessLogger or None

    Returns dict:
    {
      'tower_ht'    : float — total tower height (m)
      'base_ht'     : float — tower base height (m)
      'n_gages'     : int   — number of gages (0–9)
      'gage_nodes'  : list of int — TwrGagNd values
      'ht_fract'    : list of float — HtFract per node in tower file
      'heights'     : dict {station_label: float or None}
                      Keys: 'Top', 'TwHt1'–'TwHt9', 'Base'
    }
    or None if file not found.
    """
    if ed_file_path in (None, '', 'NONE'):
        return None

    if not os.path.isfile(ed_file_path):
        msg = f"ElastoDyn file not found: '{ed_file_path}'"
        if log:
            log.warning(msg, tag='TwrReader')
        else:
            print(f"  WARNING [tower_reader]: {msg}")
        return None

    try:
        with open(ed_file_path, 'r') as f:
            lines = f.readlines()
    except Exception as e:
        msg = f"Cannot read ElastoDyn file '{ed_file_path}': {e}"
        if log:
            log.warning(msg, tag='TwrReader')
        return None

    # ── Parse primary ED file ─────────────────────────────────────────────────
    tower_ht  = _parse_ed_value(lines, 'TowerHt')
    base_ht   = _parse_ed_value(lines, 'TowerBsHt')
    twr_nodes_f = _parse_ed_value(lines, 'TwrNodes')
    twr_nodes = int(twr_nodes_f) if twr_nodes_f is not None else 0
    twrfile   = _parse_ed_string(lines, 'TwrFile')
    n_gages_f = _parse_ed_value(lines, 'NTwGages')
    n_gages   = int(n_gages_f) if n_gages_f is not None else 0
    gage_nodes = _parse_twr_gag_nd(lines, n_gages)

    # Apply defaults
    if tower_ht is None:
        tower_ht = 87.6
        if log:
            log.warning('TowerHt not found — using default 87.6m', tag='TwrReader')
    if base_ht is None:
        base_ht = 0.0
    if twr_nodes <= 0:
        twr_nodes = 20  # NREL 5MW default
        if log:
            log.warning('TwrNodes not found — using default 20', tag='TwrReader')

    # ── Resolve tower-properties file (kept for diagnostics; NOT used for height) ──
    # NOTE: HtFract values in the tower-properties file describe where MASS and
    #       STIFFNESS are tabulated, not where analysis nodes sit. Heights of
    #       analysis nodes are computed from the (J-0.5)/N midpoint formula.
    ht_fracts = []
    if twrfile:
        ed_dir = os.path.dirname(os.path.abspath(ed_file_path))
        twr_path = os.path.join(ed_dir, twrfile)
        if os.path.isfile(twr_path):
            ht_fracts = _parse_ht_fract(twr_path)
        else:
            msg = f"Tower file not found: '{twr_path}'"
            if log:
                log.warning(msg, tag='TwrReader')
            else:
                print(f"  WARNING [tower_reader]: {msg}")

    # ── Compute heights per gage station using OpenFAST formula ───────────────
    # Elev(J) = TowerBsHt + (J - 0.5) * (TowerHt - TowerBsHt) / TwrNodes
    span = tower_ht - base_ht
    def gage_height(gage_idx):
        """Height for gage index (0-based). Returns None if data missing."""
        if not gage_nodes or gage_idx >= len(gage_nodes):
            return None
        node = gage_nodes[gage_idx]  # 1-based analysis-node number
        if node < 1 or node > twr_nodes:
            return None
        return base_ht + (node - 0.5) * span / twr_nodes

    heights = {
        'Top'  : base_ht + tower_ht,
        'TwHt9': gage_height(8),
        'TwHt8': gage_height(7),
        'TwHt7': gage_height(6),
        'TwHt6': gage_height(5),
        'TwHt5': gage_height(4),
        'TwHt4': gage_height(3),
        'TwHt3': gage_height(2),
        'TwHt2': gage_height(1),
        'TwHt1': gage_height(0),
        'Base' : base_ht,
    }

    if log:
        n_resolved = sum(1 for v in heights.values() if v is not None)
        log.info(
            f"Tower heights: TowerHt={tower_ht}m, Base={base_ht}m, "
            f"TwrNodes={twr_nodes}, {n_gages} gages, "
            f"{n_resolved}/11 stations resolved",
            tag='TwrReader')

    return {
        'tower_ht'  : tower_ht,
        'base_ht'   : base_ht,
        'twr_nodes' : twr_nodes,
        'n_gages'   : n_gages,
        'gage_nodes': gage_nodes,
        'ht_fract'  : ht_fracts,
        'heights'   : heights,
    }


def format_station_label_twr(label, height_m=None):
    """
    Format a tower station label with optional height.
    Examples:
      format_station_label_twr('TwHt1', 12.2) → 'TwHt1  (12.2m)'
      format_station_label_twr('Base',  0.0)  → 'Base   ( 0.0m)'
      format_station_label_twr('TwHt3', None) → 'TwHt3'
    """
    if height_m is None:
        return label
    return f"{label:<7} ({height_m:5.1f}m)"
