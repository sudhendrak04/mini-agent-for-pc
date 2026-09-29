import { useEffect, useRef } from "react"
import { ThinkingOrb, type OrbState } from "./components/ui/thinking-orbs"
import { useOrbConnection } from "./ws"

// The visual pattern from the orb plan's demo (section 4): orb + status
// pill. The label only appears when the backend sent one, so idle stays
// a small unobtrusive orb; thinking/confirming/acting show the detail.
// The pill is measured live and the Electron window is resized to fit,
// so labels of any length render without clipping.
export default function OrbWidget() {
  const { visual, label, speed, connected } = useOrbConnection()
  const pillRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = pillRef.current
    if (!el || !window.orbAPI) return

    const observer = new ResizeObserver(() => {
      // getBoundingClientRect(), NOT entry.contentRect: contentRect
      // excludes padding, and the pill's visible size comes from its
      // padding (0 24px 0 9px) - contentRect would undersize the window
      // by ~33px and reproduce the clipping bug in a new form.
      const rect = el.getBoundingClientRect()
      window.orbAPI!.resize(rect.width, rect.height)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  return (
    <div className="orb-drag-region">
      <div
        ref={pillRef}
        className="orb-pill"
        style={{ opacity: connected ? 1 : 0.4 }}
      >
        <span className="orb-canvas-wrap">
          <ThinkingOrb
            state={visual as OrbState}
            size={64}
            theme="dark"
            speed={speed}
          />
        </span>
        {label && <span className="orb-label">{label}</span>}
      </div>
    </div>
  )
}
