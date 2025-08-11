#!/usr/bin/env python3
"""
Cleanup script to maintain only the latest N block access list files.
Removes older files to prevent repository bloat.
"""

import os
import sys
from pathlib import Path
from typing import List, Tuple

# Configuration
MAX_FILES_TO_KEEP = 7200  # Keep only the latest 7200 files
BALS_DIR = Path("bals")

def get_bal_files() -> List[Tuple[int, Path]]:
    """
    Get all BAL JSON files with their block numbers.
    Returns a list of (block_number, file_path) tuples.
    """
    bal_files = []
    
    if not BALS_DIR.exists():
        print(f"Directory {BALS_DIR} does not exist")
        return bal_files
    
    for file_path in BALS_DIR.glob("*.json"):
        try:
            # Extract block number from filename
            # Format: BLOCKNUMBER_block_access_list_with_reads_eip7928.json
            block_num = int(file_path.stem.split("_")[0])
            bal_files.append((block_num, file_path))
        except (ValueError, IndexError):
            print(f"Warning: Could not parse block number from {file_path.name}")
            continue
    
    return bal_files

def cleanup_old_files(max_files: int = MAX_FILES_TO_KEEP) -> int:
    """
    Remove old BAL files, keeping only the most recent ones.
    Returns the number of files deleted.
    """
    # Get all BAL files
    bal_files = get_bal_files()
    
    if not bal_files:
        print("No BAL files found")
        return 0
    
    print(f"Found {len(bal_files)} BAL files")
    
    if len(bal_files) <= max_files:
        print(f"File count ({len(bal_files)}) is within limit ({max_files}), no cleanup needed")
        return 0
    
    # Sort by block number (highest/newest first)
    bal_files.sort(key=lambda x: x[0], reverse=True)
    
    # Identify files to delete (older than the latest max_files)
    files_to_keep = bal_files[:max_files]
    files_to_delete = bal_files[max_files:]
    
    print(f"Keeping {len(files_to_keep)} files (blocks {files_to_keep[-1][0]} to {files_to_keep[0][0]})")
    print(f"Deleting {len(files_to_delete)} older files")
    
    # Delete old files
    deleted_count = 0
    for block_num, file_path in files_to_delete:
        try:
            file_path.unlink()
            deleted_count += 1
            if deleted_count <= 10:  # Show first 10 deletions
                print(f"  Deleted: {file_path.name}")
            elif deleted_count == 11:
                print(f"  ... and {len(files_to_delete) - 10} more files")
        except Exception as e:
            print(f"  Error deleting {file_path.name}: {e}")
    
    return deleted_count

def main():
    """Main function to run cleanup."""
    print("BAL File Cleanup Script")
    print("=" * 50)
    
    # Allow custom limit via command line argument
    max_files = MAX_FILES_TO_KEEP
    if len(sys.argv) > 1:
        try:
            max_files = int(sys.argv[1])
            print(f"Using custom limit: {max_files} files")
        except ValueError:
            print(f"Invalid argument: {sys.argv[1]}, using default: {MAX_FILES_TO_KEEP}")
    
    # Run cleanup
    deleted = cleanup_old_files(max_files)
    
    if deleted > 0:
        print(f"\n✓ Successfully deleted {deleted} old files")
        # Get updated count
        remaining = len(get_bal_files())
        print(f"Remaining files: {remaining}")
    else:
        print("\n✓ No cleanup needed")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())