const { app, BrowserWindow, ipcMain } = require('electron');
const { join, resolve } = require('path');
const { spawn } = require('child_process');
const { existsSync } = require('fs');
const { platform } = require('os');
const { GracefulKiller } = require('./utils.cjs');

let mainWindow;
let serverProcess;
let serverPort;
let serverStarted = false;
const killer = new GracefulKiller();
const MAX_LOG_LINES = 5000;
const historicalLogs = [];

function addLog(data) {
  const log = data.toString();
  // 按行分割，并添加到历史记录中
  const lines = log.split(/(\r\n|\n|\r)/);
  historicalLogs.push(...lines);

  // 保持日志缓冲区大小
  while (historicalLogs.length > MAX_LOG_LINES) {
    historicalLogs.shift();
  }

  // 如果窗口存在，则发送实时日志
  if (mainWindow) {
    mainWindow.webContents.send('backend-log', log);
  }
}

// 默认服务器配置
const defaultServerConfig = {
  // 相对于安装路径的执行路径
  execPath: 'backend',
  // 启动命令和参数
  nt_command: 'backend.exe',
  posix_command: './backend',
  args: [],
  // 环境变量
  env: {
    ELECTRON: 'true',
    FORCE_COLOR: '1'
  },
  // 端口匹配正则表达式
  portRegex: /https?:\/\/(?:localhost|127\.0\.0\.1):(\d+)/i,
  // 启动超时时间（毫秒）
  timeout: 25000
};

/**
 * 启动后端服务
 * @param {Object} config - 服务器配置
 * @param {string} config.execPath - 相对于安装路径的执行路径
 * @param {string} config.command - 启动命令
 * @param {string[]} config.args - 命令参数
 * @param {Object} config.env - 环境变量
 * @param {RegExp} config.portRegex - 端口匹配正则表达式
 * @param {number} config.timeout - 启动超时时间（毫秒）
 * @returns {Promise<number>} - 返回服务器端口
 */
async function startServer(config = defaultServerConfig) {
  // 如果服务器已经启动，直接返回端口
  if (serverStarted && serverPort) {
    console.log('[Server] Server already running on port:', serverPort);
    return serverPort;
  }

  // 合并配置
  const serverConfig = { ...defaultServerConfig, ...config };
  
  // 根据平台调整命令（Windows 需要添加 .cmd 或 .exe 后缀）
  let command = serverConfig.posix_command;
  if (platform() === 'win32') {
    command = serverConfig.nt_command;
  }
  
  // 解析服务器脚本路径
  const basePath = app.isPackaged
    ? join(process.resourcesPath, serverConfig.execPath)
    : join(__dirname, '..', 'dist', serverConfig.execPath);
  
  console.log('[Server] App is packaged:', app.isPackaged);
  console.log('[Server] Resources path:', process.resourcesPath);
  console.log('[Server] Base path:', basePath);
  
  // 解析完整的参数路径
  const fullArgs = serverConfig.args.map(arg => {
    // 如果参数是一个文件路径（不包含空格和特殊字符），则解析为完整路径
    if (/^[^/\\]*\.[^/\\]+$/.test(arg)) {
      return join(basePath, arg);
    }
    return arg;
  });
  
  // 检查主要脚本文件是否存在
  const mainScript = fullArgs.find(arg => /\.(js|ts|py|rb|php|exe)$/i.test(arg));
  if (mainScript) {
    console.log('[Server] Checking main script:', mainScript);
    if (!existsSync(mainScript)) {
      console.error('[Server] Main script not found:', mainScript);
      throw new Error(`Server script not found: ${mainScript}`);
    }
  }

  return new Promise((resolve, reject) => {
    console.log(`[Server] Starting with command: ${command} ${fullArgs.join(' ')}`);
    console.log(`[Server] Working directory: ${basePath}`);
    console.log('[Server] Environment:', serverConfig.env);
    
    serverProcess = spawn(command, fullArgs, {
      stdio: ['pipe', 'pipe', 'pipe', 'ipc'],
      env: {
        ...process.env,
        ...serverConfig.env
      },
      cwd: basePath
    });
    
    killer.setServerProcess(serverProcess);

    let buffer = '';
    let portResolved = false;
    
    const checkPortInBuffer = (data) => {
      if (portResolved) return;
      
      buffer += data.toString();
      
      const portMatch = buffer.match(serverConfig.portRegex);
      if (portMatch && !portResolved) {
        portResolved = true;
        serverPort = parseInt(portMatch[1], 10);
        serverStarted = true;
        console.log('[Server] Port resolved:', serverPort);
        resolve(serverPort);
        buffer = '';
      }
    };

    serverProcess.stdout.on('data', (data) => {
      const output = data.toString().trim();
      console.log('[Server]', output);
      addLog(data);
      checkPortInBuffer(data);
    });

    serverProcess.stderr.on('data', (data) => {
      const error = data.toString().trim();
      console.error('[Server Error]', error);
      addLog(data);
      checkPortInBuffer(data);
    });

    serverProcess.on('error', (error) => {
      console.error('[Server] Failed to start process:', error);
      reject(error);
    });

    serverProcess.on('close', (code) => {
      console.log('[Server] Process exited with code', code);
      if (code !== 0 && !killer.isQuitting) {
        console.log('[Server] Restarting server...');
        serverStarted = false;
        serverPort = null;
        killer.setServerProcess(null);
        startServer(serverConfig).then(resolve).catch(reject);
      } else {
        serverStarted = false;
        serverPort = null;
        killer.setServerProcess(null);
      }
    });

    // 超时
    setTimeout(() => {
      if (!serverStarted) {
        console.error('[Server] Startup timeout');
        reject(new Error(`Server startup timeout after ${serverConfig.timeout}ms`));
      }
    }, serverConfig.timeout);
  });
}

// 注册IPC处理程序，只需注册一次
function registerIPCHandlers() {
  if (!ipcMain.listenerCount('get-api-port')) {
    ipcMain.handle('get-api-port', () => {
      console.log('[IPC] get-api-port called, returning port:', serverPort);
      return serverPort;
    });
  }
  if (!ipcMain.listenerCount('get-historical-logs')) {
    ipcMain.handle('get-historical-logs', () => {
      console.log(`[IPC] get-historical-logs called, returning ${historicalLogs.length} lines.`);
      return historicalLogs.join('');
    });
  }
}

async function createWindow() {
  // 启动服务器（如果尚未启动）并获取端口
  try {
    console.log('[App] Starting server...');
    serverPort = await startServer();
    serverStarted = true;
    console.log('[App] Server started successfully on port:', serverPort);
  } catch (error) {
    console.error('[App] Failed to start server:', error);
    app.quit();
    return;
  }

  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      preload: join(__dirname, 'preload.cjs'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  // 在加载页面之前，先发送端口信息
  mainWindow.webContents.on('did-finish-load', () => {
    console.log('[App] Window loaded, sending port:', serverPort);
    mainWindow.webContents.send('backend-port', serverPort);
  });

  if (process.env.NODE_ENV === 'development') {
    console.log('[App] Loading development URL');
    mainWindow.loadURL('http://localhost:5173/');
    mainWindow.webContents.openDevTools();
  } else {
    console.log('[App] Loading production file');
    mainWindow.loadFile(join(__dirname, '../dist/index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  registerIPCHandlers();
  createWindow();
});

app.on('activate', function () {
  // 在 macOS 上，当点击 dock 图标并且没有其他窗口打开时，
  // 通常在应用程序中重新创建一个窗口。
  if (BrowserWindow.getAllWindows().length === 0) {
    // 服务器已经启动，只需创建新窗口
    createWindow();
  }
});

app.on('window-all-closed', function () {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
