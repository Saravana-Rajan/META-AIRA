/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend WebSocket URL baked in at build time, e.g. wss://aira-xyz.run.app/ws */
  readonly VITE_BACKEND_WS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
