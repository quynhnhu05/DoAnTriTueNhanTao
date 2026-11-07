const { app, BrowserWindow, ipcMain, shell } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

const APP_TITLE = 'Trợ lý du lịch thông minh';
const PROJECT_NAME = 'doantrituenhantao';
const FRONTEND_URL = 'http://127.0.0.1:8080';
const PROJECT_DIR = path.resolve(__dirname, '..');
const LOCAL_PROJECT_DIR = process.env.TRAVEL_PATH_PROJECT_DIR || 'D:\\DoAnTriTueNhanTao';
const PACKAGED_RUNTIME_DIR = path.join(process.resourcesPath, 'travel-runtime');
// When installed on the development machine, use its existing data and media
// directories instead of the intentionally small runtime copy bundled in the
// installer. Fall back to bundled resources on a machine without the project.
const COMPOSE_DIR = app.isPackaged
  ? (fs.existsSync(path.join(LOCAL_PROJECT_DIR, 'docker-compose.yml'))
      ? LOCAL_PROJECT_DIR
      : PACKAGED_RUNTIME_DIR)
  : PROJECT_DIR;

let mainWindow;
let startupPromise;
let startupProcess;
let closing = false;
let finalQuit = false;
const hasSingleInstanceLock = app.requestSingleInstanceLock();

if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!mainWindow) {
      if (!closing) createWindow();
      return;
    }
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });
}

function sendStatus(message, kind = 'info', details = '') {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('travel:status', { message, kind, details });
  }
}

function runDocker(args, timeoutMs = 15 * 60 * 1000) {
  return new Promise((resolve, reject) => {
    let output = '';
    const child = spawn('docker', args, {
      cwd: COMPOSE_DIR,
      windowsHide: true,
      env: { ...process.env, DOCKER_CLI_HINTS: 'false' },
      stdio: ['ignore', 'pipe', 'pipe']
    });
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error('Docker mất quá nhiều thời gian. Hãy kiểm tra Docker Desktop rồi thử lại.'));
    }, timeoutMs);

    startupProcess = child;
    const collect = (chunk) => {
      output += chunk.toString();
      if (output.length > 12000) output = output.slice(-12000);
      const lines = output.trim().split(/\r?\n/).filter(Boolean);
      if (lines.length) sendStatus(lines[lines.length - 1]);
    };
    child.stdout.on('data', collect);
    child.stderr.on('data', collect);
    child.once('error', (error) => {
      clearTimeout(timer);
      startupProcess = undefined;
      reject(new Error(`Không chạy được Docker CLI: ${error.message}`));
    });
    child.once('close', (code) => {
      clearTimeout(timer);
      startupProcess = undefined;
      if (code === 0) resolve(output);
      else reject(new Error(`Docker Compose dừng với mã ${code}.\n${output.slice(-5000)}`));
    });
  });
}

async function startTravelServices() {
  if (startupPromise) return startupPromise;
  startupPromise = (async () => {
    sendStatus('Đang khởi động cơ sở dữ liệu, API và giao diện…');
    try {
      await runDocker([
        'compose', '-p', PROJECT_NAME,
        'up', '-d', '--build', '--wait', '--wait-timeout', '600'
      ]);
      if (closing) return;
      sendStatus('Các dịch vụ đã sẵn sàng. Đang mở ứng dụng…');
      // Open the current chat entry directly and avoid a stale cached landing page
      // from an earlier desktop build.
      await mainWindow.webContents.session.clearCache();
      await mainWindow.loadURL(`${FRONTEND_URL}/index.html?desktop=${app.getVersion()}`);
      mainWindow.setTitle(APP_TITLE);
    } catch (error) {
      startupPromise = undefined;
      if (closing) return;
      await mainWindow.loadFile(path.join(__dirname, 'startup.html'));
      sendStatus('Chưa thể khởi động ứng dụng', 'error', error.message);
    }
  })();
  return startupPromise;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    title: APP_TITLE,
    width: 1440,
    height: 940,
    minWidth: 960,
    minHeight: 680,
    show: false,
    backgroundColor: '#f3f8fb',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });

  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.webContents.on('page-title-updated', (event) => {
    event.preventDefault();
    mainWindow.setTitle(APP_TITLE);
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(FRONTEND_URL)) {
      event.preventDefault();
      if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    }
  });
  mainWindow.on('closed', () => { mainWindow = undefined; });

  mainWindow.loadFile(path.join(__dirname, 'startup.html'));
  startTravelServices();
}

if (hasSingleInstanceLock) {
  ipcMain.handle('travel:retry-startup', () => startTravelServices());

  app.whenReady().then(createWindow);
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0 && !closing) createWindow();
  });

  app.on('before-quit', (event) => {
    if (finalQuit) return;
    event.preventDefault();
    closing = true;
    if (startupProcess) startupProcess.kill();
    Promise.resolve(startupPromise).catch(() => {}).then(() =>
      runDocker(['compose', '-p', PROJECT_NAME, 'stop'], 45000).catch(() => {})
    ).finally(() => {
      finalQuit = true;
      app.exit(0);
    });
  });

  app.on('window-all-closed', () => app.quit());
}
