import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import electron from "vite-plugin-electron/simple"

// Two modes, matching the orb plan's test order (section 6):
//   `vite`                    -> browser only, for the standalone widget test
//   `vite --mode electron`    -> adds the Electron shell around the same app
export default defineConfig(({ mode }) => ({
  plugins: [
    react(),
    ...(mode === "electron"
      ? [
          electron({
            main: { entry: "electron/main.ts" },
            preload: { input: "electron/preload.ts" },
          }),
        ]
      : []),
  ],
}))
