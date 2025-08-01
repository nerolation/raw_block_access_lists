#!/usr/bin/env python3
"""Test the workflow locally to debug any issues before running on GitHub"""

import os
import sys

# Set a test RPC URL if not already set
if not os.environ.get("ETH_RPC_URL"):
    print("ERROR: ETH_RPC_URL environment variable not set")
    print("Please set it with: export ETH_RPC_URL='your-rpc-url'")
    sys.exit(1)

print("Testing imports and basic functionality...")

try:
    # Test all imports
    from parse_and_push import main, get_existing_blocks_from_github, parse_single_block
    print("✓ Main script imports successful")
    
    # Test RPC connection
    rpc_url = os.environ.get("ETH_RPC_URL")
    print(f"✓ RPC URL found: {rpc_url[:30]}...")
    
    # Test getting existing blocks
    print("\nTesting GitHub API...")
    existing = get_existing_blocks_from_github()
    print(f"✓ Found {len(existing)} existing blocks on GitHub")
    
    # Test getting latest block
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
    from helpers import get_latest_block_number
    
    latest = get_latest_block_number(rpc_url)
    print(f"✓ Latest block: {latest}")
    
    print("\nAll tests passed! The workflow should work on GitHub Actions.")
    
except Exception as e:
    print(f"\n✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)