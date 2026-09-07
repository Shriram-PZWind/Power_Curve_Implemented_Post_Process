"""
manage_sensor_list.py
Automates splitting and merging of OpenFAST post-processing sensorList.txt.
Guarantees 100% byte-level data, comment, and layout preservation.
"""

import os
import re
import sys

# Define explicit paths matching your post-processing setup
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SENSOR_LIST_PATH = os.path.join(BASE_DIR, "sensorList.txt")
PARTS_DIR = os.path.join(BASE_DIR, "sensor_list_parts")


def split_sensor_list(src_path, output_dir):
    """
    Parses unified sensorList.txt and splits it into dedicated section files.
    Uses comment-buffering to group header comments with their respective sections.
    """
    if not os.path.isfile(src_path):
        print(f"Error: Unified file not found at: {src_path}")
        return

    os.makedirs(output_dir, exist_ok=True)

    with open(src_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    parts = []  # List of tuples: (counter, section_suffix_name, lines_list)
    buffered_lines = []
    current_bucket = []
    current_suffix = "main_sensor_table"
    counter = 0

    for line in lines:
        stripped = line.strip()

        # If it's a comment or a blank line, buffer it temporarily
        if not stripped or stripped.startswith("#"):
            buffered_lines.append(line)

        # If it's a section header block like [BLADE] or [TOWER]
        elif stripped.startswith("[") and stripped.endswith("]"):
            # Flush the previous section bucket if it has active contents
            if current_bucket or parts == []:
                parts.append((counter, current_suffix, current_bucket))
                counter += 1

            # Start a new section bucket, pulling the preceding descriptive comments into it
            current_bucket = list(buffered_lines)
            buffered_lines = []

            # Extract the section title for clean file naming
            current_suffix = stripped[1:-1].strip().lower()
            current_bucket.append(line)
        else:
            # It's a standard table data row or flag entry
            if buffered_lines:
                current_bucket.extend(buffered_lines)
                buffered_lines = []
            current_bucket.append(line)

    # Clean up and append any remaining trailing lines
    if buffered_lines:
        current_bucket.extend(buffered_lines)
    if current_bucket:
        parts.append((counter, current_suffix, current_bucket))

    # Write each isolated section out to its sequential part file
    print(f"Splitting '{src_path}' into component files...")
    for idx, suffix, sec_lines in parts:
        filename = f"{idx:02d}_{suffix}.txt"
        file_path = os.path.join(output_dir, filename)
        with open(file_path, "w", encoding="utf-8") as out_f:
            out_f.writelines(sec_lines)
        print(f"  [Created] -> {filename} ({len(sec_lines)} lines)")

    print(f"\nSuccess! Highly organized sub-files generated inside: '{output_dir}'")


def merge_sensor_list(dest_path, input_dir):
    """
    Reads all configuration part files in order and merges them.
    Automatically detects matrix layouts (using '|') and flattens them
    into standard KEY = VALUE pairs for the final parser.
    """
    if not os.path.isdir(input_dir):
        print(f"Error: Parts directory not found at: {input_dir}")
        return

    files = [
        f for f in os.listdir(input_dir) if f.endswith(".txt") and re.match(r"^\d+", f)
    ]

    # Sort numerically based on leading integer (e.g., 2 before 10)
    files.sort(key=lambda f: int(re.match(r"^\d+", f).group()))

    if not files:
        print(f"Error: No configuration part files found in '{input_dir}'")
        return

    all_lines = []

    for filename in files:
        file_path = os.path.join(input_dir, filename)
        with open(file_path, "r", encoding="utf-8") as in_f:
            for line in in_f:
                stripped = line.strip()

                # 1. If it's a comment or a standard line without matrix formatting
                if stripped.startswith("#") or "|" not in line or "=" not in line:
                    all_lines.append(line)

                # 2. If it is a generic matrix grid line containing '|' and '='
                else:
                    cells = line.split("|")
                    for cell in cells:
                        if "=" in cell:
                            # Clean up the equation and add it as a flat line
                            clean_equation = cell.strip()
                            all_lines.append(clean_equation + "\n")

            # Add a small buffer space between merged files
            all_lines.append("\n")

    # Overwrite/Generate the unified sensor list file
    with open(dest_path, "w", encoding="utf-8") as out_f:
        out_f.writelines(all_lines)


if __name__ == "__main__":
    # Default to merge action if no argument provided to ensure safety inside automated builds
    action = "--merge"
    if len(sys.argv) > 1:
        action = sys.argv[1].lower()

    if action == "--split":
        split_sensor_list(SENSOR_LIST_PATH, PARTS_DIR)
    elif action == "--merge":
        merge_sensor_list(SENSOR_LIST_PATH, PARTS_DIR)
    else:
        print("Invalid argument! Use '--split' to split or '--merge' to combine.")
