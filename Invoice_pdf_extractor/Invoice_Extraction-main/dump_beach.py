import pandas as pd
import sys

def dump_excel(file_path):
    print(f"Dumping first 15 rows of {file_path}:")
    try:
        # Load without header to see raw structure
        df = pd.read_excel(file_path, header=None)
        for i, row in df.head(15).iterrows():
            print(f"Row {i}: {row.fillna('').tolist()}")
    except Exception as e:
        print(f"Error reading Excel: {e}")

if __name__ == "__main__":
    dump_excel(sys.argv[1])
