#!/usr/bin/env bash
set -o errexit

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt 2>/dev/null || pip install .

# Build frontend with production API base (empty = same origin)
cd frontend
npm install
VITE_API_BASE_URL="" npm run build
cd ..
