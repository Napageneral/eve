import { spawn } from 'child_process';
import path from 'path';

const appRoot = path.join(__dirname, '..');

const proc = spawn('npx', ['playwright', 'test', '--headed'], {
  cwd: appRoot,
  stdio: 'pipe',
});

let eveResponded = false;
let analysisComplete = false;

proc.stdout.on('data', (data) => {
  const output = data.toString();
  process.stdout.write(output);
  
  // Track success signals
  if (output.includes('✅ Eve responded') && !eveResponded) {
    eveResponded = true;
    console.log('\n╔═══════════════════════════════╗');
    console.log('║  EVE IS RESPONDING! 🎉       ║');
    console.log('╚═══════════════════════════════╝\n');
  }
  
  if (output.includes('Historic analysis COMPLETE') && !analysisComplete) {
    analysisComplete = true;
    console.log('\n╔═══════════════════════════════╗');
    console.log('║  ANALYSIS WORKING! 🎉        ║');
    console.log('╚═══════════════════════════════╝\n');
  }
  
  // Kill early on critical errors
  if (output.includes('unable to open database')) {
    console.error('\n❌ DATABASE ERROR - Killing test early\n');
    proc.kill();
    process.exit(1);
  }
  
  if (output.includes('ECONNREFUSED') && output.includes('Eve')) {
    console.error('\n❌ EVE CONNECTION REFUSED - Backend not ready?\n');
  }
});

proc.stderr.on('data', (data) => {
  process.stderr.write(data);
});

proc.on('close', (code) => {
  console.log(`\n╔═══════════════════════════════╗`);
  console.log(`║  Test exited with code ${code}     ║`);
  console.log(`║  Eve responded: ${eveResponded ? 'YES ✅' : 'NO ❌'}      ║`);
  console.log(`║  Analysis complete: ${analysisComplete ? 'YES ✅' : 'NO ❌'}  ║`);
  console.log(`╚═══════════════════════════════╝\n`);
  process.exit(code || 0);
});






