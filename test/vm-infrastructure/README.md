# ChatStats VM Infrastructure

**VM-based test isolation for parallel and background testing**

## Overview

This directory contains the VM infrastructure for running ChatStats tests in complete isolation. Using Tart (macOS VMs on Apple Silicon), we can:

- Run tests in parallel without port conflicts
- Run tests in background without disrupting local development
- Test in a clean environment every time (via snapshots)
- Simulate fresh installations for integration testing

## Architecture

```
Host Machine
├── VM Pool Manager
│   ├── VM 1 (ready) ← Acquire for test
│   ├── VM 2 (running test)
│   └── VM 3 (ready)
└── Base Snapshot: "chatstats-ready"
    └── macOS + Node + Python + ChatStats pre-installed
```

## Quick Start

### 1. Install Tart

```bash
brew install cirruslabs/cli/tart
```

### 2. Create Base VM

```bash
cd /Users/tyler/Desktop/projects/ChatStats/app/test/vm-infrastructure
./setup-vm.sh
```

This will:
- Download macOS Ventura base image
- Install dependencies (Node, Python, Homebrew)
- Clone and build ChatStats
- Create snapshot "chatstats-ready"

### 3. Initialize VM Pool

```typescript
import { VMPool } from './vm-pool';

const pool = new VMPool();
await pool.init(); // Creates 3 VMs from snapshot
```

### 4. Run Tests in VM

```typescript
const vm = await pool.acquire();
try {
  // Copy current code to VM
  await vm.copyToVM('./app', '/mnt/chatstats/app');
  
  // Run tests
  const result = await vm.exec('cd /mnt/chatstats && npm run test:e2e');
  
  // Copy artifacts back
  await vm.copyFromVM('/mnt/chatstats/app/test-runs', './test-runs');
} finally {
  await pool.release(vm); // Revert to snapshot
}
```

## Configuration

**File:** `config.json`

```json
{
  "provider": "tart",
  "snapshot": "chatstats-ready",
  "poolSize": 3,
  "cpu": 4,
  "memory": "8GB"
}
```

## VM Pool API

### `VMPool`

```typescript
class VMPool {
  async init(): Promise<void>;
  async acquire(): Promise<VM>;
  async release(vm: VM): Promise<void>;
  async resize(newSize: number): Promise<void>;
  async destroy(): Promise<void>;
}
```

### `VM`

```typescript
interface VM {
  id: string;
  name: string;
  status: 'ready' | 'running' | 'cleaning';
  exec(command: string): Promise<ExecResult>;
  copyToVM(localPath: string, vmPath: string): Promise<void>;
  copyFromVM(vmPath: string, localPath: string): Promise<void>;
}
```

## Tart Adapter API

```typescript
class TartAdapter {
  async createVM(name: string): Promise<VM>;
  async startVM(vm: VM): Promise<void>;
  async stopVM(vm: VM): Promise<void>;
  async revertToSnapshot(vm: VM, snapshot: string): Promise<void>;
  async destroyVM(vm: VM): Promise<void>;
}
```

## Usage Examples

### Background Test Runner

```typescript
import { VMPool } from './vm-infrastructure/vm-pool';
import chokidar from 'chokidar';

const pool = new VMPool();
await pool.init();

chokidar.watch('app/**/*.{ts,py,tsx}').on('change', async (filePath) => {
  const vm = await pool.acquire();
  try {
    await vm.copyToVM('./app', '/mnt/chatstats/app');
    const result = await vm.exec('test-agent --git-aware --order-by-runtime');
    console.log(`Tests ${result.status}`);
  } finally {
    await pool.release(vm);
  }
});
```

### Parallel Test Execution

```typescript
const tests = ['eve-quick', 'sse-reliability', 'ia-queue-sse'];
const results = await Promise.all(
  tests.map(async (testId) => {
    const vm = await pool.acquire();
    try {
      await vm.copyToVM('./app', '/mnt/chatstats/app');
      return await vm.exec(`test-agent --tests ${testId}`);
    } finally {
      await pool.release(vm);
    }
  })
);
```

## Troubleshooting

### VM won't start

```bash
# Check Tart status
tart list

# Check VM logs
tart run chatstats-pool-1 --log
```

### Snapshot is outdated

```bash
# Rebuild snapshot
./setup-vm.sh

# Or manually update
tart run chatstats-ready
# ... make changes ...
tart stop chatstats-ready
```

### Pool is slow

- Increase CPU/memory in config.json
- Reduce pool size if host resources are limited
- Check if VMs are properly reverting to snapshot

### Port conflicts

VMs use port mapping to avoid conflicts:
- VM 1: 13030, 15173, 13031
- VM 2: 23030, 25173, 23031
- VM 3: 33030, 35173, 33031

## Performance

**VM Lifecycle:**
- Create from snapshot: ~5 seconds
- Start VM: ~15 seconds
- Run test: varies (45s - 10min)
- Revert to snapshot: ~3 seconds
- Total overhead: ~25 seconds per test

**Pool Management:**
- Keep 3 VMs hot for instant acquisition
- Cleanup happens async after release
- Typical acquire time: <1 second

## Cost

**Disk Space:**
- Base image: ~25GB
- Snapshot: ~30GB
- Each VM clone: ~2GB (copy-on-write)
- Total for 3-VM pool: ~36GB

**Memory:**
- 8GB per VM
- 24GB total for 3-VM pool
- Host needs 32GB+ RAM recommended

## Future Enhancements

- [ ] Parallel snapshot creation for faster rebuilds
- [ ] Incremental updates to snapshot (don't rebuild from scratch)
- [ ] VM health monitoring and auto-recovery
- [ ] Metrics and logging (test duration, VM usage)
- [ ] Cloud VM provider support (for CI/CD)

