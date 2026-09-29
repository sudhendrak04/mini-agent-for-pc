// WebSocket client for the Mini Jarvis backend (orb plan section 5).
//
// The backend (main plan section 9.3) sends one JSON message per state
// change; this hook maps each message to (orb visual, label, speed) per
// the section 5.2 table and reconnects with exponential backoff when the
// backend is down, so the orb can run before Mini Jarvis starts.
import { useEffect, useRef, useState } from "react"
import type { OrbState } from "./components/ui/thinking-orbs"

// Must match the backend's orb_websocket_port in config.json - never
// hardcode two different values (orb plan section 5.4).
const WS_URL = "ws://localhost:8765"
const RECONNECT_MIN_MS = 1000
const RECONNECT_MAX_MS = 15000

// Section 5.3, updated from the package typings: thinking-orbs 0.3.x
// ships NINE states, not six, and `breathing` (a face-on ring slowly
// morphing) is a real ambient look - calmer idle than the plan's
// shaping fallback. Disconnected keeps it dimmed and slowed.
const IDLE_SPEED = 0.6
const OFFLINE_SPEED = 0.3

export type OrbVisual = OrbState

interface StateMessage {
  state: string
  visual?: string
  detail?: string
}

function mapStateToOrb(
  msg: StateMessage
): { visual: OrbVisual; label: string; speed: number } {
  switch (msg.state) {
    case "listening":
      return { visual: "listening", label: "", speed: 1 }
    case "transcribing":
      return { visual: "composing", label: "Transcribing…", speed: 1 }
    case "thinking":
      return { visual: "solving", label: "Thinking…", speed: 1 }
    case "confirming":
      return { visual: "listening", label: msg.detail ?? "", speed: 1 }
    case "acting":
      return {
        visual: msg.visual === "searching" ? "searching" : "working",
        label: msg.detail ?? "",
        speed: 1,
      }
    case "idle":
    default:
      return { visual: "breathing", label: "", speed: IDLE_SPEED }
  }
}

export function useOrbConnection() {
  const [visual, setVisual] = useState<OrbVisual>("shaping")
  const [label, setLabel] = useState("")
  const [speed, setSpeed] = useState(IDLE_SPEED)
  const [connected, setConnected] = useState(false)
  const retryDelay = useRef(RECONNECT_MIN_MS)

  useEffect(() => {
    let ws: WebSocket | undefined
    let cancelled = false
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined

    function connect() {
      ws = new WebSocket(WS_URL)
      ws.onopen = () => {
        setConnected(true)
        retryDelay.current = RECONNECT_MIN_MS
      }
      ws.onmessage = (ev) => {
        let msg: StateMessage
        try {
          msg = JSON.parse(ev.data)
        } catch {
          return
        }
        const mapped = mapStateToOrb(msg)
        setVisual(mapped.visual)
        setLabel(mapped.label)
        setSpeed(mapped.speed)
      }
      ws.onclose = () => {
        setConnected(false)
        setVisual("breathing")
        setLabel("Mini Jarvis offline")
        setSpeed(OFFLINE_SPEED)
        if (!cancelled) {
          reconnectTimer = setTimeout(connect, retryDelay.current)
          retryDelay.current = Math.min(
            retryDelay.current * 2,
            RECONNECT_MAX_MS
          )
        }
      }
      ws.onerror = () => ws?.close()
    }

    connect()
    return () => {
      cancelled = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [])

  return { visual, label, speed, connected }
}
