import { TartAdapter, VM, VMConfig, ExecResult } from './tart-adapter';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * VM Pool for managing a pool of ready VMs
 */
export class VMPool {
  private adapter: TartAdapter;
  private config: VMConfig;
  private pool: VM[] = [];
  private inUse: Set<string> = new Set();

  constructor(configPath?: string) {
    const configFile = configPath || path.join(__dirname, 'config.json');
    this.config = JSON.parse(fs.readFileSync(configFile, 'utf-8'));
    this.adapter = new TartAdapter(this.config);
  }

  /**
   * Initialize the VM pool
   */
  async init(): Promise<void> {
    console.log(`Initializing VM pool with ${this.config.poolSize} VMs...`);
    
    // Check if snapshot exists
    if (!await this.adapter.vmExists(this.config.snapshot)) {
      throw new Error(`Snapshot '${this.config.snapshot}' does not exist. Run setup-vm.sh first.`);
    }

    // Create pool VMs
    for (let i = 1; i <= this.config.poolSize; i++) {
      const vmName = `chatstats-pool-${i}`;
      
      // Check if VM already exists
      if (await this.adapter.vmExists(vmName)) {
        console.log(`VM ${vmName} already exists, reusing...`);
        this.pool.push({
          id: vmName,
          name: vmName,
          status: 'ready',
        });
      } else {
        console.log(`Creating VM ${vmName}...`);
        const vm = await this.adapter.createVM(vmName);
        this.pool.push(vm);
      }
    }

    console.log(`VM pool initialized with ${this.pool.length} VMs`);
  }

  /**
   * Acquire a VM from the pool
   */
  async acquire(): Promise<VM> {
    // Find a ready VM
    const vm = this.pool.find(v => !this.inUse.has(v.id) && v.status === 'ready');
    
    if (!vm) {
      throw new Error('No VMs available in pool. All VMs are in use.');
    }

    // Mark as in use
    this.inUse.add(vm.id);
    vm.status = 'running';
    vm.acquiredAt = new Date().toISOString();

    console.log(`Acquired VM: ${vm.name}`);
    return vm;
  }

  /**
   * Release a VM back to the pool
   */
  async release(vm: VM): Promise<void> {
    console.log(`Releasing VM: ${vm.name}...`);
    
    // Revert to snapshot (async, non-blocking)
    vm.status = 'cleaning';
    
    // Remove from in-use set
    this.inUse.delete(vm.id);
    
    // Revert to clean state
    try {
      await this.adapter.revertToSnapshot(vm, this.config.snapshot);
      vm.status = 'ready';
      console.log(`VM ${vm.name} reverted to snapshot and ready`);
    } catch (error) {
      console.error(`Failed to revert VM ${vm.name}:`, error);
      // Mark as ready anyway, but might be dirty
      vm.status = 'ready';
    }
  }

  /**
   * Resize the pool (add or remove VMs)
   */
  async resize(newSize: number): Promise<void> {
    const currentSize = this.pool.length;
    
    if (newSize > currentSize) {
      // Add VMs
      for (let i = currentSize + 1; i <= newSize; i++) {
        const vmName = `chatstats-pool-${i}`;
        console.log(`Creating VM ${vmName}...`);
        const vm = await this.adapter.createVM(vmName);
        this.pool.push(vm);
      }
    } else if (newSize < currentSize) {
      // Remove VMs (only if not in use)
      const vmsToRemove = this.pool.slice(newSize);
      for (const vm of vmsToRemove) {
        if (!this.inUse.has(vm.id)) {
          console.log(`Destroying VM ${vm.name}...`);
          await this.adapter.destroyVM(vm);
          this.pool = this.pool.filter(v => v.id !== vm.id);
        } else {
          console.warn(`Cannot remove VM ${vm.name}: currently in use`);
        }
      }
    }

    console.log(`Pool resized to ${this.pool.length} VMs`);
  }

  /**
   * Destroy all VMs in the pool
   */
  async destroy(): Promise<void> {
    console.log('Destroying VM pool...');
    
    for (const vm of this.pool) {
      if (this.inUse.has(vm.id)) {
        console.warn(`VM ${vm.name} is in use, stopping anyway...`);
      }
      await this.adapter.destroyVM(vm);
    }

    this.pool = [];
    this.inUse.clear();
    console.log('VM pool destroyed');
  }

  /**
   * Get pool status
   */
  getStatus(): {
    total: number;
    ready: number;
    running: number;
    cleaning: number;
  } {
    return {
      total: this.pool.length,
      ready: this.pool.filter(v => v.status === 'ready').length,
      running: this.pool.filter(v => v.status === 'running').length,
      cleaning: this.pool.filter(v => v.status === 'cleaning').length,
    };
  }
}

/**
 * Helper function to run a test in a VM
 */
export async function runTestInVM(
  pool: VMPool,
  testCommand: string,
  options?: {
    copyFiles?: { local: string; vm: string }[];
    copyArtifacts?: { vm: string; local: string }[];
  }
): Promise<ExecResult> {
  const vm = await pool.acquire();
  
  try {
    // Copy files to VM if specified
    if (options?.copyFiles) {
      for (const { local, vm: vmPath } of options.copyFiles) {
        await vm.copyToVM?.(local, vmPath);
      }
    }

    // Execute test command
    const result = await vm.exec?.(testCommand) || {
      stdout: '',
      stderr: 'VM exec not available',
      exitCode: 1,
    };

    // Copy artifacts back if specified
    if (options?.copyArtifacts && result.exitCode === 0) {
      for (const { vm: vmPath, local } of options.copyArtifacts) {
        await vm.copyFromVM?.(vmPath, local);
      }
    }

    return result;
  } finally {
    await pool.release(vm);
  }
}

