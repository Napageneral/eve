#!/bin/bash

# ChatStats VM Setup Script
# Creates a base Tart VM with ChatStats pre-installed

set -e  # Exit on error

echo "========================================"
echo "ChatStats VM Setup"
echo "========================================"
echo ""

# Configuration
BASE_IMAGE="ghcr.io/cirruslabs/macos-ventura-base:latest"
BASE_VM_NAME="chatstats-base"
SNAPSHOT_NAME="chatstats-ready"
CHATSTATS_REPO="https://github.com/user/chatstats.git"  # Update with actual repo

# Step 1: Check if Tart is installed
echo "Step 1: Checking for Tart..."
if ! command -v tart &> /dev/null; then
    echo "❌ Tart is not installed"
    echo "Install with: brew install cirruslabs/cli/tart"
    exit 1
fi
echo "✅ Tart found: $(tart --version)"
echo ""

# Step 2: Check if snapshot already exists
echo "Step 2: Checking for existing snapshot..."
if tart list | grep -q "$SNAPSHOT_NAME"; then
    echo "⚠️  Snapshot '$SNAPSHOT_NAME' already exists"
    read -p "Do you want to rebuild it? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Setup cancelled. Using existing snapshot."
        exit 0
    fi
    echo "Deleting existing snapshot..."
    tart delete "$SNAPSHOT_NAME" || true
fi
echo ""

# Step 3: Clone base image
echo "Step 3: Cloning base macOS image..."
if tart list | grep -q "$BASE_VM_NAME"; then
    echo "Deleting existing base VM..."
    tart delete "$BASE_VM_NAME" || true
fi
echo "Cloning $BASE_IMAGE..."
tart clone "$BASE_IMAGE" "$BASE_VM_NAME"
echo "✅ Base image cloned"
echo ""

# Step 4: Start VM
echo "Step 4: Starting VM..."
echo "This may take 30-60 seconds..."
tart run "$BASE_VM_NAME" --no-graphics &
TART_PID=$!
sleep 60  # Wait for VM to boot
echo "✅ VM started"
echo ""

# Step 5: Install dependencies
echo "Step 5: Installing dependencies in VM..."
echo "This may take 10-15 minutes..."
echo ""

# Create setup script
cat > /tmp/chatstats-setup.sh <<'EOF'
#!/bin/bash
set -e

echo "Installing Homebrew..."
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" < /dev/null

# Add Homebrew to PATH
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"

echo "Installing Node.js..."
brew install node

echo "Installing Python..."
brew install python@3.11

echo "Installing Redis..."
brew install redis

echo "Installing Git..."
brew install git

echo "Installing dependencies complete!"
node --version
python3 --version
redis-server --version
EOF

# Execute setup script in VM
tart run "$BASE_VM_NAME" -- bash < /tmp/chatstats-setup.sh

echo "✅ Dependencies installed"
echo ""

# Step 6: Clone ChatStats
echo "Step 6: Setting up ChatStats..."

cat > /tmp/chatstats-clone.sh <<EOF
#!/bin/bash
set -e

# Note: In production, you'd clone from git
# For now, we'll create a placeholder structure
mkdir -p ~/chatstats
cd ~/chatstats

echo "Placeholder for ChatStats repository"
echo "In production, run: git clone $CHATSTATS_REPO"
echo ""
echo "Manual steps after VM creation:"
echo "1. Clone your actual ChatStats repository"
echo "2. Run: npm install"
echo "3. Run: pip install -r requirements.txt"
echo "4. Run: npm run build:tsc"
EOF

tart run "$BASE_VM_NAME" -- bash < /tmp/chatstats-clone.sh

echo "✅ ChatStats setup placeholder created"
echo ""

# Step 7: Stop VM
echo "Step 7: Stopping VM..."
kill $TART_PID 2>/dev/null || true
tart stop "$BASE_VM_NAME"
sleep 5
echo "✅ VM stopped"
echo ""

# Step 8: Create snapshot
echo "Step 8: Creating snapshot..."
tart clone "$BASE_VM_NAME" "$SNAPSHOT_NAME"
echo "✅ Snapshot '$SNAPSHOT_NAME' created"
echo ""

# Step 9: Cleanup
echo "Step 9: Cleaning up..."
tart delete "$BASE_VM_NAME"
echo "✅ Base VM removed"
echo ""

# Cleanup temp files
rm -f /tmp/chatstats-setup.sh /tmp/chatstats-clone.sh

echo "========================================"
echo "✅ Setup Complete!"
echo "========================================"
echo ""
echo "Snapshot '$SNAPSHOT_NAME' is ready to use."
echo ""
echo "Next steps:"
echo "1. Initialize VM pool:"
echo "   node vm-pool-init.ts"
echo ""
echo "2. Or use in tests:"
echo "   const pool = new VMPool();"
echo "   await pool.init();"
echo ""
echo "Note: You'll need to manually install ChatStats in the snapshot"
echo "      or modify this script to clone from your actual repository."
echo ""

