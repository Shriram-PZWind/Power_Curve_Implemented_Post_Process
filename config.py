# =============================================================================
# config.py
# OpenFAST Postprocessing — User Configuration
# =============================================================================
# Edit only this file to configure the postprocessing run.

import os
import re
import sys
from tower_reader import read_tower_heights

# ─── Command Line Argument Parsing ────────────────────────────────────────────
# Expected syntax:
# python main.py <INPUT_FOLDER> <OUTPUT_FOLDER> <LC_POSTPROCESS_PATH> <SENSOR_LIST_PATH>

def _parse_cli_arguments():
    """Validates and extracts global paths from command-line arguments."""
    if len(sys.argv) < 5:
        print("\n[ERROR] Missing required command-line arguments!")
        print("Usage:")
        print("  python main.py <INPUT_FOLDER> <OUTPUT_FOLDER> <LC_POSTPROCESS_PATH> <SENSOR_LIST_PATH>\n")
        sys.exit(1)
        
    # os.path.abspath resolves relative paths and cleans up mismatched slashes
    input_folder  = os.path.abspath(sys.argv[1])
    output_folder = os.path.abspath(sys.argv[2])
    lc_path       = os.path.abspath(sys.argv[3])

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    dest_path = os.path.join(BASE_DIR, "sensorList.txt")
    input_dir = os.path.join(BASE_DIR, "sensor_list_parts")

    """
    Gathers all split section files, sorts them by sequential numeric prefix,
    and stitches them back into a single unified sensorList.txt with zero data loss.
    """
    if not os.path.exists(input_dir):
        print(f"Error: Split parts directory not found at: {input_dir}")
        return

    # Filter out only the .txt files from the split folder
    files = [f for f in os.listdir(input_dir) if f.endswith(".txt")]
    files.sort()  # Alphabetical sort naturally aligns the numeric 00_, 01_, 02_ prefixes

    if not files:
        print(f"Error: No configuration part files found in '{input_dir}'")
        return

    all_lines = []
    print("Re-aggregating sensor configurations in sequence:")
    for filename in files:
        file_path = os.path.join(input_dir, filename)
        print(f"  [Merging] <- {filename}")
        with open(file_path, "r", encoding="utf-8") as in_f:
            all_lines.extend(in_f.readlines())

    # Overwrite/Generate the unified sensor list file
    with open(dest_path, "w", encoding="utf-8") as out_f:
        out_f.writelines(all_lines)

    print(
        f"\nSuccess! Reconstructed unified file with 100% data fidelity at:\n  '{dest_path}'"
    )
    
    return input_folder, output_folder, lc_path, dest_path

# Initialize global configuration paths from sys.argv first
INPUT_FOLDER, OUTPUT_FOLDER, LC_POSTPROCESS_PATH, SENSOR_LIST_PATH = _parse_cli_arguments()


# ─── Paths ────────────────────────────────────────────────────────────────────
# INPUT_FOLDER  = r'C:\Users\PZWind-AkashM\Desktop\setup\V7_Simulations\IEC_2A_\OUTPUT\Test\1-file'   # folder containing .outb / .out files
# OUTPUT_FOLDER = r'C:\Users\PZWind-AkashM\Desktop\setup\V7_Simulations\IEC_2A_\OUTPUT\Test\1-file\1-files-debugmode'            # root output folder

# Subfolders created automatically inside OUTPUT_FOLDER
STA_FOLDER = os.path.join(OUTPUT_FOLDER, 'STA')  # .sta files (per-file + summary)
FAT_FOLDER = os.path.join(OUTPUT_FOLDER, 'FAT')  # .rfc, .markov, .ldd, .lrd files
EXT_FOLDER = os.path.join(OUTPUT_FOLDER, 'EXT')  # .max, .min, .abs files
SUM_FOLDER = os.path.join(OUTPUT_FOLDER, 'SUM')
LOG_FILE   = os.path.join(OUTPUT_FOLDER, 'postprocess.log')  # .sum component load summary files

# Blade settings and component summary configuration are now read from
# sensorList.txt [BLADE] section — no settings needed here.

# ─── Load case post-processing input file ─────────────────────────────────────
# Path to LC_PostProcess.txt — the main input file controlling fatigue analysis.
#
# The file has two sections:
#
#   1) Header parameters (key = value), before the file table:
#        LIFETIME_YEARS = 20       <- turbine design lifetime in years
#        NEQ_LIFETIME   = 1e7      <- reference cycle count for lifetime DEL
#
#   2) File table — 8 space-separated columns:
#        Col 1 : Filename          (.outb or .out)
#        Col 2 : SimTime           simulation duration (s)
#        Col 3 : Family            wind speed group index
#        Col 4 : FamilyMethod      averaging method (1=mean, 2=max, 3=weighted)
#        Col 5 : PLF               partial load factor
#        Col 6 : OccurrenceFreq_20yr  number of occurrences over lifetime
#        Col 7 : OccurrenceHours_20yr hours represented over lifetime
#        Col 8 : Probability_%     probability per file (bin prob / n_seeds)
#
# Lines starting with # and blank lines are ignored.
# The TOTAL summary row at the bottom is automatically skipped.
#
# These files are used to:
#   (1) Determine global bin ranges for RFC / Markov / LDD / LRD matrices
#   (2) Accumulate lifetime cycle counts  (cycles_k × occurrences_k per file)
# All other files in INPUT_FOLDER are processed for extreme stats + per-file
# DEL only.


# LC_POSTPROCESS_PATH = r'C:\Users\PZWind-AkashM\Desktop\Post-Proccessing\V7_BugFix2\LC_PostProcess.txt'
# SENSOR_LIST_PATH = r'C:\Users\PZWind-AkashM\Desktop\Post-Proccessing\V7_BugFix2\sensorList.txt'

# Column indices in the file table (0-based)
_COL_FILENAME   = 0
_COL_SIMTIME    = 1
_COL_FAMILY     = 2
_COL_FAMMETHOD  = 3
_COL_PLF        = 4
_COL_OCC_FREQ   = 5
_COL_OCC_HOURS  = 6
_COL_PROB       = 7
_N_COLS_EXPECTED = 8


def _read_lc_postprocess(path):
    """
    Read LC_PostProcess.txt — header parameters and 8-column file table.

    Header parameters (key = value lines, before the file table):
      LIFETIME_YEARS   turbine design lifetime in years   (e.g. 20)
      NEQ_LIFETIME     reference cycle count for lifetime DEL  (e.g. 1e7)

    File table (8 space-separated columns):
      Col 1  Filename            .outb or .out
      Col 2  SimTime             simulation duration (s)
      Col 3  Family              wind speed group index
      Col 4  FamilyMethod        averaging method (1/2/3)
      Col 5  PLF                 partial load factor
      Col 6  OccurrenceFreq_20yr number of occurrences over lifetime  ← used by pipeline
      Col 7  OccurrenceHours_20yr hours represented over lifetime
      Col 8  Probability_%       probability per file

    The TOTAL summary row at the bottom is skipped automatically.

    Returns
    -------
    files          : dict {filename: occurrences}
    metadata       : dict {filename: {Family, FamilyMethod, PLF, SimTime}}
    lifetime_years : float
    neq_lifetime   : float
    neq_rev        : float
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"LC_PostProcess.txt not found:\n  {path}\n"
            f"Please set LC_POSTPROCESS_PATH in config.py to the correct location."
        )

    REQUIRED_HEADER_KEYS = {'LIFETIME_YEARS', 'NEQ_LIFETIME', 'NEQ_REV'}
    header   = {}
    files    = {}
    metadata = {}  # {filename: {Family, FamilyMethod, PLF, SimTime}}

    with open(path, 'r', encoding='utf-8') as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Header parameter line: key = value
            if '=' in line and len(line.split()) <= 3:
                key, _, val = line.partition('=')
                key = key.strip()
                if key in REQUIRED_HEADER_KEYS:
                    try:
                        header[key] = float(val.strip())
                    except ValueError:
                        raise ValueError(
                            f"LC_PostProcess.txt line {lineno}: cannot parse "
                            f"header value for '{key}': '{val.strip()}'"
                        )
                    continue

            # Skip column header rows (Filename / [-] rows) and separator
            # lines made entirely of = or - characters (any length).
            parts = line.split()
            if parts[0] in ('Filename', '[-]', '=', '-'):
                continue
            # Detect separator runs like ===== or ----- (one token, all = or -)
            if len(parts) == 1 and set(parts[0]) <= {'=', '-'}:
                continue

            # Skip TOTAL summary row
            if parts[0].upper().startswith('TOTAL'):
                continue

            # File table row — expect 8 columns
            if len(parts) < _N_COLS_EXPECTED:
                raise ValueError(
                    f"LC_PostProcess.txt line {lineno}: expected {_N_COLS_EXPECTED} "
                    f"columns, got {len(parts)}: '{line}'"
                )

            fname = parts[_COL_FILENAME]

            # Skip if filename ends with non-output extension (safety check)
            if not (fname.endswith('.outb') or fname.endswith('.out')):
                continue

            try:
                occurrences = float(parts[_COL_OCC_FREQ])
            except ValueError:
                raise ValueError(
                    f"LC_PostProcess.txt line {lineno}: OccurrenceFreq_20yr value "
                    f"'{parts[_COL_OCC_FREQ]}' is not a number"
                )
            if occurrences < 0:
                raise ValueError(
                    f"LC_PostProcess.txt line {lineno}: OccurrenceFreq_20yr must "
                    f"be >= 0, got {occurrences} for '{fname}'"
                )

            files[fname] = occurrences
            metadata[fname] = {
                'SimTime'     : float(parts[_COL_SIMTIME]),
                'Family'      : int(float(parts[_COL_FAMILY])),
                'FamilyMethod': int(float(parts[_COL_FAMMETHOD])),
                'PLF'         : float(parts[_COL_PLF]),
            }

    # Validate header completeness
    missing_keys = REQUIRED_HEADER_KEYS - set(header.keys())
    if missing_keys:
        raise ValueError(
            f"LC_PostProcess.txt is missing required header parameters: "
            f"{', '.join(sorted(missing_keys))}\n"
            f"Add these lines before the file table:\n"
            f"  LIFETIME_YEARS = <years>\n"
            f"  NEQ_LIFETIME   = <cycles>\n"
            f"  NEQ_REV        = <revolutions>"
        )

    if not files:
        raise ValueError(
            f"No valid file entries found in:\n  {path}\n"
            f"Check the file table contains rows with .outb or .out filenames."
        )

    return files, metadata, header['LIFETIME_YEARS'], header['NEQ_LIFETIME'], header['NEQ_REV']


FATIGUE_FILES, FILE_METADATA, LIFETIME_YEARS, NEQ_LIFETIME, NEQ_REV = _read_lc_postprocess(LC_POSTPROCESS_PATH)

# Derived lifetime in seconds
LIFETIME_SECS = LIFETIME_YEARS * 365.25 * 24 * 3600

# ─── Fatigue parameters ───────────────────────────────────────────────────────
def _read_fatigue_parameters(component_config):
    """
    Parse [FATIGUE_PARAMETERS] from sensorList.txt (Table 0).
    Returns (rfc_m, ldd_m, lrd_m) as lists of float.
    Falls back to defaults if section not found.

    Parameters
    ----------
    component_config : dict
        The COMPONENT_CONFIG dict already populated by _read_sensor_list.
    """
    _defaults = [3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 25.0]
    rfc_m = list(_defaults)
    ldd_m = list(_defaults)
    lrd_m = list(_defaults)
    try:
        params = component_config.get('FATIGUE_PARAMETERS', {})
        if not params:
            return rfc_m, ldd_m, lrd_m

        def _parse(key, default):
            val = params.get(key, '').strip()
            if not val:
                return list(default)
            try:
                return [float(x) for x in val.split()]
            except ValueError:
                return list(default)

        rfc_m = _parse('RFC_M_VALUES', _defaults)
        ldd_m = _parse('LDD_M_VALUES', _defaults)
        lrd_m = _parse('LRD_M_VALUES', _defaults)
    except Exception:
        pass
    return rfc_m, ldd_m, lrd_m


def fmt_slope(m):
    """
    Format a Wöhler slope consistently for use in file headers and keys.
    Always uses one decimal place: 4 → '4.0', 3.3 → '3.3', 12 → '12.0'
    This ensures DEL_m4.0 is written and searched consistently.
    """
    return f'{float(m):.1f}'


# ─── Bin count ────────────────────────────────────────────────────────────────
N_BINS = 50    # bins for RFC / Markov / LDD / LRD

# ─── Channel names ────────────────────────────────────────────────────────────
TIME_CHANNEL        = 'Time_[s]'        # time channel in OpenFAST output
ROTOR_SPEED_CHANNEL = 'RotSpeed_[rpm]'  # rotor speed channel for revolution integration

# ─── Sensor list ──────────────────────────────────────────────────────────────
# Path to sensorList.txt — controls which calculations are performed per sensor.
# Sensors in the OpenFAST output but NOT listed here default to STA=1, 1HzEq=1,
# all other flags = 0.
# SENSOR_LIST_PATH = r'C:\Users\PZWind-DhyeyK\Desktop\post-pro\v7_claud\V7_BugFix1\sensorList.txt'

# Column indices in sensorList.txt table (0-based after SensorName)
_SL_COLS = ['STA', '1HzEq', 'EXT', 'RFC', 'Markov', 'LRD', 'LDD', 'Complimentary', 'CompMethod']
_SL_GROUP_COL = 1   # Group is col index 1 (after SensorName)
_SL_FLAG_START = 2  # Flags start at col index 2


def _read_sensor_list(path):
    """
    Read sensorList.txt — 10-column sensor control table.

    Columns: SensorName | Group | STA | 1HzEq | EXT | RFC | Markov | LRD | LDD | Complimentary

    Returns
    -------
    dict {sensor_name: {'Group': int, 'STA': int, '1HzEq': int, 'EXT': int,
                        'RFC': int, 'Markov': int, 'LRD': int, 'LDD': int,
                        'Complimentary': int}}
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"sensorList.txt not found:\n  {path}\n"
            f"Please set SENSOR_LIST_PATH in config.py to the correct location."
        )

    sensors          = {}
    component_config = {}   # {section_name: {key: value}}
    current_section  = None
    skip_first_tokens = {'SensorName', '[-]', 'Group', 'STA'}  # header row tokens

    with open(path, 'r', encoding='utf-8') as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            # Section header detection: [BLADE], [TOWER] etc.
            if line.startswith('[') and line.endswith(']'):
                current_section = line[1:-1].strip().upper()
                if current_section not in component_config:
                    component_config[current_section] = {}
                continue

            # Key = value inside a section
            if current_section and '=' in line and len(parts) >= 3:
                key, _, val = line.partition('=')
                key = key.strip()                                                  #.upper()
                val = val.strip()
                # Strip inline comment after '#' (anything from first '#' onward)
                # so e.g. "DRT_Mx_a = LSShftMxa_[kN-m]  # shaft torque"
                # yields val = "LSShftMxa_[kN-m]" rather than the full string.
                # We only treat '#' as comment if surrounded by whitespace OR at
                # end of value — this protects against legitimate '#' in values
                # (none expected, but defensive).
                hash_pos = val.find('#')
                if hash_pos >= 0:
                    val = val[:hash_pos].rstrip()
                component_config[current_section][key] = val
                continue

            # Special handling for [DERIVED_SENSORS_FLAGS] section — table rows
            # Format: SensorName  Group  PLF  STA  1HzEq  EXT  RFC  Markov  LRD  LDD  Complimentary  CompMethod
            # Row has 12 tokens (sensor_name + 11 values).
            if current_section == 'DERIVED_SENSORS_FLAGS':
                # Skip column header rows (start with 'SensorName' or '[-]' etc.)
                if parts[0] in skip_first_tokens:
                    continue
                if len(parts) < 12:
                    raise ValueError(
                        f"sensorList.txt line {lineno}: "
                        f"[DERIVED_SENSORS_FLAGS] expects 12 columns "
                        f"(SensorName Group PLF STA 1HzEq EXT RFC Markov LRD LDD "
                        f"Complimentary CompMethod), got {len(parts)}: '{line}'"
                    )
                # Store row keyed by sensor name; value is the rest of the
                # tokens joined by single space (so spec.split() gives 11 tokens).
                sensor_name = parts[0]
                spec        = ' '.join(parts[1:])
                component_config[current_section][sensor_name] = spec
                continue

            # Skip placeholder lines inside other sections
            # if current_section:
            #     continue

            # Skip column header rows (sensor table)
            if parts[0] in skip_first_tokens:
                continue
            if len(parts) < 11:
                raise ValueError(
                    f"sensorList.txt line {lineno}: expected 11 columns, "
                    f"got {len(parts)}: '{line}'"
                )
            sensor_name = parts[0]
            try:
                group = int(parts[_SL_GROUP_COL])
                flags = {col: int(parts[_SL_FLAG_START + i])
                         for i, col in enumerate(_SL_COLS)}
            except ValueError as e:
                raise ValueError(
                    f"sensorList.txt line {lineno}: could not parse values: {e}"
                )
            sensors[sensor_name] = {'Group': group, **flags}

    if not sensors:
        raise ValueError(
            f"No sensor entries found in:\n  {path}\n"
            f"Check the file contains at least one data row."
        )
    return sensors, component_config


SENSOR_LIST, COMPONENT_CONFIG = _read_sensor_list(SENSOR_LIST_PATH)

# ─── Fatigue parameters (must be after COMPONENT_CONFIG is populated) ─────────
RFC_M_VALUES, LDD_M_VALUES, LRD_M_VALUES = _read_fatigue_parameters(COMPONENT_CONFIG)

# Combined unique set — used for bin edge scanning (Phase 2)
M_VALUES_ALL = sorted(set(RFC_M_VALUES + LDD_M_VALUES + LRD_M_VALUES))

# Backward-compatible alias — Phase 4 per-file DEL uses RFC slopes
M_VALUES = RFC_M_VALUES


def get_blade_config():
    """
    Extract blade summary configuration from COMPONENT_CONFIG.
    Returns dict with BLADE_FILE_PATH, BLADE_LENGTH, SUM_DEL_SLOPES, CONVENTION.
    """
    blade = COMPONENT_CONFIG.get('BLADE', {})
    blade_file = blade.get('BLADE_FILE_PATH', '')
    if blade_file.upper() == 'NONE':
        blade_file = None
    try:
        blade_length = float(blade.get('BLADE_LENGTH', 63.0))
    except ValueError:
        blade_length = 63.0
    slopes_str = blade.get('SUM_DEL_SLOPES', '10 12 25')
    try:
        del_slopes = [float(s) for s in slopes_str.split()]
    except ValueError:
        del_slopes = [10, 12, 25]
    convention = blade.get('CONVENTION', 'AUTO').upper()
    fracs_str  = blade.get('MAIN_BLD_FRACTIONS', '0  25  50  75')
    try:
        main_fractions = [float(s) / 100.0 for s in fracs_str.split()]
    except ValueError:
        main_fractions = [0.0, 0.25, 0.50, 0.75]
    return {
        'BLADE_FILE_PATH'   : blade_file,
        'BLADE_LENGTH'      : blade_length,
        'SUM_DEL_SLOPES'    : del_slopes,
        'CONVENTION'        : convention,
        'MAIN_BLD_FRACTIONS': main_fractions,
    }


def get_hub_config():
    """
    Extract hub summary configuration from COMPONENT_CONFIG.
    Returns dict:
      sensor_map : {comp: {B1: sensor, B2: sensor, B3: sensor}}
                   Components include Mres if HUB_Mres_B* keys are set.
      del_slopes : list of float
    """
    hub = COMPONENT_CONFIG.get('HUB', {})
    # Mres first so it appears at top of tables when present
    components = ['Mres', 'Mx', 'My', 'Mz', 'Fx', 'Fy', 'Fz']
    blades     = ['B1', 'B2', 'B3']
    sensor_map = {}
    for comp in components:
        sensor_map[comp] = {}
        for b in blades:
            key = f'HUB_{comp}_{b}'                                                       #.upper()
            val = hub.get(key, '').strip()
            sensor_map[comp][b] = val if val else None
    slopes_str = hub.get('HUB_DEL_SLOPES', '3 6')
    try:
        del_slopes = [float(s) for s in slopes_str.split()]
    except ValueError:
        del_slopes = [3, 6]
    return {'sensor_map': sensor_map, 'del_slopes': del_slopes}


def get_derived_sensors():
    """
    Parse [DERIVED_SENSORS_DEF] and [DERIVED_SENSORS_FLAGS] sections
    from sensorList.txt (Tables 3A and 3B).

    Table 3A ([DERIVED_SENSORS_DEF]): name = formula
    Table 3B ([DERIVED_SENSORS_FLAGS]): name  group  plf  sta  1hzeq  ext  rfc  markov  lrd  ldd  comp  compmethod

    Returns list of dicts, one per derived sensor:
    {
      'name'    : str   — derived sensor name
      'formula' : str   — raw formula string
      'operands': list  — component sensor names from formula
      'group'   : int
      'plf'     : float
      'sta'     : int
      '1hzeq'   : int
      'ext'     : int
      'rfc'     : int
      'markov'  : int
      'lrd'     : int
      'ldd'     : int
    }
    Returns [] if sections not found.
    """
    import re

    # ── Parse Table 3A: formulas ─────────────────────────────────────────────
    def_section = COMPONENT_CONFIG.get('DERIVED_SENSORS_DEF', {})

    print(f"DEBUG: Keys in Table 3A: {[repr(k) for k in def_section.keys()]}")

    formulas = {}   # {name: (formula_str, operands_list)}
    for name, spec in def_section.items():
        clean_name = name.strip().replace("'", "").replace('"', "").lower()
        if not spec or not spec.strip():
            continue
        # Strip inline comment
        formula = spec.split('#')[0].strip()
        # Extract operands: sensor names containing brackets
        # operands = re.findall(r'[A-Za-z]\w+(?:_\[\S+\])?', formula)
        # operands = [o for o in operands if o not in ('sqrt','abs','pow')]
        raw_operands = re.findall(r'\b[A-Za-z][\w+\[\]\-]*', formula)
        operands = [o for o in raw_operands if o.lower() not in ('sqrt', 'abs', 'pow', 'sin', 'cos')]
        formulas[clean_name] = (formula, operands)

    # ── Parse Table 3B: flags ────────────────────────────────────────────────
    flag_section = COMPONENT_CONFIG.get('DERIVED_SENSORS_FLAGS', {})
    results = []
    skip = {'SensorName', 'Group', 'PLF', 'STA', '1HzEq', 'EXT', 'RFC',
            'Markov', 'LRD', 'LDD', 'Complimentary', 'CompMethod'}
    for name, spec in flag_section.items():
        name = name.strip().replace("'", "").replace('"', "").lower()
        if name in skip or not spec or not spec.strip():
            continue
        parts = spec.split()
        if name not in formulas:
            print(f'  WARNING: {name} in Table 3B has no formula in Table 3A — skipping')
            continue
        formula, operands = formulas[name]
        try:
            group  = int(parts[0])   if len(parts) > 0 else 0
            plf    = float(parts[1]) if len(parts) > 1 else 1.0
            sta    = int(parts[2])   if len(parts) > 2 else 1
            hzeq   = int(parts[3])   if len(parts) > 3 else 1
            ext    = int(parts[4])   if len(parts) > 4 else 1
            rfc    = int(parts[5])   if len(parts) > 5 else 0
            markov = int(parts[6])   if len(parts) > 6 else 0
            lrd    = int(parts[7])   if len(parts) > 7 else 0
            ldd    = int(parts[8])   if len(parts) > 8 else 0
        except (ValueError, IndexError):
            print(f'  WARNING: Could not parse flags for {name} — skipping')
            continue
        # parts indices: 0=Group, 1=PLF, 2=STA, 3=1HzEq, 4=EXT, 5=RFC,
        #                6=Markov, 7=LRD, 8=LDD, 9=Complimentary, 10=CompMethod
        comp       = int(parts[9])   if len(parts) > 9  else 0
        compmethod = int(parts[10])  if len(parts) > 10 else 2
        results.append({
            'name'       : name,
            'formula'    : formula,
            'operands'   : operands,
            'group'      : group,
            'plf'        : plf,
            'sta'        : sta,
            '1hzeq'      : hzeq,
            'ext'        : ext,
            'rfc'        : rfc,
            'markov'     : markov,
            'lrd'        : lrd,
            'ldd'        : ldd,
            'comp'       : comp,
            'compmethod' : compmethod,
        })
    return results


def get_tower_config(log=None):
    """
    Extract tower summary configuration from COMPONENT_CONFIG.

    Reads tower station heights from TWR_ED_FILE (ElastoDyn primary file).
    Heights are resolved via TwrFile path and TwrGagNd node numbers.

    Returns dict:
    {
      'stations'  : list of dicts
                    {label, height_m, Mres, Mx, My, Mz, Fx, Fy, Fz}
      'del_slopes': list of float
      'tower_ht'  : float or None
      'base_ht'   : float or None
    }
    """
    twr   = COMPONENT_CONFIG.get('TOWER', {})
    comps = ['Mres', 'Mx', 'My', 'Mz', 'Fx', 'Fy', 'Fz']

    # Station order: Top → TwHt9...TwHt1 → Base
    station_keys = [
        ('Top',   'TWR_{}_Top'),
        ('TwHt9', 'TWR_{}_Ht9'), ('TwHt8', 'TWR_{}_Ht8'),
        ('TwHt7', 'TWR_{}_Ht7'), ('TwHt6', 'TWR_{}_Ht6'),
        ('TwHt5', 'TWR_{}_Ht5'), ('TwHt4', 'TWR_{}_Ht4'),
        ('TwHt3', 'TWR_{}_Ht3'), ('TwHt2', 'TWR_{}_Ht2'),
        ('TwHt1', 'TWR_{}_Ht1'), ('Base',  'TWR_{}_Base'),
    ]

    # Read tower heights from ElastoDyn files
    ed_file  = twr.get('TWR_ED_FILE', '').strip()
    twr_data = read_tower_heights(ed_file, log=log) \
        if ed_file and ed_file.upper() != 'NONE' else None
    height_map = twr_data['heights'] if twr_data else {}

    stations = []
    for label, key_pattern in station_keys:
        station = {
            'label'   : label,
            'height_m': height_map.get(label),
        }
        for comp in comps:
            key = key_pattern.format(comp)
            station[comp] = twr.get(key, '').strip() or None
        stations.append(station)

    slopes_str = twr.get('TWR_DEL_SLOPES', '4.0')
    try:
        del_slopes = [float(s) for s in slopes_str.split()]
    except ValueError:
        del_slopes = [4.0]

    return {
        'stations'  : stations,
        'del_slopes': del_slopes,
        'tower_ht'  : twr_data['tower_ht'] if twr_data else None,
        'base_ht'   : twr_data['base_ht']  if twr_data else None,
    }


def get_yaw_config():
    """
    Extract yaw system summary configuration from COMPONENT_CONFIG.

    Returns dict:
    {
      'del_slopes'  : list of int
      'loads'       : dict — {label: sensor_name} for Mres,Mx,My,Mz,Fx,Fy,Fz
      'accel_trans' : dict — {label: sensor_name} for TAxp,TAyp,TAzp
      'accel_ang'   : dict — {label: sensor_name} for RAxp,RAyp,RAzp
      'mz_sensor'   : str  — sensor name for Mz (used to read EXT file for Mz*)
    }
    """
    yaw = COMPONENT_CONFIG.get('YAW', {})

    slopes_str = yaw.get('YAW_DEL_SLOPES', '3')
    try:
        del_slopes = [float(s) for s in slopes_str.split()]
    except ValueError:
        del_slopes = [3]

    def _get(key):
        return yaw.get(key, '').strip() or None

    loads = {
        'Mres': _get('YAW_Mres'),
        'Mx'  : _get('YAW_Mx'),
        'My'  : _get('YAW_My'),
        'Mz'  : _get('YAW_Mz'),
        'Fx'  : _get('YAW_Fx'),
        'Fy'  : _get('YAW_Fy'),
        'Fz'  : _get('YAW_Fz'),
    }
    accel_trans = {
        'TAxp': _get('YAW_TAxp'),
        'TAyp': _get('YAW_TAyp'),
        'TAzp': _get('YAW_TAzp'),
    }
    accel_ang = {
        'RAxp': _get('YAW_RAxp'),
        'RAyp': _get('YAW_RAyp'),
        'RAzp': _get('YAW_RAzp'),
    }

    return {
        'del_slopes'  : del_slopes,
        'loads'       : loads,
        'accel_trans' : accel_trans,
        'accel_ang'   : accel_ang,
        'mz_sensor'   : loads.get('Mz'),
    }


def validate_sum_slopes(log=None):
    """
    Validate that all SUM DEL slopes are a subset of M_VALUES.
    Logs warnings for any mismatch.

    Returns dict of mismatches:
    { section_name: [list of invalid slopes] }
    """
    checks = {
        'BldLoads.sum  SUM_DEL_SLOPES'  : get_blade_config().get('SUM_DEL_SLOPES', []),
        'HubLoads.sum  HUB_DEL_SLOPES'  : get_hub_config().get('del_slopes', []),
        'TwrLoads.sum  TWR_DEL_SLOPES'  : get_tower_config().get('del_slopes', []),
        'YawLoads.sum  YAW_DEL_SLOPES'  : get_yaw_config().get('del_slopes', []),
    }
    mismatches = {}
    for section, slopes in checks.items():
        invalid = [m for m in slopes
                   if float(m) not in [float(v) for v in RFC_M_VALUES]]
        if invalid:
            mismatches[section] = invalid
            msg = (f'{section}: m={invalid} not in RFC_M_VALUES={RFC_M_VALUES} '
                   f'— these slopes will show N/A in .sum file')
            if log:
                log.warning(msg, tag='Validate')
            else:
                print(f'  WARNING: {msg}')

    # Pitch bearing RFC/LDD slopes
    ptb_cfg = get_pitch_bearing_config()
    for section, slopes, master in [
        ('PitchBearing.sum  PTB_RFC_SLOPES', ptb_cfg['rfc_slopes'], RFC_M_VALUES),
        ('PitchBearing.sum  PTB_LDD_SLOPES', ptb_cfg['ldd_slopes'], LDD_M_VALUES),
    ]:
        invalid = [m for m in slopes
                   if float(m) not in [float(v) for v in master]]
        if invalid:
            mismatches[section] = invalid
            msg = (f'{section}: m={invalid} not in M_VALUES — '
                   f'N/A will be written in .sum file')
            if log:
                log.warning(msg, tag='Validate')
            else:
                print(f'  WARNING: {msg}')

    # Foundation RFC/LDD slopes
    fnd_cfg = get_foundation_config()
    for section, slopes, master in [
        ('FNDLoads.sum  FND_RFC_SLOPES', fnd_cfg['rfc_slopes'], RFC_M_VALUES),
        ('FNDLoads.sum  FND_LDD_SLOPES', fnd_cfg['ldd_slopes'], LDD_M_VALUES),
    ]:
        invalid = [m for m in slopes
                   if float(m) not in [float(v) for v in master]]
        if invalid:
            mismatches[section] = invalid
            msg = (f'{section}: m={invalid} not in M_VALUES — '
                   f'N/A will be written in .sum file')
            if log:
                log.warning(msg, tag='Validate')
            else:
                print(f'  WARNING: {msg}')

    # Drivetrain LDD/LRD/HSS slopes
    drt_cfg = get_drivetrain_config()
    for section, slopes, master in [
        ('DRTLoads.sum  DRT_LDD_SLOPES',     drt_cfg['ldd_slopes'],     LDD_M_VALUES),
        ('DRTLoads.sum  DRT_LRD_SLOPES',     drt_cfg['lrd_slopes'],     LRD_M_VALUES),
        ('DRTLoads.sum  DRT_HSS_DEL_SLOPES', drt_cfg['hss_del_slopes'], RFC_M_VALUES),
    ]:
        invalid = [m for m in slopes
                   if float(m) not in [float(v) for v in master]]
        if invalid:
            mismatches[section] = invalid
            msg = (f'{section}: m={invalid} not in M_VALUES — '
                   f'N/A will be written in .sum file')
            if log:
                log.warning(msg, tag='Validate')
            else:
                print(f'  WARNING: {msg}')

    if not mismatches and log:
        log.info('All SUM DEL slopes validated — subset of RFC/LDD/LRD_M_VALUES',
                 tag='Validate')
    return mismatches


def get_drivetrain_config():
    """
    Extract drivetrain summary configuration from COMPONENT_CONFIG [DRIVETRAIN].

    Returns dict:
    {
      'del_slopes'       : list of float  — RFC slopes for Table 1B
      'ldd_slopes'       : list of float  — LDD slopes for Table 1C
      'lrd_slopes'       : list of float  — LRD slopes for Table 1C
      'ldd_lrd_sensors'  : list of str    — full sensor names for Table 1C
      'a_frame'          : dict {label: sensor_name}  — non-rotating sensors
      's_frame'          : dict {label: sensor_name}  — rotating sensors
    }
    """
    drt = COMPONENT_CONFIG.get('DRIVETRAIN', {})

    def _parse_slopes(key, default):
        val = drt.get(key, '').strip()
        if not val:
            return list(default)
        try:
            return [float(x) for x in val.split()]
        except ValueError:
            return list(default)

    def _get(key):
        return drt.get(key, '').strip() or None

    del_slopes = _parse_slopes('DRT_DEL_SLOPES', [4.0, 8.0])
    ldd_slopes = _parse_slopes('DRT_LDD_SLOPES', [3.3, 6.7])
    lrd_slopes = _parse_slopes('DRT_LRD_SLOPES', [3.3, 6.7])

    # Full sensor names for Table 1C — space-separated
    ldd_lrd_sensors = drt.get('DRT_LDD_LRD_SENSORS', '').split()

    # Non-rotating (a) frame sensors
    a_frame = {
        'Mres': _get('DRT_Mres_a'),
        'Mx'  : _get('DRT_Mx_a'),
        'My'  : _get('DRT_My_a'),
        'Mz'  : _get('DRT_Mz_a'),
        'Fx'  : _get('DRT_Fx_a'),
        'Fy'  : _get('DRT_Fy_a'),
        'Fz'  : _get('DRT_Fz_a'),
    }

    # Rotating (s) frame sensors
    s_frame = {
        'Mres': _get('DRT_Mres_s'),
        'Mx'  : _get('DRT_Mx_s'),
        'My'  : _get('DRT_My_s'),
        'Mz'  : _get('DRT_Mz_s'),
        'Fx'  : _get('DRT_Fx_s'),
        'Fy'  : _get('DRT_Fy_s'),
        'Fz'  : _get('DRT_Fz_s'),
    }

    hss_del_slopes = _parse_slopes('DRT_HSS_DEL_SLOPES', [4.0, 8.0])
    hss = {
        'Tq' : drt.get('DRT_HSS_Tq',  '').strip() or None,
        'Pwr': drt.get('DRT_HSS_Pwr', '').strip() or None,
        'V'  : drt.get('DRT_HSS_V',   '').strip() or None,
    }

    return {
        'del_slopes'      : del_slopes,
        'ldd_slopes'      : ldd_slopes,
        'lrd_slopes'      : lrd_slopes,
        'ldd_lrd_sensors' : ldd_lrd_sensors,
        'a_frame'         : a_frame,
        's_frame'         : s_frame,
        'hss_del_slopes'  : hss_del_slopes,
        'hss'             : hss,
    }


def get_foundation_config():
    """
    Extract foundation summary configuration from COMPONENT_CONFIG [FOUNDATION].

    Returns dict:
    {
      'reference'   : str   — reference elevation label
      'sensors'     : dict  {label: sensor_name} Mres/Mx/My/Mz/Fx/Fy/Fz
      'rfc_slopes'  : list of float — RFC DEL slopes for Table 2A
      'ldd_slopes'  : list of float — LDD DEL slopes for Table 2B
      'ldd_sensors' : list of str   — full sensor names for Table 2B
    }
    """
    fnd = COMPONENT_CONFIG.get('FOUNDATION', {})

    def _parse_slopes(key, default):
        val = fnd.get(key, '').strip()
        if not val:
            return list(default)
        try:
            return [float(x) for x in val.split()]
        except ValueError:
            return list(default)

    def _get(key):
        return fnd.get(key, '').strip() or None

    sensors = {
        'Mres': _get('FND_Mres'),
        'Mx'  : _get('FND_Mx'),
        'My'  : _get('FND_My'),
        'Mz'  : _get('FND_Mz'),
        'Fx'  : _get('FND_Fx'),
        'Fy'  : _get('FND_Fy'),
        'Fz'  : _get('FND_Fz'),
    }

    ldd_sensors = fnd.get('FND_LDD_SENSORS', '').split()

    return {
        'reference'  : fnd.get('FND_REFERENCE', 'Tower base').strip(),
        'sensors'    : sensors,
        'rfc_slopes' : _parse_slopes('FND_RFC_SLOPES', [3.0, 5.0]),
        'ldd_slopes' : _parse_slopes('FND_LDD_SLOPES', [3.0, 5.0]),
        'ldd_sensors': ldd_sensors,
    }


def get_pitch_bearing_config():
    """
    Extract pitch bearing configuration from COMPONENT_CONFIG [PITCH_BEARING].

    Returns dict:
    {
      'rfc_slopes' : list of float  — RFC slopes (Hertzian, e.g. [3.3])
      'ldd_slopes' : list of float  — LDD slopes (Hertzian, e.g. [3.3])
      'blades'     : dict {B1: {comp: sensor}, B2: ..., B3: ...}
      'components' : list ['Mres','Mx','My','Mz','Fx','Fy','Fz']
    }
    """
    ptb   = COMPONENT_CONFIG.get('PITCH_BEARING', {})
    comps = ['Mres', 'Mx', 'My', 'Mz', 'Fx', 'Fy', 'Fz']

    def _parse_slopes(key, default):
        val = ptb.get(key, '').strip()
        if not val:
            return list(default)
        try:
            return [float(x) for x in val.split()]
        except ValueError:
            return list(default)

    blades = {}
    for b in range(1, 4):
        blade_sensors = {}
        for comp in comps:
            key = f'PTB_{comp}_B{b}'
            blade_sensors[comp] = ptb.get(key, '').strip() or None
        blades[f'B{b}'] = blade_sensors

    return {
        'rfc_slopes': _parse_slopes('PTB_RFC_SLOPES', [3.3]),
        'ldd_slopes': _parse_slopes('PTB_LDD_SLOPES', [3.3]),
        'blades'    : blades,
        'components': comps,
    }


def get_tower_clearance_config():
    """
    Extract tower clearance configuration from COMPONENT_CONFIG [TOWER_CLEARANCE].

    Returns dict:
    {
      'clrnc'  : dict {B1, B2, B3} → TwrClrnc sensor names
      'oop'    : dict {B1, B2, B3} → OoPDefl sensor names
    }
    """
    clr = COMPONENT_CONFIG.get('TOWER_CLEARANCE', {})

    def _get(key):
        return clr.get(key, '').strip() or None

    return {
        'clrnc': {
            'B1': _get('CLR_TwrClrnc_B1'),
            'B2': _get('CLR_TwrClrnc_B2'),
            'B3': _get('CLR_TwrClrnc_B3'),
        },
        'oop': {
            'B1': _get('CLR_OoPDefl_B1'),
            'B2': _get('CLR_OoPDefl_B2'),
            'B3': _get('CLR_OoPDefl_B3'),
        },
    }


def get_sensor_flags(sensor_name):
    """
    Return flags dict for a sensor. If not in SENSOR_LIST, return defaults:
    STA=1, 1HzEq=1, all others=0.
    """
    return SENSOR_LIST.get(sensor_name, {
        'Group': 0, 'STA': 1, '1HzEq': 1,
        'EXT': 0, 'RFC': 0, 'Markov': 0,
        'LRD': 0, 'LDD': 0, 'Complimentary': 0,
        'CompMethod': 2
    })
