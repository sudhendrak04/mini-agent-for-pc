import { app, BrowserWindow, ipcMain } from "electron"
import fs from "node:fs"
import path from "node:path"

// Breathing room around the pill so nothing clips at the edge, and so a
// future outer glow/shadow has room to render without getting cut off.
const PADDING = 16

// The orb must talk to whatever port the Python assistant is broadcasting
// on, so read it from the assistant's config.json (default 8765 if the
// file is missing or unreadable - e.g. running the orb on its own).
const DEFAULT_PORT = 8765

function backendPort(): number {
  try {
    const configPath = path.join(__dirname, "..", "..", "config.json")
    const raw = JSON.parse(fs.readFileSync(configPath, "utf8"))
    const port = Number(raw.orb_websocket_port)
    return Number.isInteger(port) && port > 0 ? port : DEFAULT_PORT
  } catch {
    return DEFAULT_PORT
  }
}

ipcMain.handle("orb-config", () => ({ port: backendPort() }))

let win: BrowserWindow | null = null
let shown = false

function createOrbWindow() {
  win = new BrowserWindow({
    width: 140,
    height: 90,
    show: false,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: false,
    skipTaskbar: true,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
    },
  })
  win.setAlwaysOnTop(true, "screen-saver")
  if (process.env.VITE_DEV_SERVER_URL) {
    win.loadURL(process.env.VITE_DEV_SERVER_URL)
  } else {
    win.loadFile(path.join(__dirname, "../dist/index.html"))
  }
  return win
}

ipcMain.on(
  "orb-resize",
  (_event, { width, height }: { width: number; height: number }) => {
    if (!win) return
    const bounds = win.getBounds()
    win.setBounds({
      x: bounds.x,
      y: bounds.y,
      width: Math.ceil(width) + PADDING,
      height: Math.ceil(height) + PADDING,
    })
    // Hidden until the first real measurement replaces the initial guess,
    // so the orb never appears at a "wrong" size (see fix notes).
    if (!shown) {
      shown = true
      win.show()
    }
  }
)

app.whenReady().then(createOrbWindow)
app.on("window-all-closed", () => app.quit())
