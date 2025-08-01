import pandas as pd
import requests
import snappy as snappy_compression
import rlp
import ssz


def count_accounts_and_slots(trace_result):
    accounts_set = set()
    total_slots = 0

    for tx in trace_result:
        post = tx.get("result", {}).get("post", {})
        for addr, changes in post.items():
            accounts_set.add(addr)
            if "storage" in changes:
                total_slots += len(changes["storage"])

    return len(accounts_set), total_slots


def get_tracer_payload(block_number_hex, diff_mode=True):
    return {
        "method": "debug_traceBlockByNumber",
        "params": [
            block_number_hex,
            {"tracer": "prestateTracer", "tracerConfig": {"diffMode": diff_mode}},
        ],
        "id": 1,
        "jsonrpc": "2.0",
    }


def get_latest_block_number(rpc_url: str) -> int:
    """Get the latest block number from the Ethereum node."""
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_blockNumber",
        "params": [],
        "id": 1
    }
    
    response = requests.post(rpc_url, json=payload)
    response.raise_for_status()
    
    result = response.json()
    if "error" in result:
        raise Exception(f"RPC error: {result['error']}")
    
    # Convert hex to int
    return int(result.get("result", "0x0"), 16)


def fetch_block_info(block_number: int, rpc_url: str) -> dict:
    """Fetch block information including transaction receipts"""
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getBlockByNumber",
        "params": [hex(block_number), True],
        "id": 1
    }
    
    response = requests.post(rpc_url, json=payload)
    response.raise_for_status()
    
    result = response.json()
    if "error" in result:
        raise Exception(f"RPC error: {result['error']}")
    
    return result.get("result", {})

def fetch_block_receipts(block_number: int, rpc_url: str) -> dict:
    """Fetch block information including transaction receipts"""
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getBlockReceipts",
        "params": [hex(block_number)],
        "id": 1
    }
    
    response = requests.post(rpc_url, json=payload)
    response.raise_for_status()
    
    result = response.json()
    if "error" in result:
        raise Exception(f"RPC error: {result['error']}")
    
    return result.get("result", {})

def fetch_block_trace(block_number, rpc_url, diff_mode=True):
    block_number_hex = hex(block_number)
    payload = get_tracer_payload(block_number_hex, diff_mode)
    response = requests.post(rpc_url, json=payload)
    data = response.json()
    if "error" in data:
        raise Exception(f"RPC Error: {data['error']}")
    return data["result"]


def parse_hex_or_zero(x):
    if pd.isna(x) or x is None:
        return 0
    if isinstance(x, (int, float)):
        return int(x)
    if isinstance(x, str):
        if x.startswith("0x"):
            return int(x, 16)
        else:
            return int(x) if x.isdigit() else 0
    return 0


def get_compressed_size(obj, extra_data=None):
    compressed_data = snappy_compression.compress(obj)
    compressed_size = len(compressed_data)

    # If extra data is provided (like contract code), compress that too
    if extra_data:
        for data in extra_data:
            if data:
                compressed_size += len(snappy_compression.compress(data))

    return compressed_size / 1024


def hex_to_bytes32(hexstr: str) -> bytes:
    """Convert a hex string like '0x...' into exactly 32 bytes (big‐endian)."""
    no_pref = hexstr[2:] if hexstr.startswith("0x") else hexstr
    raw = bytes.fromhex(no_pref)
    return raw.rjust(32, b"\x00")


def get_rlp_compressed_size(obj, extra_data=None):
    """Get snappy-compressed size of RLP object in KiB"""
    if isinstance(obj, bytes):
        compressed_data = snappy_compression.compress(obj)
    else:
        rlp_encoded = rlp.encode(obj)
        compressed_data = snappy_compression.compress(rlp_encoded)
    
    compressed_size = len(compressed_data)

    # If extra data is provided (like contract code), compress that too
    if extra_data:
        for data in extra_data:
            if data:
                compressed_size += len(snappy_compression.compress(data))

    return compressed_size / 1024


def compare_ssz_rlp_sizes(ssz_obj, rlp_obj, ssz_sedes=None):
    """
    Detailed size comparison between SSZ and RLP encodings.
    Returns dict with raw and compressed sizes for both formats.
    """
    # Get SSZ encoding
    if isinstance(ssz_obj, bytes):
        ssz_encoded = ssz_obj
    else:
        if ssz_sedes is None:
            raise ValueError("ssz_sedes required when ssz_obj is not bytes")
        ssz_encoded = ssz.encode(ssz_obj, sedes=ssz_sedes)
    
    # Get RLP encoding
    if isinstance(rlp_obj, bytes):
        rlp_encoded = rlp_obj
    else:
        rlp_encoded = rlp.encode(rlp_obj)
    
    # Calculate raw sizes
    ssz_raw_size = len(ssz_encoded)
    rlp_raw_size = len(rlp_encoded)
    
    # Calculate compressed sizes
    ssz_compressed_size = len(snappy_compression.compress(ssz_encoded))
    rlp_compressed_size = len(snappy_compression.compress(rlp_encoded))
    
    return {
        'ssz': {
            'raw_bytes': ssz_raw_size,
            'raw_kb': ssz_raw_size / 1024,
            'compressed_bytes': ssz_compressed_size,
            'compressed_kb': ssz_compressed_size / 1024,
            'compression_ratio': ssz_compressed_size / ssz_raw_size
        },
        'rlp': {
            'raw_bytes': rlp_raw_size,
            'raw_kb': rlp_raw_size / 1024,
            'compressed_bytes': rlp_compressed_size,
            'compressed_kb': rlp_compressed_size / 1024,
            'compression_ratio': rlp_compressed_size / rlp_raw_size
        },
        'comparison': {
            'raw_size_ratio': rlp_raw_size / ssz_raw_size,
            'compressed_size_ratio': rlp_compressed_size / ssz_compressed_size,
            'raw_size_diff_bytes': rlp_raw_size - ssz_raw_size,
            'compressed_size_diff_bytes': rlp_compressed_size - ssz_compressed_size,
        }
    }


def get_raw_size_kb(obj):
    """Get raw (uncompressed) size of object in KiB"""
    if isinstance(obj, bytes):
        return len(obj) / 1024
    elif hasattr(obj, '__len__'):
        # For RLP/SSZ serializable objects, encode first
        try:
            encoded = rlp.encode(obj)
            return len(encoded) / 1024
        except:
            try:
                encoded = ssz.encode(obj)
                return len(encoded) / 1024
            except:
                return 0
    return 0


def analyze_component_sizes(ssz_components, rlp_components, component_names):
    """
    Analyze size differences for each component of a BAL.
    
    Args:
        ssz_components: dict with component names as keys and SSZ encoded bytes as values
        rlp_components: dict with component names as keys and RLP encoded bytes as values
        component_names: list of component names to analyze
        
    Returns:
        dict with detailed analysis for each component
    """
    analysis = {}
    
    for component in component_names:
        if component in ssz_components and component in rlp_components:
            ssz_data = ssz_components[component]
            rlp_data = rlp_components[component]
            
            # Calculate raw sizes
            ssz_raw = len(ssz_data)
            rlp_raw = len(rlp_data)
            
            # Calculate compressed sizes
            ssz_compressed = len(snappy_compression.compress(ssz_data))
            rlp_compressed = len(snappy_compression.compress(rlp_data))
            
            analysis[component] = {
                'ssz_raw_kb': ssz_raw / 1024,
                'rlp_raw_kb': rlp_raw / 1024,
                'ssz_compressed_kb': ssz_compressed / 1024,
                'rlp_compressed_kb': rlp_compressed / 1024,
                'raw_size_ratio': rlp_raw / ssz_raw if ssz_raw > 0 else float('inf'),
                'compressed_size_ratio': rlp_compressed / ssz_compressed if ssz_compressed > 0 else float('inf'),
                'raw_diff_bytes': rlp_raw - ssz_raw,
                'compressed_diff_bytes': rlp_compressed - ssz_compressed,
                'ssz_compression_ratio': ssz_compressed / ssz_raw if ssz_raw > 0 else 0,
                'rlp_compression_ratio': rlp_compressed / rlp_raw if rlp_raw > 0 else 0,
            }
    
    return analysis


def fetch_block_with_transactions(block_number, rpc_url):
    """
    Fetch block data including full transaction objects.
    
    Args:
        block_number: The block number to fetch
        rpc_url: The RPC endpoint URL
        
    Returns:
        dict: Block data with full transaction objects
    """
    block_number_hex = hex(block_number)
    payload = {
        "method": "eth_getBlockByNumber",
        "params": [block_number_hex, True],  # True to include full transaction objects
        "id": 1,
        "jsonrpc": "2.0",
    }
    response = requests.post(rpc_url, json=payload)
    data = response.json()
    if "error" in data:
        raise Exception(f"RPC Error: {data['error']}")
    return data["result"]


def fetch_transaction_receipts_batch(block_number, rpc_url):
    """
    Fetch all transaction receipts for a block using batch requests.
    
    Args:
        block_number: The block number to fetch receipts for
        rpc_url: The RPC endpoint URL
        
    Returns:
        list: Transaction receipts in block order
    """
    # Try eth_getBlockReceipts first (faster if supported)
    block_number_hex = hex(block_number)
    payload = {
        "method": "eth_getBlockReceipts",
        "params": [block_number_hex],
        "id": 1,
        "jsonrpc": "2.0",
    }
    response = requests.post(rpc_url, json=payload)
    data = response.json()
    
    if "error" not in data and data.get("result"):
        return data["result"]
    
    # Fallback: batch individual receipt requests
    block = fetch_block_with_transactions(block_number, rpc_url)
    tx_hashes = [tx["hash"] for tx in block.get("transactions", [])]
    
    if not tx_hashes:
        return []
    
    # Create batch request
    batch_payload = []
    for i, tx_hash in enumerate(tx_hashes):
        batch_payload.append({
            "method": "eth_getTransactionReceipt",
            "params": [tx_hash],
            "id": i + 1,
            "jsonrpc": "2.0",
        })
    
    response = requests.post(rpc_url, json=batch_payload)
    batch_data = response.json()
    
    # Sort responses by id to maintain order
    if isinstance(batch_data, list):
        receipts = [None] * len(tx_hashes)
        for item in batch_data:
            if "error" in item:
                raise Exception(f"RPC Error for receipt: {item['error']}")
            receipts[item["id"] - 1] = item["result"]
        return receipts
    else:
        raise Exception(f"Batch RPC Error: {batch_data.get('error', 'Unknown error')}")


def identify_simple_eth_transfers(block_number, rpc_url):
    """
    Identify simple ETH transfers (21k gas limit, 21k gas used).
    
    Args:
        block_number: The block number to analyze
        rpc_url: The RPC endpoint URL
        
    Returns:
        dict: Maps tx_index to dict with 'from' and 'to' addresses for simple transfers
    """
    block = fetch_block_with_transactions(block_number, rpc_url)
    receipts = fetch_transaction_receipts_batch(block_number, rpc_url)
    
    simple_transfers = {}
    
    for tx_index, (tx, receipt) in enumerate(zip(block.get("transactions", []), receipts)):
        # Check if this is a simple ETH transfer
        # Conditions:
        # 1. Gas limit is 21000
        # 2. Gas used is 21000
        # 3. No input data (or empty input data)
        gas_limit = int(tx.get("gas", "0x0"), 16)
        gas_used = int(receipt.get("gasUsed", "0x0"), 16)
        input_data = tx.get("input", "0x")
        
        if (gas_limit == 21000 and 
            gas_used == 21000 and 
            (input_data == "0x" or input_data == "")):
            # This is a simple ETH transfer
            simple_transfers[tx_index] = {
                "from": tx["from"].lower(),
                "to": tx.get("to", "").lower() if tx.get("to") else None
            }
    
    return simple_transfers
