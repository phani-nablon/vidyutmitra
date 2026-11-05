
import os
import sys
import hashlib
import shutil
from pathlib import Path
from collections import defaultdict

# Main folder
if len(sys.argv) < 2:
    sys.exit("usage: python remove_duplicates.py <folder>")
folder = Path(sys.argv[1])

# Folder for duplicate files
extra_folder = folder / "Extra"
extra_folder.mkdir(exist_ok=True)

def get_file_hash(file_path):
    sha256 = hashlib.sha256()

    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha256.update(chunk)

    return sha256.hexdigest()


# Group files by size
size_groups = defaultdict(list)

for file in folder.rglob("*"):
    if not file.is_file():
        continue

    # Ignore files already inside Extra
    if extra_folder in file.parents:
        continue

    size_groups[file.stat().st_size].append(file)


# Find and move duplicates
hash_groups = defaultdict(list)

for size, files in size_groups.items():
    if len(files) < 2:
        continue

    for file in files:
        file_hash = get_file_hash(file)
        hash_groups[(size, file_hash)].append(file)


duplicate_count = 0

for (size, file_hash), files in hash_groups.items():

    if len(files) < 2:
        continue

    # Keep the first file
    for duplicate in files[1:]:

        destination = extra_folder / duplicate.name

        # Avoid overwriting existing files
        counter = 1
        while destination.exists():
            destination = extra_folder / (
                f"{duplicate.stem}_{counter}{duplicate.suffix}"
            )
            counter += 1

        shutil.move(str(duplicate), str(destination))

        duplicate_count += 1

        print(f"MOVED: {duplicate} -> {destination}")


print("\nDuplicate removal completed!")
print(f"Duplicate files moved: {duplicate_count}")
print(f"Extra folder: {extra_folder}")

import os
import sys
import hashlib
import shutil
from pathlib import Path
from collections import defaultdict

# Main folder
if len(sys.argv) < 2:
    sys.exit("usage: python remove_duplicates.py <folder>")
folder = Path(sys.argv[1])


# Folder for duplicate files
extra_folder = folder / "Extra"
extra_folder.mkdir(exist_ok=True)

def get_file_hash(file_path):
    sha256 = hashlib.sha256()

    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha256.update(chunk)

    return sha256.hexdigest()


# Group files by size
size_groups = defaultdict(list)

for file in folder.rglob("*"):
    if not file.is_file():
        continue

    # Ignore files already inside Extra
    if extra_folder in file.parents:
        continue

    size_groups[file.stat().st_size].append(file)


# Find and move duplicates
hash_groups = defaultdict(list)

for size, files in size_groups.items():
    if len(files) < 2:
        continue

    for file in files:
        file_hash = get_file_hash(file)
        hash_groups[(size, file_hash)].append(file)


duplicate_count = 0

for (size, file_hash), files in hash_groups.items():

    if len(files) < 2:
        continue

    # Keep the first file
    for duplicate in files[1:]:

        destination = extra_folder / duplicate.name

        # Avoid overwriting existing files
        counter = 1
        while destination.exists():
            destination = extra_folder / (
                f"{duplicate.stem}_{counter}{duplicate.suffix}"
            )
            counter += 1

        shutil.move(str(duplicate), str(destination))

        duplicate_count += 1

        print(f"MOVED: {duplicate} -> {destination}")


print("\nDuplicate removal completed!")
print(f"Duplicate files moved: {duplicate_count}")
print(f"Extra folder: {extra_folder}")
