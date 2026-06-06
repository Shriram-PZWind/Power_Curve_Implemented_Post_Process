# """
# runner.py
# Master pipeline orchestrator. Synchronizes the split configuration files 
# into a unified sensorList.txt and immediately executes main.py.
# """

# import os
# import sys
# import subprocess

# # Automatically import the active sensor list path configuration 
# try:
#     import config
#     path = getattr(config, "SENSOR_LIST_PATH", None)
# except ImportError:
#     print("CRITICAL ERROR: Could not import 'config.py'. Ensure runner.py is in the correct directory.")
#     sys.exit(1)


# def execution_pipeline():
#     print("=" * 80)
#     print("STAGE 1: Aggregating split text modules into unified sensorList.txt...")
#     print("=" * 80)
    
#     # 1. Execute the configuration assembly script
#     try:
#         subprocess.run(
#             ["python", "manage_sensor_list.py", "--merge"], 
#             check=True
#         )
#     except subprocess.CalledProcessError as e:
#         print(f"\nCRITICAL ERROR: Failed to aggregate sensor files. Build aborted. {e}")
#         sys.exit(1)
#     except FileNotFoundError:
#         print("\nCRITICAL ERROR: 'manage_sensor_list.py' script was not found in this folder.")
#         sys.exit(1)

#     # 2. Safety Guard Verification Check
#     if not path or not os.path.isfile(path):
#         raise FileNotFoundError(
#             f"sensorList.txt not found:\n  {path}\n"
#             f"Please set SENSOR_LIST_PATH in config.py to the correct location."
#         )

#     print("\n" + "=" * 80)
#     print("STAGE 2: Verification complete. Triggering OpenFAST Postprocessing (main.py)...")
#     print("=" * 80)

#     # 3. Seamlessly spin up the core data processing pipeline
#     try:
#         subprocess.run(
#             ["python", "main.py"], 
#             check=True
#         )
#     except subprocess.CalledProcessError as e:
#         print(f"\nCRITICAL ERROR: Main pipeline run encountered failures. {e}")
#         sys.exit(1)

#     print("\n" + "=" * 80)
#     print("PROCESS COMPLETE: All stages finished successfully.")
#     print("=" * 80)


# if __name__ == "__main__":
#     execution_pipeline()

# -------------------------------------------------------------------------------------------------------------------

# """
# runner.py
# Master pipeline orchestrator executable entry-point.
# Fixes Windows multiprocessing pickling errors by preserving module namespaces 
# and enforcing PyInstaller freeze support.
# """
# import os
# import sys
# import re
# import multiprocessing

# def parse_paths_txt(file_path):
#     """Parses paths.txt securely, handling spaces, casing, and quotes."""
#     paths = {}
#     if not os.path.exists(file_path):
#         return paths
#     with open(file_path, 'r', encoding='utf-8') as f:
#         for line in f:
#             line = line.strip()
#             if not line or line.startswith('#'): continue
#             if '=' in line:
#                 key, val = line.split('=', 1)
#                 # Normalize key: remove spaces/special chars, convert to lowercase
#                 key = re.sub(r'[^a-zA-Z]', '', key).lower()
#                 # Clean up value: strip whitespace, single quotes, and double quotes
#                 val = val.strip().strip('"').strip("'")
#                 paths[key] = val
#     return paths

# def execution_pipeline():
#     print("=" * 80)
#     print("STAGE 0: Parsing Environment & User Constraints...")
#     print("=" * 80)
    
#     # 1. Terminal Input Validation
#     if len(sys.argv) < 2:
#         print("\n[CRITICAL ERROR] Target folder path parameter is missing.")
#         print("Usage in terminal: your_pipeline.exe <PATH_TO_FOLDER>")
#         sys.exit(1)
        
#     user_folder = os.path.abspath(sys.argv[1])
#     if not os.path.isdir(user_folder):
#         print(f"\n[CRITICAL ERROR] Provided folder does not exist:\n  {user_folder}")
#         sys.exit(1)
        
#     # 2. Extract paths.txt
#     paths_file = os.path.join(user_folder, "paths.txt")
#     if not os.path.isfile(paths_file):
#         print(f"\n[CRITICAL ERROR] Missing 'paths.txt' inside configuration folder:\n  {paths_file}")
#         sys.exit(1)

#     parsed_paths = parse_paths_txt(paths_file)
    
#     in_folder = parsed_paths.get('inputfolder', '')
#     out_folder = parsed_paths.get('outputfolder', '')
#     lc_path = parsed_paths.get('lcpostproc', '')
#     sensor_path_from_txt = parsed_paths.get('sensorlist', '')
    
#     # 3. Strict Pre-Validation of Hardcoded External Paths
#     validation_errors = []
    
#     if not in_folder:
#         validation_errors.append("- 'input folder = ...' is missing or undefined in paths.txt")
#     elif not os.path.isdir(os.path.abspath(in_folder)):
#         validation_errors.append(f"- Input folder path specified does not exist:\n  {in_folder}")
        
#     if not out_folder:
#         validation_errors.append("- 'output folder = ...' is missing or undefined in paths.txt")
        
#     if not lc_path:
#         validation_errors.append("- 'lcpostproc = ...' path is missing or undefined in paths.txt")
#     elif not os.path.isfile(os.path.abspath(lc_path)):
#         validation_errors.append(f"- LC PostProcess text file specified does not exist:\n  {lc_path}")

#     if validation_errors:
#         print("\n[ERROR] Configuration Verification Failed! Please fix your 'paths.txt' file:")
#         for error in validation_errors:
#             print(error)
#         print("\nExecution stopped. Please correct the paths and re-run the executable.")
#         sys.exit(1)
        
#     # 4. Resolve sensorList.txt logic (Cases 1, 2, and 3)
#     fallback_sensor_path = os.path.join(user_folder, "sensorList.txt")
#     parts_dir = os.path.join(user_folder, "sensor_list_parts")
    
#     final_sensor_path = fallback_sensor_path  # Default to Case 1/2 inside the folder
    
#     if sensor_path_from_txt:
#         # User defined a path in paths.txt
#         if os.path.exists(os.path.abspath(sensor_path_from_txt)):
#             # Case 3: File exists at provided path -> Use it and overwrite via merge later
#             final_sensor_path = os.path.abspath(sensor_path_from_txt)
#         else:
#             # Path provided but file DOES NOT exist -> Check with user
#             print(f"\n[WARNING] Sensor list path was provided in paths.txt but the file was not found:")
#             print(f"  {sensor_path_from_txt}")
#             ans = input("Should we proceed based on the folder input (create/overwrite there)? (y/n): ")
#             if ans.strip().lower() == 'y':
#                 final_sensor_path = fallback_sensor_path
#             else:
#                 print("Process aborted by user.")
#                 sys.exit(0)
                
#     # 5. Inject validated global variables into os.environ
#     os.environ["OPENFAST_INPUT_FOLDER"] = os.path.abspath(in_folder)
#     os.environ["OPENFAST_OUTPUT_FOLDER"] = os.path.abspath(out_folder)
#     os.environ["OPENFAST_LC_PATH"] = os.path.abspath(lc_path)
#     os.environ["OPENFAST_SENSOR_LIST_PATH"] = os.path.abspath(final_sensor_path)

#     # Verify parts folder exists before executing merge
#     if not os.path.isdir(parts_dir):
#         print(f"\n[CRITICAL ERROR] 'sensor_list_parts' folder not found inside:\n  {user_folder}")
#         sys.exit(1)

#     print("\n" + "=" * 80)
#     print("STAGE 1: Merging split text modules into unified sensorList.txt...")
#     print(f"Target: {os.path.abspath(final_sensor_path)}")
#     print("=" * 80)
    
#     # Import and run merge utility BEFORE config/main are ever touched or imported!
#     import manage_sensor_list
#     manage_sensor_list.merge_sensor_list(os.path.abspath(final_sensor_path), parts_dir)
    
#     print("\n" + "=" * 80)
#     print("STAGE 2: Verification complete. Triggering OpenFAST Postprocessing...")
#     print("=" * 80)
    
#     # Execute main.py via native module import. This protects the pickling scope namespace
#     # required by the multiprocessing pooling framework inside compiled executables.
#     try:
#         import main
#         main.main()
#     except Exception as e:
#         print(f"\n[CRITICAL ERROR] Main postprocessing pipeline encountered an exception:\n  {e}")
#         sys.exit(1)

#     print("\n" + "=" * 80)
#     print("PROCESS COMPLETE: All post-processing loops finished successfully.")
#     print("=" * 80)

# if __name__ == "__main__":
#     # Crucial for PyInstaller single-file executables utilizing multiprocessing loops on Windows
#     multiprocessing.freeze_support()
#     execution_pipeline()

# -------------------------------------------------------------------------------------------------------------
# RUNNCER CODE WITHOUT TIME AT END BELOW CODE IS WITH TIME AT END

# -------------------------------------------------------------------------------------------------------------

"""
runner.py
Master pipeline orchestrator executable entry-point.
Tracks complete execution performance timing and safely handles multiprocessing 
pickling scopes on Windows environments.
"""
import os
import sys
import re
import time
import multiprocessing

def parse_paths_txt(file_path):
    """Parses paths.txt securely, handling spaces, casing, and quotes."""
    paths = {}
    if not os.path.exists(file_path):
        return paths
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'): continue
            if '=' in line:
                key, val = line.split('=', 1)
                # Normalize key: remove spaces/special chars, convert to lowercase
                key = re.sub(r'[^a-zA-Z]', '', key).lower()
                # Clean up value: strip whitespace, single quotes, and double quotes
                val = val.strip().strip('"').strip("'")
                paths[key] = val
    return paths

def execution_pipeline():
    # Start the high-precision master stopwatch
    start_perf_time = time.perf_counter()

    # print("=" * 80)
    # print("STAGE 0: Parsing Environment & User Constraints...")
    # print("=" * 80)
    
    # 1. Terminal Input Validation
    if len(sys.argv) < 2:
        print("\n[CRITICAL ERROR] Target folder path parameter is missing.")
        print("Usage in terminal: your_pipeline.exe <PATH_TO_FOLDER>")
        sys.exit(1)
        
    user_folder = os.path.abspath(sys.argv[1])
    if not os.path.isdir(user_folder):
        print(f"\n[CRITICAL ERROR] Provided folder does not exist:\n  {user_folder}")
        sys.exit(1)
        
    # 2. Extract paths.txt
    paths_file = os.path.join(user_folder, "paths.txt")
    if not os.path.isfile(paths_file):
        print(f"\n[CRITICAL ERROR] Missing 'paths.txt' inside configuration folder:\n  {paths_file}")
        sys.exit(1)

    parsed_paths = parse_paths_txt(paths_file)
    
    in_folder = parsed_paths.get('inputfolder', '')
    out_folder = parsed_paths.get('outputfolder', '')
    lc_path = parsed_paths.get('lcpostproc', '')
    sensor_path_from_txt = parsed_paths.get('sensorlist', '')
    
    # 3. Strict Pre-Validation of Hardcoded External Paths
    validation_errors = []
    
    if not in_folder:
        validation_errors.append("- 'input folder = ...' is missing or undefined in paths.txt")
    elif not os.path.isdir(os.path.abspath(in_folder)):
        validation_errors.append(f"- Input folder path specified does not exist:\n  {in_folder}")
        
    if not out_folder:
        validation_errors.append("- 'output folder = ...' is missing or undefined in paths.txt")
        
    if not lc_path:
        validation_errors.append("- 'lcpostproc = ...' path is missing or undefined in paths.txt")
    elif not os.path.isfile(os.path.abspath(lc_path)):
        validation_errors.append(f"- LC PostProcess text file specified does not exist:\n  {lc_path}")

    if validation_errors:
        print("\n[ERROR] Configuration Verification Failed! Please fix your 'paths.txt' file:")
        for error in validation_errors:
            print(error)
        print("\nExecution stopped. Please correct the paths and re-run the executable.")
        sys.exit(1)
        
    # 4. Resolve sensorList.txt logic (Cases 1, 2, and 3)
    fallback_sensor_path = os.path.join(user_folder, "sensorList.txt")
    # parts_dir = os.path.join(user_folder, "sensor_list_parts")
    
    final_sensor_path = fallback_sensor_path  # Default to Case 1/2 inside the folder
    
    if sensor_path_from_txt:
        # User defined a path in paths.txt
        if os.path.exists(os.path.abspath(sensor_path_from_txt)):
            # Case 3: File exists at provided path -> Use it and overwrite via merge later
            final_sensor_path = os.path.abspath(sensor_path_from_txt)
        else:
            # Path provided but file DOES NOT exist -> Check with user
            print(f"\n[WARNING] Sensor list path was provided in paths.txt but the file was not found:")
            print(f"  {sensor_path_from_txt}")
            ans = input("Should we proceed based on the folder input (create/overwrite there)? (y/n): ")
            if ans.strip().lower() == 'y':
                final_sensor_path = fallback_sensor_path
            else:
                print("Process aborted by user.")
                sys.exit(0)
                
    # 5. Inject validated global variables into os.environ
    os.environ["OPENFAST_INPUT_FOLDER"] = os.path.abspath(in_folder)
    os.environ["OPENFAST_OUTPUT_FOLDER"] = os.path.abspath(out_folder)
    os.environ["OPENFAST_LC_PATH"] = os.path.abspath(lc_path)
    os.environ["OPENFAST_SENSOR_LIST_PATH"] = os.path.abspath(final_sensor_path)

    # Verify parts folder exists before executing merge
    # if not os.path.isdir(parts_dir):
    #     print(f"\n[CRITICAL ERROR] 'sensor_list_parts' folder not found inside:\n  {user_folder}")
    #     sys.exit(1)

    # print("\n" + "=" * 80)
    # print("STAGE 1: Merging split text modules into unified sensorList.txt...")
    # print(f"Target: {os.path.abspath(final_sensor_path)}")
    # print("=" * 80)
    
    # Import and run merge utility BEFORE config/main are ever touched or imported!
    import manage_sensor_list
    manage_sensor_list.merge_sensor_list(os.path.abspath(final_sensor_path), user_folder)
    
    # print("\n" + "=" * 80)
    # print("STAGE 2: Verification complete. Triggering OpenFAST Postprocessing...")
    # print("=" * 80)
    
    # Execute main.py via native module import.
    try:
        import main
        main.main()
    except Exception as e:
        print(f"\n[CRITICAL ERROR] Main postprocessing pipeline encountered an exception:\n  {e}")
        sys.exit(1)

    # Calculate final performance run metrics
    end_perf_time = time.perf_counter()
    total_seconds = end_perf_time - start_perf_time
    
    # Format seconds into Hours, Minutes, and Seconds cleanly
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = total_seconds % 60

    print("\n" + "=" * 80)
    print("PROCESS COMPLETE: All post-processing loops finished successfully.")
    if hours > 0:
        print(f"Total Execution Time: {hours}h {minutes}m {seconds:.2f}s")
    elif minutes > 0:
        print(f"Total Execution Time: {minutes}m {seconds:.2f}s")
    else:
        print(f"Total Execution Time: {seconds:.2f} seconds")
    print("=" * 80)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    execution_pipeline()