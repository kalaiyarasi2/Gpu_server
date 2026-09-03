import fitz  # PyMuPDF
import argparse
import os

def detect_rotation_by_text(page):
    blocks = page.get_text("blocks")

    vertical = 0
    horizontal = 0

    for block in blocks:
        x0, y0, x1, y1, text, *_ = block
        width = abs(x1 - x0)
        height = abs(y1 - y0)

        if height > width:
            vertical += 1
        else:
            horizontal += 1

    if vertical > horizontal:
        return 90  # rotate needed
    return 0


def auto_rotate_pdf_content(input_pdf, output_pdf):
    try:
        doc = fitz.open(input_pdf)
        rotated_any = False

        for i in range(len(doc)):
            page = doc[i]
            angle = detect_rotation_by_text(page)

            if angle != 0:
                page.set_rotation(angle)
                rotated_any = True
                print(f"  - Page {i+1}: rotated by {angle}°")

        doc.save(output_pdf)
        doc.close()

        if rotated_any:
            print(f"  ✅ PDF rotated: {output_pdf}")
            return True
        else:
            print(f"  ✅ PDF already correctly oriented: {output_pdf}")
            return False
        return rotated_any
            
    except Exception as e:
        print(f"  ❌ Error processing {input_pdf}: {e}")
        return False


def process_path(input_path, output_path=None):
    if os.path.isfile(input_path):
        # Input is a single file
        if output_path:
             # Check if output_path implies a directory
             if os.path.isdir(output_path) or output_path.endswith(os.sep):
                 filename = os.path.basename(input_path)
                 base, ext = os.path.splitext(filename)
                 final_output = os.path.join(output_path, f"{base}_fixed{ext}")
             else:
                 final_output = output_path
        else:
             base, ext = os.path.splitext(input_path)
             final_output = f"{base}_fixed{ext}"
        
        print(f"📄 Processing file: {input_path}")
        auto_rotate_pdf_content(input_path, final_output)

    elif os.path.isdir(input_path):
        # Input is a directory
        print(f"📂 Processing directory: {input_path}")
        target_dir = output_path if output_path else input_path
        
        if target_dir and not os.path.exists(target_dir):
            os.makedirs(target_dir)

        files_processed = 0
        for root, _, files in os.walk(input_path):
            for file in files:
                if file.lower().endswith(".pdf"):
                    # Avoid reprocessing already fixed files if in same dir
                    if "_fixed.pdf" in file:
                        continue
                        
                    input_file_path = os.path.join(root, file)
                    
                    if output_path:
                        # If output dir is specified, flatten or keep name
                        if os.path.abspath(target_dir) == os.path.abspath(input_path):
                             base, ext = os.path.splitext(file)
                             output_filename = f"{base}_fixed{ext}"
                        else:
                             # Creating a mirror of proper names in output dir
                             output_filename = file
                        
                        final_output = os.path.join(target_dir, output_filename)
                    else:
                        base, ext = os.path.splitext(input_file_path)
                        final_output = f"{base}_fixed{ext}"

                    print(f"  -> Input: {file}")
                    auto_rotate_pdf_content(input_file_path, final_output)
                    files_processed += 1
        
        if files_processed == 0:
            print(f"⚠️ No PDF files found in {input_path}")
    else:
        print(f"❌ Error: Path '{input_path}' not found")


def main():
    parser = argparse.ArgumentParser(description="Auto-rotate PDF pages based on text orientation.")
    parser.add_argument("input", help="Input file or directory path")
    parser.add_argument("-o", "--output", help="Output file or directory path (optional)")
    
    args = parser.parse_args()
    
    process_path(args.input, args.output)

if __name__ == "__main__":
    main()