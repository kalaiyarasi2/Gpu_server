import olefile
import sys

def inspect_ole(file_path):
    print(f"\n--- OLE Inspect: {file_path} ---")
    try:
        if not olefile.isOleFile(file_path):
            print("Not an OLE file.")
            return
            
        ole = olefile.OleFileIO(file_path)
        print("Streams found in OLE file:")
        for stream in ole.listdir():
            print(f" - {'/'.join(stream)}")
        ole.close()
    except Exception as e:
        print(f"Error inspecting OLE: {e}")

if __name__ == "__main__":
    inspect_ole(r"c:\Users\INTERN\main_project\Lake Country Anthem 1.1-1.31.xlsx")
