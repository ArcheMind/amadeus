import { execSync } from 'child_process';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

// Get the directory where the script is located, which is /backend
const __filename = fileURLToPath(import.meta.url);
const backendDir = path.dirname(__filename);
const projectRoot = path.dirname(backendDir); // Assuming backend is a subdir of project root
const venvPath = path.join(backendDir, '.venv');
const requirementsPath = path.join(backendDir, 'requirements.txt');

// Function to execute shell commands
function runCommand(command, cwd) {
  console.log(`Executing: ${command} in ${cwd}`);
  try {
    execSync(command, { stdio: 'inherit', cwd });
    console.log(`Successfully executed: ${command}`);
  } catch (error) {
    console.error(`Error executing command: ${command}`);
    console.error(error);
    process.exit(1);
  }
}

console.log('Starting backend build process...');

// Build the backend executable using PyInstaller with uv
const buildCommand = 'uv run -p 3.12 --with-requirements backend/requirements.txt pyinstaller --name=backend --distpath dist --runtime-hook backend/pyinstaller_hook.py --add-data "version.json:." backend/main.py';
runCommand(buildCommand, projectRoot);

console.log('Backend build process completed.');
