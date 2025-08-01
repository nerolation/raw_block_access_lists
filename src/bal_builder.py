import os
import ssz
import sys
import json
import argparse
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple
from eth_utils import to_canonical_address

project_root = str(Path(__file__).parent.parent)
src_dir = os.path.join(project_root, "src")
sys.path.insert(0, src_dir)

from BALs import *
from helpers import *
from helpers import fetch_block_info

def get_rpc_url():
    """Get RPC URL from environment variable or file."""
    # First try environment variable
    rpc_url = os.environ.get("ETH_RPC_URL")
    if rpc_url:
        return rpc_url
    
    # Fallback to file if it exists
    rpc_file = os.path.join(project_root, "rpc.txt")
    if os.path.exists(rpc_file):
        with open(rpc_file, "r") as file:
            return file.read().strip()
    
    # If running as main script, require RPC URL
    if __name__ == "__main__":
        raise ValueError("No RPC URL found. Set ETH_RPC_URL environment variable or create rpc.txt")
    
    # Otherwise return None and let caller handle it
    return None

RPC_URL = get_rpc_url()
IGNORE_STORAGE_LOCATIONS = False

def extract_balances(state):
    balances = {}
    for addr, changes in state.items():
        if "balance" in changes:
            balances[addr] = parse_hex_or_zero(changes["balance"])
    return balances

def parse_pre_and_post_balances(pre_state, post_state):
    return extract_balances(pre_state), extract_balances(post_state)

def get_balance_delta(pres, posts, pre_balances, post_balances):
    all_addresses = pres.union(posts)
    balance_delta = {}
    for addr in all_addresses:
        pre_balance = pre_balances.get(addr, 0)
        post_balance = post_balances.get(addr, pre_balance)
        delta = post_balance - pre_balance
        if delta != 0:  # Include all non-zero deltas (positive and negative)
            balance_delta[addr] = delta
    return balance_delta

def get_balance_delta_with_touches(pres, posts, pre_balances, post_balances, balance_touches_for_tx=None):
    """Calculate balance deltas using balance_touches to get accurate pre-balances.
    
    This function corrects for the diffMode=true limitation where addresses with
    balance reads but no changes are omitted from the pre-state.
    """
    all_addresses = pres.union(posts)
    balance_delta = {}
    
    for addr in all_addresses:
        # Check if we have a more accurate pre-balance from balance_touches
        if balance_touches_for_tx and addr in balance_touches_for_tx and addr not in pres:
            # Address was touched but not in pre-state due to diffMode=true
            # Use the balance from diffMode=false as the accurate pre-balance
            pre_balance = parse_hex_or_zero(balance_touches_for_tx[addr])
        else:
            pre_balance = pre_balances.get(addr, 0)
            
        post_balance = post_balances.get(addr, pre_balance)
        delta = post_balance - pre_balance
        if delta != 0:  # Include all non-zero deltas (positive and negative)
            balance_delta[addr] = delta
    return balance_delta

def extract_balance_touches_from_block(block_number: int, rpc_url: str) -> Dict[int, Dict[str, str]]:
    """Extract all addresses that had balance reads/writes using diff_mode=False.
    
    Returns:
        Dict mapping tx_index to dict of address -> balance value
    """
    trace_result = fetch_block_trace(block_number, rpc_url, diff_mode=False)
    balance_touches = {}
    
    for tx_id, tx_trace in enumerate(trace_result):
        result = tx_trace.get("result", {})
        touched_addrs = {}
        for address, acc_data in result.items():
            if "balance" in acc_data:
                touched_addrs[address.lower()] = acc_data["balance"]
        if touched_addrs:
            balance_touches[tx_id] = touched_addrs
    
    return balance_touches

def identify_gas_related_addresses(block_info: dict, tx_index: int) -> Tuple[Optional[str], Optional[str]]:
    if not block_info or "transactions" not in block_info:
        return None, None
        
    transactions = block_info.get("transactions", [])
    if tx_index >= len(transactions):
        return None, None
        
    tx = transactions[tx_index]
    sender = tx.get("from", "").lower() if tx.get("from") else None
    
    fee_recipient = block_info.get("miner", "").lower() if block_info.get("miner") else None
    
    return sender, fee_recipient

def process_balance_changes(trace_result, builder: BALBuilder, touched_addresses: set, 
                          balance_touches: Dict[int, Dict[str, str]] = None, 
                          reverted_tx_indices: set = None,
                          block_info: dict = None,
                          receipts: List[dict] = None,
                          ignore_reads: bool = False):
    if reverted_tx_indices is None:
        reverted_tx_indices = set()
        
    processed_per_tx = {}
    
    for tx_id, tx in enumerate(trace_result):
        result = tx.get("result")
        if not isinstance(result, dict):
            continue

        if tx_id in reverted_tx_indices:
            # For reverted transactions, calculate gas fee from receipt with EIP-1559 support
            if receipts and tx_id < len(receipts) and block_info and tx_id < len(block_info.get("transactions", [])):
                receipt = receipts[tx_id]
                tx_info = block_info["transactions"][tx_id]
                
                # Calculate gas fee components
                gas_used = int(receipt.get("gasUsed", "0x0"), 16)
                
                # Get effective gas price
                if "effectiveGasPrice" in receipt:
                    effective_gas_price = int(receipt["effectiveGasPrice"], 16)
                elif "gasPrice" in tx_info:
                    effective_gas_price = int(tx_info["gasPrice"], 16)
                else:
                    effective_gas_price = 0
                
                # Get base fee for EIP-1559
                base_fee_per_gas = int(block_info.get("baseFeePerGas", "0x0"), 16)
                
                # Calculate fees
                total_gas_fee = gas_used * effective_gas_price
                
                # For EIP-1559 transactions, only priority fee goes to miner
                if base_fee_per_gas > 0:
                    priority_fee_per_gas = effective_gas_price - base_fee_per_gas
                    priority_fee_total = gas_used * priority_fee_per_gas
                else:
                    # Pre-EIP-1559, all gas fee goes to miner
                    priority_fee_total = total_gas_fee
                
                # Get addresses
                sender = tx_info.get("from", "").lower()
                fee_recipient = block_info.get("miner", "").lower()
                
                # Get accurate pre-balances from balance touches
                balance_touches_for_tx = balance_touches.get(tx_id, {}) if balance_touches else {}
                
                # Calculate post-balances
                sender_pre = parse_hex_or_zero(balance_touches_for_tx.get(sender, "0x0"))
                fee_recipient_pre = parse_hex_or_zero(balance_touches_for_tx.get(fee_recipient, "0x0"))
                
                # Sender pays full gas fee
                sender_post = sender_pre - total_gas_fee
                # Fee recipient only gets priority fee (base fee is burned)
                fee_recipient_post = fee_recipient_pre + priority_fee_total
                
                # Add balance changes
                sender_canonical = to_canonical_address(sender)
                sender_post_bytes = sender_post.to_bytes(16, "big", signed=False)
                builder.add_balance_change(sender_canonical, tx_id, sender_post_bytes)
                
                fee_recipient_canonical = to_canonical_address(fee_recipient)
                fee_recipient_post_bytes = fee_recipient_post.to_bytes(16, "big", signed=False)
                builder.add_balance_change(fee_recipient_canonical, tx_id, fee_recipient_post_bytes)
                
                touched_addresses.add(sender)
                touched_addresses.add(fee_recipient)
                processed_addrs = {sender, fee_recipient}
            else:
                processed_addrs = set()
        else:
            # Normal (non-reverted) transaction processing
            pre_state, post_state = tx["result"]["pre"], tx["result"]["post"]
            pre_balances, post_balances = parse_pre_and_post_balances(pre_state, post_state)
            pres, posts = set(pre_balances.keys()), set(post_balances.keys())
            
            # Get balance touches for this specific transaction to fix diffMode=true issues
            balance_touches_for_tx = balance_touches.get(tx_id, {}) if balance_touches else {}
            
            # Use the enhanced delta calculation that accounts for missing pre-states
            balance_delta = get_balance_delta_with_touches(pres, posts, pre_balances, post_balances, balance_touches_for_tx)
            
            # Also add addresses from balance_touches to ensure they're tracked
            if balance_touches_for_tx:
                posts.update(balance_touches_for_tx.keys())

            all_balance_addresses = pres.union(posts)
            for address in all_balance_addresses:
                touched_addresses.add(address.lower())

            processed_addrs = set()
            
            for address, delta_val in balance_delta.items():
                canonical = to_canonical_address(address)
                post_balance = post_balances.get(address, 0)
                post_balance_bytes = post_balance.to_bytes(16, "big", signed=False)
                builder.add_balance_change(canonical, tx_id, post_balance_bytes)
                processed_addrs.add(address.lower())
        
        processed_per_tx[tx_id] = processed_addrs
    
    if balance_touches and not ignore_reads:
        for tx_id, touched_addrs_balances in balance_touches.items():
            processed = processed_per_tx.get(tx_id, set())
            for address, balance_hex in touched_addrs_balances.items():
                if address not in processed:
                    touched_addresses.add(address)
                    canonical = to_canonical_address(address)
                    builder.add_touched_account(canonical)

def extract_non_empty_code(state: dict, address: str) -> Optional[str]:
    code = state.get(address, {}).get("code")
    if code and code not in ("", "0x"):
        return code
    return None

def decode_hex_code(code_hex: str) -> bytes:
    code_str = code_hex[2:] if code_hex.startswith("0x") else code_hex
    return bytes.fromhex(code_str)

def process_code_changes(trace_result: List[dict], builder: BALBuilder, reverted_tx_indices: set = None):
    if reverted_tx_indices is None:
        reverted_tx_indices = set()
        
    for tx_id, tx in enumerate(trace_result):
        result = tx.get("result")
        if not isinstance(result, dict):
            continue
            
        if tx_id in reverted_tx_indices:
            continue

        pre_state, post_state = result.get("pre", {}), result.get("post", {})
        all_addresses = set(pre_state) | set(post_state)

        for address in all_addresses:
            pre_code = extract_non_empty_code(pre_state, address)
            post_code = extract_non_empty_code(post_state, address)
            
            if post_code is None or post_code == pre_code:
                continue

            code_bytes = decode_hex_code(post_code)
            if len(code_bytes) > MAX_CODE_SIZE:
                raise ValueError(
                    f"Contract code too large in tx {tx_id} for {address}: "
                    f"{len(code_bytes)} > {MAX_CODE_SIZE}"
                )

            canonical = to_canonical_address(address)
            builder.add_code_change(canonical, tx_id, code_bytes)

def extract_reads_from_block(block_number: int, rpc_url: str) -> Dict[str, Set[str]]:
    trace_result = fetch_block_trace(block_number, rpc_url, diff_mode=False)
    reads = defaultdict(set)
    
    for tx_trace in trace_result:
        result = tx_trace.get("result", {})
        for address, acc_data in result.items():
            storage = acc_data.get("storage", {})
            for slot in storage.keys():
                reads[address.lower()].add(slot.lower())
    
    return reads

def _is_non_write_read(pre_val_hex: Optional[str], post_val_hex: Optional[str]) -> bool:
    return pre_val_hex is not None and post_val_hex is None

def process_storage_changes(
    trace_result: List[dict], 
    additional_reads: Optional[Dict[str, Set[str]]] = None,
    ignore_reads: bool = IGNORE_STORAGE_LOCATIONS,
    builder: BALBuilder = None,
    reverted_tx_indices: set = None
):
    if reverted_tx_indices is None:
        reverted_tx_indices = set()
        
    block_writes: Dict[str, Dict[str, List[Tuple[int, str]]]] = {}
    block_reads: Dict[str, Set[str]] = {}

    for tx_id, tx in enumerate(trace_result):
        result = tx.get("result")
        if not isinstance(result, dict):
            continue
            
        if tx_id in reverted_tx_indices:
            continue

        pre_state = result.get("pre", {})
        post_state = result.get("post", {})
        all_addresses = set(pre_state) | set(post_state)

        for address in all_addresses:
            pre_storage = pre_state.get(address, {}).get("storage", {})
            post_storage = post_state.get(address, {}).get("storage", {})
            all_slots = set(pre_storage) | set(post_storage)

            for slot in all_slots:
                pre_val = pre_storage.get(slot)
                post_val = post_storage.get(slot)

                if post_val is not None:
                    pre_bytes = (
                        hex_to_bytes32(pre_val) if pre_val is not None else b"\x00" * 32
                    )
                    post_bytes = hex_to_bytes32(post_val)
                    if pre_bytes != post_bytes:
                        block_writes.setdefault(address, {}).setdefault(slot, []).append(
                            (tx_id, post_val)
                        )
                    else:
                        if not ignore_reads:
                            if address not in block_writes or slot not in block_writes.get(address, {}):
                                block_reads.setdefault(address, set()).add(slot)
                elif pre_val is not None and slot not in post_storage:
                    zero_value = "0x" + "00" * 32
                    block_writes.setdefault(address, {}).setdefault(slot, []).append(
                        (tx_id, zero_value)
                    )
                elif not ignore_reads and _is_non_write_read(pre_val, post_val):
                    if address not in block_writes or slot not in block_writes.get(address, {}):
                        block_reads.setdefault(address, set()).add(slot)

    if not ignore_reads and additional_reads is not None:
        for address, read_slots in additional_reads.items():
            if address not in block_writes:
                for slot in read_slots:
                    block_reads.setdefault(address, set()).add(slot)
            else:
                written_slots = set(block_writes[address].keys())
                for slot in read_slots:
                    if slot not in written_slots:
                        block_reads.setdefault(address, set()).add(slot)

    for address, slots in block_writes.items():
        canonical_addr = to_canonical_address(address)
        for slot, write_entries in slots.items():
            slot_bytes = hex_to_bytes32(slot)
            for tx_id, val_hex in write_entries:
                value_bytes = hex_to_bytes32(val_hex)
                builder.add_storage_write(canonical_addr, slot_bytes, tx_id, value_bytes)

    for address, read_slots in block_reads.items():
        canonical_addr = to_canonical_address(address)
        for slot in read_slots:
            slot_bytes = hex_to_bytes32(slot)
            builder.add_storage_read(canonical_addr, slot_bytes)

def _get_nonce(info: dict, fallback: str = "0") -> int:
    nonce_str = info.get("nonce", fallback)
    return int(nonce_str, 16) if isinstance(nonce_str, str) and nonce_str.startswith('0x') else int(nonce_str)

def process_nonce_changes(trace_result: List[Dict[str, Any]], builder: BALBuilder, reverted_tx_indices: set = None):
    if reverted_tx_indices is None:
        reverted_tx_indices = set()
        
    for tx_index, tx in enumerate(trace_result):
        result = tx.get("result")
        if not isinstance(result, dict):
            continue

        pre_state = result.get("pre", {})
        post_state = result.get("post", {})

        # Process all addresses that appear in either pre or post state
        all_addresses = set(pre_state.keys()) | set(post_state.keys())
        
        for address_hex in all_addresses:
            pre_info = pre_state.get(address_hex, {})
            post_info = post_state.get(address_hex, {})
            
            # Get pre and post nonces (defaulting to 0 if not present)
            pre_nonce = _get_nonce(pre_info, fallback="0") if pre_info else 0
            post_nonce = _get_nonce(post_info, fallback="0") if post_info else 0
            
            # Add nonce change if it increased
            if post_nonce > pre_nonce:
                canonical = to_canonical_address(address_hex)
                builder.add_nonce_change(canonical, tx_index, post_nonce)

def collect_touched_addresses(trace_result: List[dict]) -> Set[str]:
    touched = set()
    
    for tx in trace_result:
        result = tx.get("result")
        if not isinstance(result, dict):
            continue
            
        pre_state = result.get("pre", {})
        post_state = result.get("post", {})
        
        all_addresses = set(pre_state.keys()) | set(post_state.keys())
        for addr in all_addresses:
            touched.add(addr.lower())
    
    return touched

def sort_block_access_list(bal: BlockAccessList) -> BlockAccessList:
    sorted_accounts = []
    
    for account in sorted(bal.account_changes, key=lambda x: bytes(x.address)):
        sorted_storage_writes = []
        for storage_access in sorted(account.storage_writes, key=lambda x: bytes(x.slot)):
            sorted_changes = sorted(storage_access.changes, key=lambda x: x.tx_index)
            sorted_storage_access = StorageAccess(slot=storage_access.slot, changes=sorted_changes)
            sorted_storage_writes.append(sorted_storage_access)
        
        sorted_storage_reads = sorted(account.storage_reads, key=lambda x: bytes(x))
        
        sorted_balance_changes = sorted(account.balance_changes, key=lambda x: x.tx_index)
        sorted_nonce_changes = sorted(account.nonce_changes, key=lambda x: x.tx_index)
        sorted_code_changes = sorted(account.code_changes, key=lambda x: x.tx_index)
        
        sorted_account = AccountChanges(
            address=account.address,
            storage_writes=sorted_storage_writes,
            storage_reads=sorted_storage_reads,
            balance_changes=sorted_balance_changes,
            nonce_changes=sorted_nonce_changes,
            code_changes=sorted_code_changes
        )
        sorted_accounts.append(sorted_account)
    
    return BlockAccessList(account_changes=sorted_accounts)

def get_component_sizes(bal: BlockAccessList) -> Dict[str, float]:
    
    storage_writes_data = []
    storage_reads_data = []
    balance_changes_data = []
    nonce_changes_data = []
    code_changes_data = []
    
    for account in bal.account_changes:
        if account.storage_writes:
            storage_writes_data.extend(account.storage_writes)
        if account.storage_reads:
            storage_reads_data.extend(account.storage_reads)
        if account.balance_changes:
            balance_changes_data.extend(account.balance_changes)
        if account.nonce_changes:
            nonce_changes_data.extend(account.nonce_changes)
        if account.code_changes:
            code_changes_data.extend(account.code_changes)
    
    storage_writes_size = get_compressed_size(
        ssz.encode(storage_writes_data, sedes=SSZList(StorageAccess, MAX_SLOTS))
    ) if storage_writes_data else 0
    
    storage_reads_size = get_compressed_size(
        ssz.encode(storage_reads_data, sedes=SSZList(StorageKey, MAX_SLOTS))
    ) if storage_reads_data else 0
    
    balance_size = get_compressed_size(
        ssz.encode(balance_changes_data, sedes=SSZList(BalanceChange, MAX_TXS))
    ) if balance_changes_data else 0
    
    nonce_size = get_compressed_size(
        ssz.encode(nonce_changes_data, sedes=SSZList(NonceChange, MAX_TXS))
    ) if nonce_changes_data else 0
    
    code_size = get_compressed_size(
        ssz.encode(code_changes_data, sedes=SSZList(CodeChange, MAX_TXS))
    ) if code_changes_data else 0
    
    total_storage_size = storage_writes_size + storage_reads_size
    total_size = total_storage_size + balance_size + code_size + nonce_size
    
    return {
        'storage_writes_kb': storage_writes_size,
        'storage_reads_kb': storage_reads_size,
        'storage_total_kb': total_storage_size,
        'balance_diffs_kb': balance_size,
        'nonce_diffs_kb': nonce_size,
        'code_diffs_kb': code_size,
        'total_kb': total_size,
    }

def main():
    global IGNORE_STORAGE_LOCATIONS
    
    parser = argparse.ArgumentParser(description='Build Block Access Lists (BALs) from Ethereum blocks using latest EIP-7928 format')
    parser.add_argument('--no-reads', action='store_true', 
                        help='Ignore storage read locations (only include writes)')
    args = parser.parse_args()
    
    IGNORE_STORAGE_LOCATIONS = args.no_reads
    
    # Get RPC URL for main script execution
    rpc_url = RPC_URL
    if not rpc_url:
        raise ValueError("No RPC URL found. Set ETH_RPC_URL environment variable or create rpc.txt")
    
    print(f"Running EIP-7928 SSZ BAL builder with IGNORE_STORAGE_LOCATIONS = {IGNORE_STORAGE_LOCATIONS}")
    totals = defaultdict(list)
    block_totals = []
    data = []

    random_blocks = range(20615532, 20615562, 10)

    for block_number in random_blocks:
        print(f"Processing block {block_number}...")
        trace_result = fetch_block_trace(block_number, rpc_url)
        
        block_reads = None
        if not IGNORE_STORAGE_LOCATIONS:
            print(f"  Fetching reads for block {block_number}...")
            block_reads = extract_reads_from_block(block_number, rpc_url)

        print(f"  Fetching balance touches for block {block_number}...")
        balance_touches = extract_balance_touches_from_block(block_number, rpc_url)
        
        print(f"  Fetching transaction receipts for block {block_number}...")
        receipts = fetch_block_receipts(block_number, rpc_url)
        reverted_tx_indices = set()
        for i, receipt in enumerate(receipts):
            if receipt and receipt.get("status") == "0x0":
                reverted_tx_indices.add(i)
        if reverted_tx_indices:
            print(f"    Found {len(reverted_tx_indices)} reverted transactions: {sorted(reverted_tx_indices)}")
            
        print(f"  Fetching block info...")
        block_info = fetch_block_info(block_number, rpc_url)

        builder = BALBuilder()
        
        touched_addresses = collect_touched_addresses(trace_result)
        
        process_storage_changes(trace_result, block_reads, IGNORE_STORAGE_LOCATIONS, builder, reverted_tx_indices)
        process_balance_changes(trace_result, builder, touched_addresses, balance_touches, reverted_tx_indices, block_info, receipts, IGNORE_STORAGE_LOCATIONS)
        process_code_changes(trace_result, builder, reverted_tx_indices)
        process_nonce_changes(trace_result, builder, reverted_tx_indices)
        
        if not IGNORE_STORAGE_LOCATIONS:
            for addr in touched_addresses:
                canonical = to_canonical_address(addr)
                builder.add_touched_account(canonical)
        
        block_obj = builder.build(ignore_reads=IGNORE_STORAGE_LOCATIONS)
        block_obj_sorted = sort_block_access_list(block_obj)
        
        full_block_encoded = ssz.encode(block_obj_sorted, sedes=BlockAccessList)

 
        bal_raw_dir = os.path.join(project_root, "bal_raw", "ssz")
        os.makedirs(bal_raw_dir, exist_ok=True)
        
        reads_suffix = "without_reads" if IGNORE_STORAGE_LOCATIONS else "with_reads"
        filename = f"{block_number}_block_access_list_{reads_suffix}_eip7928.txt"
        filepath = os.path.join(bal_raw_dir, filename)
        
        with open(filepath, "wb") as f:
            f.write(full_block_encoded)

        component_sizes = get_component_sizes(block_obj_sorted)

        accs, slots = count_accounts_and_slots(trace_result)
        
        bal_stats = get_account_stats(block_obj_sorted)

        data.append({
            "block_number": block_number,
            "sizes": component_sizes,
            "counts": {
                "accounts": accs,
                "slots": slots,
            },
            "bal_stats": bal_stats,
        })

        totals["storage_writes"].append(component_sizes["storage_writes_kb"])
        totals["storage_reads"].append(component_sizes["storage_reads_kb"])
        totals["storage_total"].append(component_sizes["storage_total_kb"])
        totals["balance"].append(component_sizes["balance_diffs_kb"])
        totals["code"].append(component_sizes["code_diffs_kb"])
        totals["nonce"].append(component_sizes["nonce_diffs_kb"])
        block_totals.append(component_sizes["total_kb"])

    filename = "bal_analysis_without_reads_eip7928.json" if IGNORE_STORAGE_LOCATIONS else "bal_analysis_with_reads_eip7928.json"
    filepath = os.path.join(project_root, "bal_raw", "ssz", filename)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    print("\nAverage compressed size per component (in KiB):")
    for name, sizes in totals.items():
        avg = sum(sizes) / len(sizes) if sizes else 0
        print(f"{name}: {avg:.2f} KiB")

    overall_avg = sum(block_totals) / len(block_totals) if block_totals else 0
    print(f"\nOverall average compressed size per block: {overall_avg:.2f} KiB")
    
    if data:
        avg_accounts = sum(d["bal_stats"]["total_accounts"] for d in data) / len(data)
        avg_storage_writes = sum(d["bal_stats"]["total_storage_writes"] for d in data) / len(data)
        avg_storage_reads = sum(d["bal_stats"]["total_storage_reads"] for d in data) / len(data)
        avg_balance_changes = sum(d["bal_stats"]["total_balance_changes"] for d in data) / len(data)
        avg_nonce_changes = sum(d["bal_stats"]["total_nonce_changes"] for d in data) / len(data)
        
        print(f"\nEfficiency stats:")
        print(f"Average accounts per block: {avg_accounts:.1f}")
        print(f"Average storage writes per block: {avg_storage_writes:.1f}")
        print(f"Average storage reads per block: {avg_storage_reads:.1f}")
        print(f"Average balance changes per block: {avg_balance_changes:.1f}")
        print(f"Average nonce changes per block: {avg_nonce_changes:.1f}")

if __name__ == "__main__":
    main()