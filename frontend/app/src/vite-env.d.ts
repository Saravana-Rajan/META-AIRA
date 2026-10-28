/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend WebSocket URL baked in at build time, e.g. wss://aira-xyz.run.app/ws */
  readonly VITE_BACKEND_WS?: string;
  /** Shared demo key (same as the backend APP_KEY), built in at deploy time */
  readonly VITE_AIRA_APP_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
