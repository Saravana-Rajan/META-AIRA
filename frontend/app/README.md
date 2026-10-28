# AIRA phone app

Voice-first PWA for a blind shop owner. Vanilla TypeScript + Vite, no framework (~7.5 kB JS gzipped).
It talks to the backend WebSocket `/ws` using the protocol in `backend/aira/contracts.py`, which `src/contracts.ts` mirrors.

## Run

```bash
cd frontend/app
npm install
npm run dev        # http://localhost:5173 (also on your LAN IP)
npm run build      # tsc --noEmit (strict) + vite build -> dist/
npm run preview    # serve dist/ on :4173
```

### Backend URL
- **Dev (`npm run dev`)**: `?ws=wss://host/ws` query param, then `VITE_BACKEND_WS`, then `ws://localhost:8080/ws`.
- **Production build**: only the build-time `VITE_BACKEND_WS` is used, e.g.
  `VITE_BACKEND_WS=wss://aira-backend.example.run.app/ws npm run build`. If it is unset, the app uses the same origin (`wss://<host>/ws`).
  The `?ws=` override is **ignored in production**, so a crafted link can't send your audio, frames or key to another server.

### App key
Open the app once with `?key=YOUR_APP_KEY`. The key is saved in localStorage and then removed from the address bar and history with `history.replaceState`. It is never built into the bundle, and it is only sent in `hello` to the configured backend.

Other params: `?lang=en|ta|hi` (otherwise the language picker choice saved in localStorage is used).

### On a phone
The mic, camera and AudioWorklet need a **secure context** (HTTPS, or `localhost`). For a LAN test, use an HTTPS tunnel
(e.g. `cloudflared tunnel --url http://localhost:5173`) or deploy `dist/` behind HTTPS. On iOS Safari, audio and speech start only after the first tap.

## How to use (owner)
| Gesture | Action |
|---|---|
| Double tap anywhere (<350 ms) | Start / stop talking (push-to-talk toggle). Starting also interrupts AIRA (barge-in). |
| Long press (>=600 ms) | **Stop**: cancels speech, stops the mic, sends `stop` (pending confirmations are rejected). |
| Single tap | Repeat the last AIRA sentence |
| Screen reader double-tap / Enter / Space | Same as double tap |
| Escape | Stop |

On a confirmation, a full-screen **Yes / No** screen appears (it is also announced). Saying "yes" works too, because the agent handles it.
"Type instead" at the bottom sends `{type:"text"}`, for demos and as an accessibility fallback.

## Files
- `src/contracts.ts`: TS mirror of `contracts.py` plus `parseInbound` (fills pydantic defaults).
- `src/connection.ts`: WS URL policy, app key handling, hello on every open, jittered exponential backoff reconnect (0.5 s to 10 s).
- `src/mic.ts` + `public/pcm-worklet.js`: getUserMedia (echo cancellation, noise suppression and auto gain on) to an AudioWorklet that downmixes, linearly resamples to 24 kHz, and sends PCM16 LE in 1920-sample (3840-byte, 80 ms) chunks. Audio streams only while talking is on, the socket is open, and AIRA is silent (echo guard).
- `src/speech.ts`: speechSynthesis voice selection (ta-IN / hi-IN / en-IN with fallbacks). Splits text into sentences to work around Chrome's cut-off. Has a watchdog for engines that never fire `onend`, plus cancel.
- `src/camera.ts`: rear camera, started on the first gesture. Captures JPEGs on `request_frame` (max side 768 px, quality 0.8, OffscreenCanvas with a canvas fallback).
- `src/cues.ts`: WebAudio earcons and vibration patterns for `ack/thinking/done/warn/match/mismatch/unsure` and local cues.
- `src/gestures.ts`: double-tap, long-press and single-tap detection, plus screen-reader and keyboard mapping.
- `src/i18n.ts`: UI strings in en/ta/hi, with the language persisted.
- `public/sw.js`: offline shell (network-first pages, stale-while-revalidate assets; never touches the WS). `public/manifest.webmanifest`, icons (`npm run icons` regenerates the PNGs).
