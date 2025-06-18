const { app } = require('electron');
const { platform } = require('os');
const { spawn } = require('child_process');

/**
 * Gracefully starts and stops a child process.
 */
class GracefulKiller {
  constructor() {
    this.serverProcess = null;
    this.isQuitting = false;
    this.gracefulKillTimeout = 5000; // 5 seconds
    this.platform = platform();
    
    app.on('before-quit', (event) => {
      if (this.isQuitting) {
        return;
      }
      
      console.log('[GracefulKiller] App is quitting, attempting graceful shutdown...');
      
      if (this.serverProcess && !this.serverProcess.killed) {
        console.log('[GracefulKiller] Server process found, preventing immediate quit');
        event.preventDefault(); // Prevent app from quitting immediately
        this.isQuitting = true;
        
        this.kill(this.serverProcess).then(() => {
          console.log('[GracefulKiller] Server process killed, quitting app');
          // 使用 setImmediate 避免递归调用
          setImmediate(() => {
            app.quit();
          });
        }).catch((error) => {
          console.error('[GracefulKiller] Error during graceful shutdown:', error);
          // 即使出错也要退出应用
          setImmediate(() => {
            app.quit();
          });
        });
      } else {
        console.log('[GracefulKiller] No server process to kill');
        this.isQuitting = true;
      }
    });

    // 添加 will-quit 事件处理，作为备用
    app.on('will-quit', (event) => {
      if (this.serverProcess && !this.serverProcess.killed && !this.isQuitting) {
        console.log('[GracefulKiller] Last chance to kill server process');
        event.preventDefault();
        this.isQuitting = true;
        this.forceKill(this.serverProcess).then(() => {
          app.quit();
        });
      }
    });
  }

  setServerProcess(proc) {
    this.serverProcess = proc;
    console.log('[GracefulKiller] Server process set:', proc ? proc.pid : 'null');
  }
  
  /**
   * Kills the process, first with SIGTERM, then with SIGKILL after a timeout.
   * @param {ChildProcess} processToKill - The process to kill.
   * @returns {Promise<void>}
   */
  kill(processToKill) {
    return new Promise((resolve) => {
      if (!processToKill || processToKill.killed) {
        console.log('[GracefulKiller] Process already killed or not found');
        resolve();
        return;
      }

      console.log(`[GracefulKiller] Attempting to kill process ${processToKill.pid} on ${this.platform}`);
      
      const timeout = setTimeout(() => {
        console.log('[GracefulKiller] Process did not exit gracefully, forcing kill.');
        this.forceKill(processToKill).then(resolve);
      }, this.gracefulKillTimeout);
      
      processToKill.on('exit', (code, signal) => {
        clearTimeout(timeout);
        console.log(`[GracefulKiller] Process exited gracefully with code ${code}, signal ${signal}.`);
        resolve();
      });

      processToKill.on('error', (error) => {
        clearTimeout(timeout);
        console.error('[GracefulKiller] Process error during kill:', error);
        resolve();
      });
      
      try {
        if (this.platform === 'win32') {
          // Windows: 使用 taskkill 命令优雅地终止进程及其子进程
          console.log('[GracefulKiller] Using taskkill for Windows');
          this.killWindowsProcessTree(processToKill.pid, false);
        } else {
          // Unix: 使用 SIGTERM
          console.log('[GracefulKiller] Using SIGTERM for Unix-like system');
          processToKill.kill('SIGTERM');
        }
      } catch (error) {
        clearTimeout(timeout);
        console.error('[GracefulKiller] Error sending kill signal:', error);
        this.forceKill(processToKill).then(resolve);
      }
    });
  }

  /**
   * 强制终止进程
   * @param {ChildProcess} processToKill - The process to kill.
   * @returns {Promise<void>}
   */
  forceKill(processToKill) {
    return new Promise((resolve) => {
      if (!processToKill || processToKill.killed) {
        resolve();
        return;
      }

      console.log(`[GracefulKiller] Force killing process ${processToKill.pid}`);

      const timeout = setTimeout(() => {
        console.log('[GracefulKiller] Force kill timeout, giving up');
        resolve();
      }, 3000);

      processToKill.on('exit', () => {
        clearTimeout(timeout);
        console.log('[GracefulKiller] Process force killed successfully');
        resolve();
      });

      processToKill.on('error', (error) => {
        clearTimeout(timeout);
        console.error('[GracefulKiller] Error during force kill:', error);
        resolve();
      });

      try {
        if (this.platform === 'win32') {
          // Windows: 强制终止进程树
          this.killWindowsProcessTree(processToKill.pid, true);
        } else {
          // Unix: 使用 SIGKILL
          processToKill.kill('SIGKILL');
        }
      } catch (error) {
        clearTimeout(timeout);
        console.error('[GracefulKiller] Error during force kill:', error);
        resolve();
      }
    });
  }

  /**
   * Windows特定的进程树终止方法
   * @param {number} pid - 进程ID
   * @param {boolean} force - 是否强制终止
   */
  killWindowsProcessTree(pid, force = false) {
    try {
      const args = force 
        ? ['taskkill', '/F', '/T', '/PID', pid.toString()]
        : ['taskkill', '/T', '/PID', pid.toString()];
      
      console.log(`[GracefulKiller] Executing: ${args.join(' ')}`);
      
      const killProcess = spawn(args[0], args.slice(1), {
        stdio: 'pipe',
        windowsHide: true
      });

      killProcess.on('exit', (code) => {
        if (code === 0) {
          console.log(`[GracefulKiller] Successfully ${force ? 'force ' : ''}killed process tree ${pid}`);
        } else {
          console.log(`[GracefulKiller] taskkill exited with code ${code} for PID ${pid}`);
        }
      });

      killProcess.on('error', (error) => {
        console.error(`[GracefulKiller] Error executing taskkill: ${error}`);
      });

    } catch (error) {
      console.error(`[GracefulKiller] Failed to execute taskkill: ${error}`);
    }
  }
}

module.exports = { GracefulKiller }; 