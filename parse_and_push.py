#!/usr/bin/env python3
"""
Continuous BAL parser that checks GitHub for existing blocks and parses new ones.
Designed to run every 10 minutes via cron or GitHub Actions.
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path
from typing import Set, Optional

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from bal_builder import (
    BALBuilder, 
    fetch_block_trace, 
    fetch_block_receipts,
    fetch_block_info,
    extract_balance_touches_from_block,
    extract_reads_from_block,
    process_storage_changes,
    process_balance_changes,
    process_code_changes,
    process_nonce_changes,
    collect_touched_addresses,
    sort_block_access_list,
    to_canonical_address
)
from BALs import BlockAccessList, bal_to_json
import ssz
from helpers import get_latest_block_number

# Configuration
GITHUB_REPO_URL = "https://github.com/nerolation/raw_block_access_lists"
OUTPUT_DIR = Path("output")
RPC_FILE = Path("rpc.txt")
MAX_BLOCKS_PER_RUN = 10  # Process max 10 blocks per run to avoid timeouts

def get_rpc_url() -> str:
    """Read RPC URL from file."""
    if RPC_FILE.exists():
        return RPC_FILE.read_text().strip()
    # Fallback to environment variable
    rpc_url = os.environ.get("ETH_RPC_URL")
    if not rpc_url:
        raise ValueError("No RPC URL found. Create rpc.txt or set ETH_RPC_URL environment variable")
    return rpc_url

def get_existing_blocks_from_github() -> Set[int]:
    """Fetch list of already parsed blocks from GitHub repository."""
    print("Checking existing blocks on GitHub...")
    
    # Use GitHub API to list files
    import requests
    
    # Extract owner and repo from URL
    parts = GITHUB_REPO_URL.replace("https://github.com/", "").split("/")
    owner, repo = parts[0], parts[1]
    
    existing_blocks = set()
    
    try:
        # GitHub API endpoint for repository contents in bals directory
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/bals"
        
        # Add GitHub token if available for higher rate limits
        headers = {}
        github_token = os.environ.get("GITHUB_TOKEN")
        if github_token:
            headers["Authorization"] = f"token {github_token}"
        
        response = requests.get(api_url, headers=headers)
        
        if response.status_code == 200:
            files = response.json()
            
            # Parse block numbers from filenames
            # Expected format: BLOCKNUMBER_block_access_list_with_reads_eip7928.json
            for file_info in files:
                if isinstance(file_info, dict) and file_info.get("name", "").endswith(".json"):
                    filename = file_info["name"]
                    try:
                        block_num = int(filename.split("_")[0])
                        existing_blocks.add(block_num)
                    except (ValueError, IndexError):
                        continue
            
            print(f"Found {len(existing_blocks)} existing blocks on GitHub")
        else:
            print(f"GitHub API returned status {response.status_code} for bals directory")
            # Fallback: check local git repo if cloned
            if Path(".git").exists():
                result = subprocess.run(
                    ["git", "ls-files", "bals/*.json"],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split("\n"):
                        if line:
                            try:
                                block_num = int(Path(line).stem.split("_")[0])
                                existing_blocks.add(block_num)
                            except (ValueError, IndexError):
                                continue
    
    except Exception as e:
        print(f"Error checking GitHub: {e}")
        # Continue with empty set if we can't check
    
    return existing_blocks

def parse_single_block(block_number: int, rpc_url: str, output_dir: Path) -> Optional[Path]:
    """Parse a single block and save as JSON file."""
    print(f"\nParsing block {block_number}...")
    
    try:
        # Fetch block trace with diff mode
        trace_result = fetch_block_trace(block_number, rpc_url)
        
        # Fetch reads (non-diff mode)
        print(f"  Fetching reads...")
        block_reads = extract_reads_from_block(block_number, rpc_url)
        
        # Fetch balance touches
        print(f"  Fetching balance touches...")
        balance_touches = extract_balance_touches_from_block(block_number, rpc_url)
        
        # Fetch receipts to identify reverted transactions
        print(f"  Fetching receipts...")
        receipts = fetch_block_receipts(block_number, rpc_url)
        reverted_tx_indices = set()
        for i, receipt in enumerate(receipts):
            if receipt and receipt.get("status") == "0x0":
                reverted_tx_indices.add(i)
        
        if reverted_tx_indices:
            print(f"    Found {len(reverted_tx_indices)} reverted transactions")
        
        # Fetch block info
        print(f"  Fetching block info...")
        block_info = fetch_block_info(block_number, rpc_url)
        
        # Build BAL
        builder = BALBuilder()
        
        # Collect touched addresses
        touched_addresses = collect_touched_addresses(trace_result)
        
        # Process all changes
        process_storage_changes(trace_result, block_reads, False, builder, reverted_tx_indices)
        process_balance_changes(trace_result, builder, touched_addresses, balance_touches, 
                              reverted_tx_indices, block_info, receipts, False)
        process_code_changes(trace_result, builder, reverted_tx_indices)
        process_nonce_changes(trace_result, builder, reverted_tx_indices)
        
        # Add touched addresses
        for addr in touched_addresses:
            canonical = to_canonical_address(addr)
            builder.add_touched_account(canonical)
        
        # Build and sort BAL
        block_obj = builder.build(ignore_reads=False)
        block_obj_sorted = sort_block_access_list(block_obj)
        
        # Convert to JSON
        json_data = bal_to_json(block_obj_sorted)
        
        # Save as JSON file
        filename = f"{block_number}_block_access_list_with_reads_eip7928.json"
        filepath = output_dir / filename
        
        with open(filepath, "w") as f:
            json.dump(json_data, f, indent=2)
        
        print(f"  Saved: {filename} (JSON format)")
        return filepath
        
    except Exception as e:
        print(f"  Error parsing block {block_number}: {e}")
        return None

def main():
    """Main function to parse new blocks and prepare for GitHub push."""
    print("BAL Parser - Continuous Block Access List Generator")
    print("=" * 50)
    
    # Get RPC URL
    rpc_url = get_rpc_url()
    
    # Create output directory
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    # Get latest block
    try:
        latest_block = get_latest_block_number(rpc_url)
        print(f"Latest block: {latest_block}")
    except Exception as e:
        print(f"Error getting latest block: {e}")
        return 1
    
    # Get existing blocks from GitHub
    existing_blocks = get_existing_blocks_from_github()
    
    blocks_to_parse = []
    
    if not existing_blocks:
        # No existing blocks - start from latest and work backwards
        print("No existing blocks found. Starting from latest block.")
        for i in range(MAX_BLOCKS_PER_RUN):
            block_num = latest_block - i
            if block_num >= 0:
                blocks_to_parse.append(block_num)
    else:
        # Find the highest block we have
        highest_existing = max(existing_blocks)
        print(f"Highest existing block: {highest_existing}")
        
        # First priority: fill gap between highest existing and latest
        if highest_existing < latest_block:
            for block_num in range(latest_block, highest_existing, -1):
                if block_num not in existing_blocks:
                    blocks_to_parse.append(block_num)
                if len(blocks_to_parse) >= MAX_BLOCKS_PER_RUN:
                    break
        
        # Second priority: fill any gaps in existing blocks
        if len(blocks_to_parse) < MAX_BLOCKS_PER_RUN:
            # Find gaps by checking continuity
            sorted_existing = sorted(existing_blocks, reverse=True)
            for i in range(len(sorted_existing) - 1):
                current = sorted_existing[i]
                next_block = sorted_existing[i + 1]
                
                # If there's a gap
                if current - next_block > 1:
                    # Fill the gap
                    for block_num in range(current - 1, next_block, -1):
                        if block_num not in existing_blocks:
                            blocks_to_parse.append(block_num)
                        if len(blocks_to_parse) >= MAX_BLOCKS_PER_RUN:
                            break
                
                if len(blocks_to_parse) >= MAX_BLOCKS_PER_RUN:
                    break
        
        # Third priority: continue backwards from lowest existing
        if len(blocks_to_parse) < MAX_BLOCKS_PER_RUN:
            lowest_existing = min(existing_blocks)
            for block_num in range(lowest_existing - 1, lowest_existing - 20, -1):
                if block_num >= 0 and block_num not in existing_blocks:
                    blocks_to_parse.append(block_num)
                if len(blocks_to_parse) >= MAX_BLOCKS_PER_RUN:
                    break
    
    if not blocks_to_parse:
        print("No new blocks to parse")
        return 0
    
    # Sort blocks to parse (highest first)
    blocks_to_parse = sorted(blocks_to_parse, reverse=True)[:MAX_BLOCKS_PER_RUN]
    
    print(f"\nFound {len(blocks_to_parse)} blocks to parse")
    print(f"Will parse: {blocks_to_parse}")
    
    # Parse blocks
    parsed_files = []
    for block_num in blocks_to_parse:
        filepath = parse_single_block(block_num, rpc_url, OUTPUT_DIR)
        if filepath:
            parsed_files.append(filepath)
    
    print(f"\nSuccessfully parsed {len(parsed_files)} blocks")
    
    # Save metadata for GitHub Action
    metadata = {
        "parsed_blocks": [int(f.stem.split("_")[0]) for f in parsed_files],
        "total_parsed": len(parsed_files),
        "latest_block": latest_block,
        "highest_existing": max(existing_blocks) if existing_blocks else None,
        "total_existing": len(existing_blocks),
        "timestamp": int(time.time())
    }
    
    with open(OUTPUT_DIR / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())