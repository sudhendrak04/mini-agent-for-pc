// Bridge for the renderer to request window resizes: the pill is measured
// live (OrbWidget's ResizeObserver) and the window follows it, because a
// fixed window size clips long labels like "opening chrome…".
import { contextBridge, ipcRenderer } from "electron"

contextBridge.exposeInMainWorld("orbAPI", {
  resize: (width: number, height: number) =>
    ipcRenderer.send("orb-resize", { width, height }),
})
