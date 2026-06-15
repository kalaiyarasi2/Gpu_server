import json
import sys

def get_diff(d1, d2, path=""):
    diffs = []
    keys = set(list(d1.keys()) + list(d2.keys()))
    for key in keys:
        new_path = f"{path}.{key}" if path else key
        if key not in d1:
            diffs.append(f"Added: {new_path}")
        elif key not in d2:
            diffs.append(f"Removed: {new_path}")
        elif isinstance(d1[key], dict) and isinstance(d2[key], dict):
            diffs.extend(get_diff(d1[key], d2[key], new_path))
        elif d1[key] != d2[key]:
            diffs.append(f"Changed: {new_path} ({d1[key]} -> {d2[key]})")
    return diffs

def compare_files(f1, f2):
    try:
        with open(f1, 'r') as file1, open(f2, 'r') as file2:
            data1 = json.load(file1)
            data2 = json.load(file2)
            
            diffs = get_diff(data1, data2)
            if not diffs:
                print("Files are identical")
            else:
                print("Files are different:")
                for diff in diffs:
                    print(diff)
    except Exception as e:
        print(f"Error: {e}")

compare_files(sys.argv[1], sys.argv[2])
