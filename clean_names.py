
import os
import re
from pathlib import Path

# Change this to your target folder
folder = Path(r"C:\Users\ibpha\work\projects\substation\data\all_data\Updated Drawings\Vidyut Mitra_CSS Drawings for Automation\all_docs")

for file in folder.iterdir():
    if not file.is_file():
        continue

    # Separate filename and extension
    name = file.stem.lower()
    ext = file.suffix.lower()

    # Replace spaces and special characters with underscore
    name = re.sub(r'[^a-z0-9]', '_', name)

    # Remove consecutive underscores
    name = re.sub(r'_+', '_', name)

    # Remove leading and trailing underscores
    name = name.strip('_')

    # Handle empty filenames
    if not name:
        name = "unnamed"

    # Build new filename
    new_file = file.with_name(name + ext)

    # Rename only if changed and destination doesn't exist
    if new_file != file:
        if new_file.exists():
            print(f"SKIPPED (already exists): {new_file.name}")
        else:
            file.rename(new_file)
            print(f"RENAMED: {file.name} -> {new_file.name}")

print("Done!")
