// Types injected by the Electron preload bridge (see electron/preload.ts).
export interface OrbApi {
  resize: (width: number, height: number) => void
  getConfig: () => Promise<{ port: number }>
}

declare global {
  interface Window {
    orbAPI?: OrbApi
  }
}
