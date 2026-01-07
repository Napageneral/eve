import { spawn } from 'child_process';
import { promisify } from 'util';
import { exec } from 'child_process';

const execAsync = promisify(exec);

export interface VMConfig {
  provider: 'tart';
  image: string;
  snapshot: string;
  poolSize: number;
  cpu: number;
  memory: string;
  disk?: string;
}

export interface VM {
  id: string;
  name: string;
  status: 'ready' | 'running' | 'cleaning';
  acquiredAt?: string;
}

export interface ExecResult {
  stdout: string;
  stderr: string;
  exitCode: number;
}

/**
 * Tart VM adapter for macOS VMs on Apple Silicon
 */
export class TartAdapter {
  constructor(private config: VMConfig) {}

  /**
   * Create a new VM from snapshot
   */
  async createVM(name: string): Promise<VM> {
    try {
      // Clone from snapshot
      await execAsync(`tart clone ${this.config.snapshot} ${name}`);
      
      return {
        id: name,
        name,
        status: 'ready',
      };
    } catch (error) {
      throw new Error(`Failed to create VM ${name}: ${error}`);
    }
  }

  /**
   * Start a VM
   */
  async startVM(vm: VM): Promise<void> {
    try {
      // Start VM in background
      await execAsync(`tart run ${vm.name} --no-graphics &`);
      
      // Wait for VM to be ready (check SSH or health endpoint)
      await this.waitForReady(vm);
    } catch (error) {
      throw new Error(`Failed to start VM ${vm.name}: ${error}`);
    }
  }

  /**
   * Stop a VM
   */
  async stopVM(vm: VM): Promise<void> {
    try {
      await execAsync(`tart stop ${vm.name}`);
    } catch (error) {
      // Ignore error if VM is already stopped
      console.warn(`Warning stopping VM ${vm.name}: ${error}`);
    }
  }

  /**
   * Revert VM to snapshot
   */
  async revertToSnapshot(vm: VM, snapshot: string): Promise<void> {
    try {
      // Stop VM if running
      await this.stopVM(vm);
      
      // Delete current VM
      await execAsync(`tart delete ${vm.name}`);
      
      // Clone fresh from snapshot
      await execAsync(`tart clone ${snapshot} ${vm.name}`);
    } catch (error) {
      throw new Error(`Failed to revert VM ${vm.name} to snapshot: ${error}`);
    }
  }

  /**
   * Destroy a VM
   */
  async destroyVM(vm: VM): Promise<void> {
    try {
      await this.stopVM(vm);
      await execAsync(`tart delete ${vm.name}`);
    } catch (error) {
      throw new Error(`Failed to destroy VM ${vm.name}: ${error}`);
    }
  }

  /**
   * Execute command in VM
   */
  async exec(vm: VM, command: string): Promise<ExecResult> {
    try {
      const { stdout, stderr } = await execAsync(`tart run ${vm.name} -- ${command}`);
      return {
        stdout,
        stderr,
        exitCode: 0,
      };
    } catch (error: any) {
      return {
        stdout: error.stdout || '',
        stderr: error.stderr || '',
        exitCode: error.code || 1,
      };
    }
  }

  /**
   * Copy file from host to VM
   */
  async copyToVM(vm: VM, localPath: string, vmPath: string): Promise<void> {
    try {
      // Use tart's shared folder or scp
      // For now, assuming shared folder is configured
      await execAsync(`tart run ${vm.name} -- mkdir -p ${vmPath}`);
      await execAsync(`tart run ${vm.name} -- cp -r /mnt/chatstats-host/${localPath} ${vmPath}`);
    } catch (error) {
      throw new Error(`Failed to copy to VM ${vm.name}: ${error}`);
    }
  }

  /**
   * Copy file from VM to host
   */
  async copyFromVM(vm: VM, vmPath: string, localPath: string): Promise<void> {
    try {
      // Use tart's shared folder or scp
      await execAsync(`tart run ${vm.name} -- cp -r ${vmPath} /mnt/chatstats-host/${localPath}`);
    } catch (error) {
      throw new Error(`Failed to copy from VM ${vm.name}: ${error}`);
    }
  }

  /**
   * Wait for VM to be ready
   */
  private async waitForReady(vm: VM, timeoutMs: number = 60000): Promise<void> {
    const startTime = Date.now();
    
    while (Date.now() - startTime < timeoutMs) {
      try {
        // Try to execute a simple command
        const result = await this.exec(vm, 'echo ready');
        if (result.exitCode === 0) {
          return;
        }
      } catch (error) {
        // VM not ready yet, continue waiting
      }
      
      // Wait 2 seconds before retry
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
    
    throw new Error(`VM ${vm.name} did not become ready within ${timeoutMs}ms`);
  }

  /**
   * Check if VM exists
   */
  async vmExists(name: string): Promise<boolean> {
    try {
      const { stdout } = await execAsync('tart list');
      return stdout.includes(name);
    } catch (error) {
      return false;
    }
  }

  /**
   * Get VM status
   */
  async getVMStatus(name: string): Promise<'running' | 'stopped' | 'unknown'> {
    try {
      const { stdout } = await execAsync(`tart list`);
      const lines = stdout.split('\n');
      
      for (const line of lines) {
        if (line.includes(name)) {
          if (line.includes('running')) {
            return 'running';
          } else {
            return 'stopped';
          }
        }
      }
      
      return 'unknown';
    } catch (error) {
      return 'unknown';
    }
  }
}

