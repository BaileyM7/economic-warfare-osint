#!/usr/bin/env bash
set -e

echo "=== Installing Python dependencies ==="
pip install --upgrade pip
pip install -r requirements.txt

echo "=== Building frontend ==="
cd frontend
npm ci
npx vite build
cd ..

echo "=== Build complete ==="
