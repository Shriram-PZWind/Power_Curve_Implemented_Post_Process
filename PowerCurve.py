import os
import re
import math
from glob import glob

class MultiDensityPowerCalculator:
    def __init__(self, densities=None):
        self.densities = densities or []
        self.density_data = {}
        self.rotor_area = math.pi * (160 / 2) ** 2  

    def extract_density_from_path(self, filepath):
       
        dir_name = os.path.basename(os.path.dirname(filepath))
        match = re.search(r"(?:density|rho|dens)[_\s]*([0-2]\.\d{3})", dir_name, re.IGNORECASE)
        if match:
            return float(match.group(1))
            
        match_num = re.search(r"\b([0-2]\.\d{3})\b", dir_name)
        if match_num:
            val = float(match_num.group(1))
            if 0.5 <= val <= 2.5:
                return val

       
        path_match = re.search(r"(?:density|rho|dens)[_\s]*([0-2]\.\d{3})", filepath, re.IGNORECASE)
        if path_match:
            return float(path_match.group(1))

        filename = os.path.basename(filepath)
        match_file = re.search(r"(?:density|rho|dens)[_\s]*([0-2]\.\d{3})\b", filename, re.IGNORECASE)
        if match_file:
            return float(match_file.group(1))
 
        return None

    def process_root_directory(self, root_path):
        print(f"Scanning root directory: {root_path}\n" + "-"*50)
        
        for root, dirs, files in os.walk(root_path):
            for file in files:
                if file.lower() == "summary_raw.sta":
                    full_file_path = os.path.join(root, file)
                    
                    density_value = self.extract_density_from_path(full_file_path)
                    
                    if density_value is None:
                        print(f"\nCould not automatically detect density for file at: {full_file_path}")
                        while True:
                            user_input = input("Please provide a valid density value (e.g., 1.225): ").strip()
                            try:
                                density_value = float(user_input)
                                break
                            except ValueError:
                                print("Invalid format. Please enter a valid numerical density value.")
                    
                    self.parse_stats_file(full_file_path, density_value)
                    
                    if density_value not in self.densities:
                        self.densities.append(density_value)
                        
        self.densities = sorted(list(set(self.densities)))

    def parse_stats_file(self, root_path, density):
        wind_data = {}
        header = []
        mean_section = False

        with open(root_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line_str = line.strip()

                if line_str.startswith("Mean"):
                    mean_section = True
                    continue

                if mean_section and line_str.startswith("Stdev"):
                    break

                if not mean_section or not line_str:
                    continue

                if line_str.startswith("File"):
                    header = re.split(r'\s+|(?<=\])', line_str)
                    continue

                parts = re.split(r"\s+", line_str)
                if len(parts) < 7:
                    continue

                ws_idx = next((i for i, col in enumerate(header) if "WindHubVelX" in col), 1)
                pwr_idx = next((i for i, col in enumerate(header) if "GenPwr" in col), 2)
                spd_idx = next((i for i, col in enumerate(header) if "GenSpeed" in col), 3)
                trq_idx = next((i for i, col in enumerate(header) if "GenTq" in col), 4)
                bld_pitch = next((i for i, col in enumerate(header) if "BldPitch1" in col), 5)
                cp = next((i for i, col in enumerate(header) if "RtAeroCp" in col), 6)
                ct = next((i for i, col in enumerate(header) if "RtAeroCt" in col), 7)

                try:
                    ws = int((float(parts[ws_idx])) * 2) / 2.0
                    pwr = float(parts[pwr_idx]) if len(parts) > pwr_idx else 0.0
                    speed = float(parts[spd_idx]) if len(parts) > spd_idx else 0.0
                    torque = float(parts[trq_idx]) if len(parts) > trq_idx else 0.0
                    bldPitch = float(parts[bld_pitch]) if len(parts) > bld_pitch else 0.0
                    Cp = float(parts[cp]) if len(parts) > cp else 0.0
                    Ct = float(parts[ct]) if len(parts) > ct else 0.0

                    wind_data[round(ws, 4)] = {
                        "power": pwr,
                        "speed": speed,
                        "torque": torque, 
                        "bldPitch": bldPitch,
                        "Cp": Cp,
                        "Ct": Ct
                    }
                except (ValueError, IndexError):
                    pass

        self.density_data[density] = wind_data
        print(f"Parsed density {density:.3f} from '{os.path.basename(root_path)}'")

    def parse_folder(self, root_path, file_extension="*.sta"):
        search_pattern = os.path.join(root_path, file_extension)
        files = glob(search_pattern)
 
        if not files:
            print(f"No files matching '{file_extension}' found in: {root_path}")
            return
 
        print(f"Found {len(files)} files in folder...")
        for filepath in files:
            density = self.extract_density_from_path(filepath)
            if density is None:
                print(f"\nCould not automatically detect density for file: {os.path.basename(filepath)}")
                while True:
                    user_input = input("Please provide a valid density value (e.g., 1.225): ").strip()
                    try:
                        density = float(user_input)
                        break
                    except ValueError:
                        print("Invalid format. Please enter a valid numerical density value.")

            self.parse_stats_file(filepath, density)
            if density not in self.densities:
                self.densities.append(density)
 
        self.densities = sorted(list(set(self.densities)))
 
    def get_all_wind_speeds(self):
        ws_set = set()
        for ws_dict in self.density_data.values():
            ws_set.update(ws_dict.keys())
        return sorted(list(ws_set))
 
    def generate_formatted_text(self, output_txt_path):
        all_ws = self.get_all_wind_speeds()
        if not self.densities:
            print("No densities parsed. Text output file skipped.")
            return
            
        lines = []
        tables = [
            ("TABLE 1 - ELECTRICAL POWER (kW)", "power"),
            ("TABLE 2 - GENERATOR SPEED (rpm)", "speed"),
            ("TABLE 3 - GENERATOR TORQUE (kNm)", "torque"),
            ("TABLE 4 - BLADE PITCH ANGLE (deg)", "bldPitch"),
            ("TABLE 5 - POWER COEFFICIENT Cp (-)", "Cp"),
            ("TABLE 6 - THRUST COEFFICIENT Ct (-)", "Ct"),
        ]
        
        col_width = 12
        total_width = 11 + len(self.densities) * (col_width + 3)
        divider = "=" * total_width
        line_divider = "-" * total_width

        for title, param in tables:
            lines.extend([divider, title, divider])
            
            header = " WS (m/s) | " + " | ".join(f"{rho:12.3f}" for rho in self.densities) + " |"
            lines.extend([header, line_divider])

            for ws in all_ws:
                row_vals = []
                for rho in self.densities:
                    val = self.density_data.get(rho, {}).get(ws, {}).get(param, 0.0)
                    
                    if param == "power":
                        fmt_str = f"{val:12.1f}"
                    elif param == "speed":
                        fmt_str = f"{val:12.2f}"
                    elif param in ["torque", "bldPitch"]:
                        fmt_str = f"{val:12.3f}"
                    elif param in ["Cp", "Ct"]:
                        fmt_str = f"{val:12.4f}"
                    else:
                        fmt_str = f"{val:12.3f}"
                        
                    row_vals.append(fmt_str)

                data_row = " | ".join(row_vals)
                lines.append(f"    {ws:5.2f} | {data_row} |")
                
            lines.append("\n")

        with open(output_txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"Output successfully written to: {output_txt_path}")

