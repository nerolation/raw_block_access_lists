#!/bin/bash
# Check the status of the latest workflow run

echo "Checking latest workflow run status..."
echo "Visit: https://github.com/nerolation/raw_block_access_lists/actions"
echo ""
echo "To manually trigger the workflow:"
echo "1. Go to the Actions tab"
echo "2. Click 'Parse and Push BALs'"
echo "3. Click 'Run workflow'"
echo "4. Select 'main' branch and click 'Run workflow'"
echo ""
echo "Common issues to check:"
echo "- Is ETH_RPC_URL secret set in repository settings?"
echo "- Are all dependencies installing correctly?"
echo "- Is the RPC endpoint accessible from GitHub Actions?"