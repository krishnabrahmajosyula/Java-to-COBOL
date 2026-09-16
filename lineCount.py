import os

# Folder containing COBOL programs
folder_path = r"C:\Users\srikr\OneDrive\Documents\GitHub\Java-to-COBOL\Dataset Pipeline\COBOL_files"

total_lines = 0
file_count = 0

for root, dirs, files in os.walk(folder_path):
    for file in files:
        file_path = os.path.join(root, file)

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()

                # Count non-empty lines only
                code_lines = [line for line in lines if line.strip()]

                line_count = len(code_lines)

                print(f"{file} -> {line_count} lines")

                total_lines += line_count
                file_count += 1

        except Exception as e:
            print(f"Error reading {file}: {e}")

print("\n========== FINAL RESULT ==========")
print(f"Total Files: {file_count}")
print(f"Total Lines: {total_lines}")

if file_count > 0:
    print(f"Average Lines per File: {total_lines / file_count:.2f}")