const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('travelDesktop', {
  retryStartup: () => ipcRenderer.invoke('travel:retry-startup'),
  onStatus: (callback) => {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, message) => callback(message);
    ipcRenderer.on('travel:status', listener);
    return () => ipcRenderer.removeListener('travel:status', listener);
  }
});
