# =============================================================================
# io_reader.py — OpenFAST File Reader
# =============================================================================
# Reads .outb (binary) and .out (ASCII) OpenFAST output files using
# openfast_toolbox and returns a pandas DataFrame.

import os
import pandas as pd


def read_fast_file(filepath):
    """
    Read an OpenFAST output file (.outb or .out) and return a pandas DataFrame.

    Parameters
    ----------
    filepath : str
        Full path to the .outb or .out file.

    Returns
    -------
    pd.DataFrame
        DataFrame with sensor names (including units) as column headers.
        e.g. 'Time_[s]', 'RotSpeed_[rpm]', 'RootMyc1_[kN-m]', ...
    """
    from openfast_toolbox.io import FASTOutputFile

    ext = os.path.splitext(filepath)[1].lower()
    if ext not in ['.outb', '.out']:
        raise ValueError(f"Unsupported file extension '{ext}' for file: {filepath}")

    try:
        df = FASTOutputFile(filepath).toDataFrame()
        df = df.loc[:, ~df.columns.duplicated(keep='first')]
    except Exception as e:
        raise IOError(f"Failed to read file '{filepath}': {e}")

    return df


def get_all_files(folder):
    """
    Scan a folder and return sorted list of full paths to all .outb and .out files.

    Parameters
    ----------
    folder : str
        Path to the input folder.

    Returns
    -------
    list of str
        Sorted list of full file paths.
    """
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"Input folder not found: {folder}")

    files = []
    for fname in sorted(os.listdir(folder)):
        if fname.lower().endswith('.outb') or fname.lower().endswith('.out'):
            files.append(os.path.join(folder, fname))

    return files


def get_sensor_names(df, time_col='Time_[s]'):
    """
    Return list of sensor channel names from DataFrame, excluding the time column.

    Parameters
    ----------
    df : pd.DataFrame
    time_col : str

    Returns
    -------
    list of str
    """
    return [c for c in df.columns if c != time_col]


def get_simulation_duration(df, time_col='Time_[s]'):
    """
    Return simulation duration in seconds from the time channel.

    Parameters
    ----------
    df : pd.DataFrame
    time_col : str

    Returns
    -------
    float
        Duration = last_time - first_time
    """
    time = df[time_col].values
    return float(time[-1] - time[0])


# =============================================================================
# Sensor name → safe filename helper
# =============================================================================

# def sanitize_for_filename(sensor_name):
#     """
#     Convert an OpenFAST sensor name to a safe filename.

#     OpenFAST channel names contain bracket characters (e.g. 'RootMxc1_[kN-m]')
#     that are unsafe on Windows filesystems. This helper replaces all
#     OS-unsafe characters with underscores and strips trailing underscores.

#     Examples
#     --------
#     >>> sanitize_for_filename('RootMxc1_[kN-m]')
#     'RootMxc1_kN-m'
#     >>> sanitize_for_filename('Mres_TwrBs_[kN-m]')
#     'Mres_TwrBs_kN-m'
#     >>> sanitize_for_filename('TwrBsFxt_[kN]')
#     'TwrBsFxt_kN'

#     Parameters
#     ----------
#     sensor_name : str
#         Raw OpenFAST channel name (e.g. 'RootMxc1_[kN-m]').

#     Returns
#     -------
#     str
#         Safe filename with brackets and slashes replaced.
#     """
#     if sensor_name is None:
#         return ''
#     # Characters not safe in Windows or Unix filenames:
#     # < > : " / \ | ? * and brackets
#     unsafe = '<>:"/\\|?*[]'
#     out = ''.join('_' if c in unsafe else c for c in str(sensor_name))
#     # Collapse runs of underscores
#     while '__' in out:
#         out = out.replace('__', '_')
#     # Strip trailing underscores
#     return out.rstrip('_')

def sanitize_for_filename(sensor_name):
    """
    Convert an OpenFAST sensor name to a safe filename.
 
    OpenFAST channel names contain bracket characters (e.g. 'RootMxc1_[kN-m]')
    that are unsafe on Windows filesystems. This helper replaces all
    OS-unsafe characters with underscores and strips trailing underscores.
 
    Examples
    --------
    >>> sanitize_for_filename('RootMxc1_[kN-m]')
    'RootMxc1_kN-m'
    >>> sanitize_for_filename('Mres_TwrBs_[kN-m]')
    'Mres_TwrBs_kN-m'
    >>> sanitize_for_filename('TwrBsFxt_[kN]')
    'TwrBsFxt_kN'
 
    Parameters
    ----------
    sensor_name : str
        Raw OpenFAST channel name (e.g. 'RootMxc1_[kN-m]').
 
    Returns
    -------
    str
        Safe filename with brackets and slashes replaced.
    """
    # if sensor_name is None:
    #     return ''
    # # Characters not safe in Windows or Unix filenames:
    # # < > : " / \ | ? * and brackets
    # unsafe = '<>:"/\\|?*[]'
    # out = ''.join('_' if c in unsafe else c for c in str(sensor_name))
    # # Collapse runs of underscores
    # while '__' in out:
    #     out = out.replace('__', '_')
    # # Strip trailing underscores
    # return out.rstrip('_')
 
    if sensor_name is None:
        return ''
 
    # 1. Handle illegal characters (< > : " / \ | ? *) by replacing with '_'
    unsafe_chars = '<>:"/\\|?*'
    out = ''.join('_' if c in unsafe_chars else c for c in str(sensor_name))
   
    # 2. REMOVE brackets instead of replacing them with underscores.
    # This prevents the "_[" from becoming "__"
    out = out.replace('[', '').replace(']', '')
   
    # 3. Clean up: collapse any accidental double underscores to single
    while '__' in out:
        out = out.replace('__', '_')
       
    # 4. Remove any underscore at the very end (the one created by ']')
    return out.strip('_')

# =============================================================================
# Backward-compatible aliases — main.py and other modules use these names
# =============================================================================
# The original function names use the convention read_fast_file, get_all_files,
# get_sensor_names. main.py / fatigue_stats.py / extreme_stats.py import the
# alternative naming convention read_fast_output, get_all_output_files,
# get_sensor_columns. Both naming conventions are supported via these aliases.

read_fast_output      = read_fast_file
get_all_output_files  = get_all_files
get_sensor_columns    = get_sensor_names
