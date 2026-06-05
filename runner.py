"""
runner.py
Master pipeline orchestrator. Synchronizes the split configuration files 
into a unified sensorList.txt and immediately executes main.py.
"""

import os
import sys
import subprocess

# Automatically import the active sensor list path configuration 
try:
    import config
    path = getattr(config, "SENSOR_LIST_PATH", None)
except ImportError:
    print("CRITICAL ERROR: Could not import 'config.py'. Ensure runner.py is in the correct directory.")
    sys.exit(1)


def execution_pipeline():
    print("=" * 80)
    print("STAGE 1: Aggregating split text modules into unified sensorList.txt...")
    print("=" * 80)
    
    # 1. Execute the configuration assembly script
    try:
        subprocess.run(
            ["python", "manage_sensor_list.py", "--merge"], 
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"\nCRITICAL ERROR: Failed to aggregate sensor files. Build aborted. {e}")
        sys.exit(1)
    except FileNotFoundError:
        print("\nCRITICAL ERROR: 'manage_sensor_list.py' script was not found in this folder.")
        sys.exit(1)

    # 2. Safety Guard Verification Check
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(
            f"sensorList.txt not found:\n  {path}\n"
            f"Please set SENSOR_LIST_PATH in config.py to the correct location."
        )

    print("\n" + "=" * 80)
    print("STAGE 2: Verification complete. Triggering OpenFAST Postprocessing (main.py)...")
    print("=" * 80)

    # 3. Seamlessly spin up the core data processing pipeline
    try:
        subprocess.run(
            ["python", "main.py"], 
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"\nCRITICAL ERROR: Main pipeline run encountered failures. {e}")
        sys.exit(1)

    print("\n" + "=" * 80)
    print("PROCESS COMPLETE: All stages finished successfully.")
    print("=" * 80)


if __name__ == "__main__":
    execution_pipeline()