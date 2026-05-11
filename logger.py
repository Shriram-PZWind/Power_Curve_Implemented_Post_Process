# =============================================================================
# logger.py — Post-Processing Pipeline Logger
# =============================================================================
#
# Writes a structured log file (postprocess.log) to the output root folder.
# Every pipeline event is timestamped and categorised.
#
# Usage:
#   from logger import PostProcessLogger
#   log = PostProcessLogger(log_path)
#   log.phase_start(1, "Reading sensor list")
#   log.info("87 sensor channels found")
#   log.warning("Mres_TwHt6 skipped — component sensor not found")
#   log.phase_complete(1, "87 real + 17 derived sensors active")
#   log.file_written("FAT/RootMxb1_kN-m.rfc")
#   log.close()
#
# =============================================================================

import os
import time
from datetime import datetime


class PostProcessLogger:
    """
    Structured logger for the OpenFAST post-processing pipeline.

    Writes timestamped entries to a log file.
    Also mirrors all entries to the console.

    Log levels:
      INFO    — normal progress events
      WARNING — non-fatal issues (missing sensors, slope mismatches, N/A values)
      ERROR   — fatal errors that stop processing
    """

    # Width constants for aligned output
    _W_TIME  = 10   # [HH:MM:SS]
    _W_LEVEL = 9    # [INFO   ] / [WARNING] / [ERROR  ]
    _W_TAG   = 10   # Phase 0 / Phase 11 / SUM / FAT etc.

    def __init__(self, log_path, output_folder='', echo_to_console=True):
        """
        Parameters
        ----------
        log_path        : str  — full path to postprocess.log
        output_folder   : str  — output root folder (shown in header)
        echo_to_console : bool — whether to also print to stdout
        """
        self._path    = log_path
        self._echo    = echo_to_console
        self._start   = time.time()
        self._counts  = {'INFO': 0, 'WARNING': 0, 'ERROR': 0}
        self._fh      = open(log_path, 'w', encoding='utf-8')
        self._write_header(output_folder)

    # ── Public API ────────────────────────────────────────────────────────────

    def phase_start(self, phase_num, description):
        """Log the start of a pipeline phase."""
        tag = f'Phase {phase_num:<2}'
        self._write('INFO', tag, f'Started  — {description}')

    def phase_complete(self, phase_num, summary=''):
        """Log successful completion of a pipeline phase."""
        tag = f'Phase {phase_num:<2}'
        msg = f'Completed'
        if summary:
            msg += f' — {summary}'
        self._write('INFO', tag, msg)

    def info(self, message, tag='Pipeline'):
        """Log an informational message."""
        self._write('INFO', tag, message)

    def file_written(self, relative_path, tag=None):
        """Log a file write event."""
        if tag is None:
            # Infer tag from folder prefix
            parts = relative_path.replace('\\', '/').split('/')
            tag = parts[0] if len(parts) > 1 else 'Output'
        self._write('INFO', tag, f'Written  — {relative_path}')

    def warning(self, message, tag='Warning'):
        """Log a non-fatal warning."""
        self._write('WARNING', tag, message)

    def error(self, message, tag='ERROR'):
        """Log a fatal error."""
        self._write('ERROR', tag, message)

    def slope_mismatch(self, sensor_name, m_value, fat_file):
        """Log a DEL slope mismatch — m not found in FAT file."""
        self.warning(
            f'm={m_value} not found in {fat_file} — N/A written in .sum file',
            tag='SUM')

    def sensor_skipped(self, sensor_name, reason, tag='Derived'):
        """Log a skipped sensor."""
        self.warning(f'{sensor_name} skipped — {reason}', tag=tag)

    def close(self, success=True):
        """Write footer and close log file."""
        elapsed = time.time() - self._start
        h = int(elapsed // 3600)
        m = int((elapsed % 3600) // 60)
        s = int(elapsed % 60)
        elapsed_str = f'{h}h {m:02d}m {s:02d}s' if h else f'{m}m {s:02d}s'

        status = 'Completed successfully' if success else 'FAILED'
        self._write('INFO', 'Pipeline', f'{status} — elapsed {elapsed_str}')
        self._write_summary()
        self._write_footer()
        self._fh.close()

    # ── Private helpers ────────────────────────────────────────────────────────

    def _write(self, level, tag, message):
        """Write a single log entry."""
        ts    = datetime.now().strftime('%H:%M:%S')
        level_str = f'[{level:<7}]'
        tag_str   = f'{tag:<10}'
        line  = f'[{ts}] {level_str} {tag_str} {message}'
        self._fh.write(line + '\n')
        self._fh.flush()
        self._counts[level] = self._counts.get(level, 0) + 1
        if self._echo:
            print(line)

    def _write_header(self, output_folder):
        """Write the log file header."""
        sep = '=' * 80
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        lines = [
            sep,
            ' OpenFAST Post-Processing Pipeline — Log File',
            f' Started  : {now}',
            f' Output   : {output_folder}' if output_folder else '',
            sep,
            '',
        ]
        for line in lines:
            if line:
                self._fh.write(line + '\n')
        self._fh.flush()

    def _write_summary(self):
        """Write warning/error summary before footer."""
        self._fh.write('\n' + '-' * 80 + '\n')
        self._fh.write(' Summary\n')
        self._fh.write(f'  INFO    entries : {self._counts.get("INFO",    0)}\n')
        self._fh.write(f'  WARNING entries : {self._counts.get("WARNING", 0)}\n')
        self._fh.write(f'  ERROR   entries : {self._counts.get("ERROR",   0)}\n')
        self._fh.write('-' * 80 + '\n')
        self._fh.flush()

    def _write_footer(self):
        self._fh.write('=' * 80 + '\n')
        self._fh.flush()
