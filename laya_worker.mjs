// Mini Jarvis - Phase 2: Laya worker.
//
// A persistent Node.js process that loads the Laya ONNX model once and then
// answers choice questions from Python over JSON lines on stdin/stdout.
//
//   stdin:  {"id": 1, "state": "open chrome", "options": {"OPEN_APPLICATION": "...", ...}}
//   stdout: {"ready": true} once the model is warm, then per request:
//           {"id": 1, "probabilities": {"OPEN_APPLICATION": 0.93, ...}}
//           {"id": 1, "error": "..."} on failure for that request.
//
// stderr carries download/load progress - Python routes it to a log file so
// stdout stays a clean protocol channel.

import { Laya } from "@receptron/laya";
import { createInterface } from "node:readline";

const QUESTION = "intent";
const INSTRUCTIONS = "What is the user asking for?";

async function main() {
  const laya = await Laya.load({
    onProgress: ({ file, received, total }) => {
      process.stderr.write(`laya: ${file} ${received}/${total}\n`);
    },
  });

  process.stdout.write(JSON.stringify({ ready: true }) + "\n");

  const rl = createInterface({ input: process.stdin });
  for await (const line of rl) {
    if (!line.trim()) continue;
    let req;
    try {
      req = JSON.parse(line);
    } catch {
      process.stdout.write(JSON.stringify({ error: "bad json line" }) + "\n");
      continue;
    }
    try {
      const result = await laya.systemOne(req.state, {
        [QUESTION]: {
          type: "choice",
          instructions: req.instructions || INSTRUCTIONS,
          criteria: req.options,
        },
      });
      const probabilities = result.answers[QUESTION].probabilities;
      process.stdout.write(
        JSON.stringify({ id: req.id, probabilities }) + "\n"
      );
    } catch (e) {
      const message = e && e.message ? e.message : String(e);
      process.stdout.write(
        JSON.stringify({ id: req.id, error: message }) + "\n"
      );
    }
  }
}

main().catch((e) => {
  process.stderr.write(`fatal: ${(e && e.stack) || e}\n`);
  process.exit(1);
});
