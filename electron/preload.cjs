const { contextBridge, ipcRenderer } = require('electron');

const validInvokeChannels = ['get-api-port', 'get-historical-logs'];
const validReceiveChannels = ['backend-log', 'backend-port'];

// 创建一个自定义事件
const apiReadyEvent = new Event('api-ready');

contextBridge.exposeInMainWorld('api', {
  getApiPort: () => ipcRenderer.invoke('get-api-port'),
  getHistoricalLogs: () => ipcRenderer.invoke('get-historical-logs'),
  send: (channel, data) => {
    const validChannels = [];
    if (validChannels.includes(channel)) {
      ipcRenderer.send(channel, data);
    }
  },
  receive: (channel, listener) => {
    if (validReceiveChannels.includes(channel)) {
      // Create a new listener function that we can wrap
      const subscription = (event, ...args) => listener(...args);
      ipcRenderer.on(channel, subscription);

      // Return a cleanup function to remove the listener
      return () => {
        ipcRenderer.removeListener(channel, subscription);
      };
    }
    console.warn(`[Preload] Blocked a receive subscription to an invalid channel: ${channel}`);
  },
  invoke: (channel, ...args) => {
    if (validInvokeChannels.includes(channel)) {
      return ipcRenderer.invoke(channel, ...args);
    }
    console.warn(`[Preload] Blocked an invoke call to an invalid channel: ${channel}`);
  }
});

// 监听后端端口信息
ipcRenderer.on('backend-port', (event, port) => {
  console.log('[Preload] Received backend port:', port);
  // 触发 api-ready 事件
  window.dispatchEvent(apiReadyEvent);
});