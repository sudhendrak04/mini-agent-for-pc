// Throwaway demo server for the orb plan's section 6.1 test: cycles the
// six message shapes from section 5.1 so every row of the section 5.2
// mapping table can be checked in a plain browser tab. Not part of the
// real backend - the Python broadcaster replaces this in Stage 3.
import { WebSocketServer } from "ws"

const SHAPES = [
  { state: "idle" },
  { state: "listening" },
  { state: "transcribing" },
  { state: "thinking", detail: "classifying" },
  { state: "confirming", detail: "Delete final_report.docx?" },
  { state: "acting", visual: "searching", detail: "google search: python asyncio" },
  { state: "acting", visual: "working", detail: "opening chrome" },
  {
    state: "acting",
    visual: "working",
    detail: "moved C:\\Users\\Admin\\Desktop\\a-very-long-file-name-example.txt to documents",
  },
]

const HOLD_MS = {
  idle: 3000,
  listening: 2500,
  transcribing: 2000,
  thinking: 2500,
  confirming: 4000,
  acting: 3000,
}

const wss = new WebSocketServer({ port: 8765 })
console.log("[demo] listening on ws://localhost:8765 - cycling orb states")

wss.on("connection", (socket) => {
  console.log("[demo] orb connected")
  let index = 0
  let timer

  const send = () => {
    const msg = SHAPES[index % SHAPES.length]
    socket.send(JSON.stringify(msg))
    console.log("[demo] sent", JSON.stringify(msg))
    index += 1
    timer = setTimeout(send, HOLD_MS[msg.state] ?? 2500)
  }

  send()
  socket.on("close", () => {
    clearTimeout(timer)
    console.log("[demo] orb disconnected")
  })
})
