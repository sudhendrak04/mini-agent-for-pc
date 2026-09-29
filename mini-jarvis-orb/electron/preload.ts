// Bridge for the renderer: window resizes (the pill is measured live) and
// the backend port read from the Python config.json - so the orb and the
// assistant can never drift apart on port numbers.
import { contextBridge, ipcRenderer } from "electron"

contextBridge.exposeInMainWorld("orbAPI", {
  resize: (width: number, height: number) =>
    ipcRenderer.send("orb-resize", { width, height }),
  getConfig: () => ipcRenderer.invoke("orb-config"),
})
