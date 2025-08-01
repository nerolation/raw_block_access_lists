# BAL Parser for GitHub

This repository contains a continuous parser for Block Access Lists (BALs) that automatically pushes parsed blocks to GitHub.

## Overview

The parser:
- Checks the GitHub repository for already parsed blocks
- Fetches the latest block from Ethereum
- Parses new blocks working backwards from the latest
- Generates SSZ-encoded BAL files following EIP-7928 format
- Automatically commits and pushes new files to GitHub

## Setup

### Local Setup

1. Clone this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create `rpc.txt` with your Ethereum RPC URL:
   ```bash
   echo "https://your-rpc-endpoint" > rpc.txt
   ```
   Or set the `ETH_RPC_URL` environment variable

### GitHub Actions Setup

1. Fork this repository
2. Go to Settings → Secrets and add:
   - `ETH_RPC_URL`: Your Ethereum RPC endpoint URL
3. The workflow will run automatically every 10 minutes

### Manual Run

To run the parser manually:

```bash
python parse_and_push.py
```

## Configuration

- `MAX_BLOCKS_PER_RUN`: Maximum blocks to parse per run (default: 10)
- Output files are snappy-compressed SSZ format: `{block_number}_block_access_list_with_reads_eip7928.ssz`

## File Format

The BAL files follow the EIP-7928 specification and include:
- Storage writes and reads
- Balance changes
- Nonce changes
- Code changes

Each file is SSZ-encoded and then compressed with Snappy for efficient storage.