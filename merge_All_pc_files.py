import os
import glob
import pandas as pd
from functools import reduce

def extract_table_data(file_path):
    """
    Parses a file to extract its raw structured data for both tables.
    Returns dictionaries containing data mapped by table title.
    """
    tables_data = {}
    current_table = None
    sensor_col = None
    
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
                
            if "TABLE " in line_str:
                current_table = line_str.replace("=", "").strip()
                tables_data[current_table] = []
                continue
                
            if "WS" in line_str and "m/s" in line_str:
                parts = [p.strip() for p in line_str.split('|') if p.strip()]
                if len(parts) >= 2:
                    sensor_col = parts[1]
                continue
                
            if '|' in line_str and not "===" in line_str and not "---" in line_str:
                parts = [p.strip() for p in line_str.split('|') if p.strip()]
                if len(parts) >= 2 and current_table:
                    try:
                        ws_val = float(parts[0])
                        sensor_val = float(parts[1])
                        tables_data[current_table].append((ws_val, sensor_val))
                    except ValueError:
                        continue
                        
    
    dfs = {}
    for t_name, rows in tables_data.items():
        if rows:
            dfs[t_name] = pd.DataFrame(rows, columns=["WS (m/s)", sensor_col])
    return dfs

def remove_duplicate_columns(merged_df):
    if merged_df.empty:
        return merged_df

    ws_columns = "WS (m/s)"

    sensor_columns = [
        col for col in merged_df.columns
        if col != ws_columns
    ]
    columns_to_remove = []
    for i in range(len(sensor_columns)):
        col1 = sensor_columns[i]
        if col1 in columns_to_remove:
            continue

        for j in range(i + 1, len(sensor_columns)):
            col2 = sensor_columns[j]

            if col2 in columns_to_remove:
                continue

            series1 = merged_df[col1]
            series2 = merged_df[col2]

            # compare Data

            same_data = series1.fillna("__EMPTY__").astype(str).equals(
                series2.fillna("__EMPTY__").astype(str)
            )

            if same_data:
                columns_to_remove.append(col2)

    merged_df = merged_df.drop(
        columns = columns_to_remove,
        errors="ignore"
    )            

    return merged_df


def rebuild_file_format(merged_tables, template_file_path):
    """
    Reads the exact text structure of the template file and recreates it 
    with the newly merged multi-column structure dynamically.
    """
    output_lines = []
    current_table = None
    sensor_cols = []
    
    
    for df in merged_tables.values():
      
        cols = sorted(
            [c for c in df.columns if c != "WS (m/s)"], 
            key=lambda x: float(str(x).split('_')[0])
        )
        if len(cols) > len(sensor_cols):
            sensor_cols = cols

  
    with open(template_file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line_str = line.strip()
            
        
            if "===" in line_str:
                line_width = 13 + (len(sensor_cols) * 15)
                output_lines.append("=" * line_width)
                continue
            if "---" in line_str:
                line_width = 13 + (len(sensor_cols) * 15)
                output_lines.append("-" * line_width)
                continue
                
            if "TABLE " in line_str:
                current_table = line_str.replace("=", "").strip()
                output_lines.append(f" {current_table}")
                continue

                
            if "WS" in line_str and "m/s" in line_str:
                header_row = " WS (m/s)   |"
                for col in sensor_cols:
                    header_row += f" {col:>12} |"
                output_lines.append(header_row)
                continue
            
            if '|' in line_str:
                parts = [p.strip() for p in line_str.split('|') if p.strip()]
                try:
                    ws_key = float(parts[0])
                    row_string = f" {ws_key:>9.2f}  |"

                    
                    if current_table in merged_tables:
                        df_target = merged_tables[current_table]
                        matching_row = df_target[df_target["WS (m/s)"] == ws_key]
                        
                        for col in sensor_cols:
                            if not matching_row.empty and col in matching_row.columns:
                                val = matching_row[col].values[0]
                                row_string += f" {val:>12.1f} |" if not pd.isna(val) else f" {'':>12} |"
                            else:
                                row_string += f" {'':>12} |"
                    output_lines.append(row_string)
                except ValueError:
                    output_lines.append(line.rstrip())
            else:
                output_lines.append(line.rstrip())
                
    return "\n".join(output_lines) + "\n"

def merge_power_curve_files(input_folder, output_file="Power_Curve.txt"):
    file_pattern = os.path.join(input_folder, "*.txt")
    all_files = sorted(glob.glob(file_pattern))

    if not all_files:
        print(f"No source files detected in folder: '{input_folder}'")
        return None

   
    all_parsed_data = []
    for filepath in all_files:
        table_dfs = extract_table_data(filepath)
        if table_dfs:
            all_parsed_data.append(table_dfs)

   
    unique_table_names = set(k for d in all_parsed_data for k in d.keys())
    merged_tables = {}

    # for t_name in unique_table_names:
    #     dfs_to_merge = [d[t_name] for d in all_parsed_data if t_name in d]
    #     if dfs_to_merge:
           
    #         merged_df = reduce(lambda left, right: pd.merge(left, right, on="WS (m/s)", how="outer"), dfs_to_merge)
    #         merged_df = merged_df.sort_values(by="WS (m/s)", ascending=True).reset_index(drop=True)
    #         merged_tables[t_name] = merged_df

    for t_name in unique_table_names:
 
        dfs_to_merge = [d[t_name] for d in all_parsed_data if t_name in d]

        if dfs_to_merge:
            merged_df = dfs_to_merge[0].copy()
 
            for next_df in dfs_to_merge[1:]:
 
                merged_df = pd.merge(
                    merged_df,
                    next_df,
                    on="WS (m/s)",
                    how="outer",
                    suffixes=("", "_NEW")
                )
 
                new_columns = [
                    col for col in merged_df.columns
                    if col.endswith("_NEW")
                ]
 
                for new_col in new_columns:
    
                    original_col = new_col[:-4]
    
                    if original_col in merged_df.columns:
    
                        old_data = merged_df[original_col]
                        new_data = merged_df[new_col]
    
                        # Compare values, treating empty values equally
                        same_data = old_data.fillna("__EMPTY__").astype(str).equals(
                            new_data.fillna("__EMPTY__").astype(str)
                        )
    
                        if same_data:
                            merged_df.drop(
                                columns=[new_col],
                                inplace=True
                            )
 
                        else:
                            base_name = original_col
                            counter = 2
    
                            new_name = f"{base_name}_{counter}"
    
                            while new_name in merged_df.columns:
                                counter += 1
                                new_name = f"{base_name}_{counter}"
    
                            merged_df.rename(
                                columns={new_col: new_name},
                                inplace=True
                            )
    
                    else:
                        merged_df.rename(
                            columns={new_col: original_col},
                            inplace=True
                        )
    
            merged_df = merged_df.sort_values(
                by="WS (m/s)",
                ascending=True
            ).reset_index(drop=True)
 
       
 
            merged_df = remove_duplicate_columns(merged_df)
 
            merged_tables[t_name] = merged_df
   
    formatted_output = rebuild_file_format(merged_tables, all_files[0])

    
    with open(output_file, "w", encoding="utf-8") as out_file:
        out_file.write(formatted_output)

    print(f"Success! All folder files merged -> '{output_file}'")
    return output_file



    
      

