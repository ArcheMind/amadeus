const { spawn } = require('child_process');

const delay = parseInt(process.argv[2]) * 1000;
const command = process.argv[3];
const args = process.argv.slice(4);

setTimeout(() => {
  const child = spawn(command, args, {
    stdio: 'inherit',
    shell: true
  });
  
  child.on('exit', (code) => {
    process.exit(code);
  });
}, delay); 