#!/bin/bash
# Development environment setup for CodeCompass

set -e

echo "🚀 Setting up CodeCompass development environment..."

# Create virtual environment
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate virtual environment
source .venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -e .

# Install pre-commit hooks
echo "Installing pre-commit hooks..."
pre-commit install

echo "✅ Setup complete!"
echo ""
echo "To activate the virtual environment:"
echo "  source .venv/bin/activate"
echo ""
echo "To run tests:"
echo "  pytest"
echo ""
echo "To run the CLI:"
echo "  codecompass --help"
