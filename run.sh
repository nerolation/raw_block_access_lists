#!/bin/bash
# Simple script to run the parser and push to GitHub

set -e

echo "Running BAL parser..."
python parse_and_push.py

# Check if there are new files
if [ -d "output" ] && [ "$(ls -A output/*.ssz 2>/dev/null)" ]; then
    echo "Moving files to repository root..."
    mv output/*.ssz .
    
    echo "Committing and pushing to GitHub..."
    git add -f *.ssz
    git commit -m "Add BAL blocks $(date +'%Y-%m-%d %H:%M:%S UTC')"
    git push
    
    echo "Done! New BAL files pushed to GitHub."
else
    echo "No new files to push."
fi