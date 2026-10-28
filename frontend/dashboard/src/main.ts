import './style.css';
import { LiveConnection, fetchSnapshot, frameUrl, initKey, resolveBase } from './api';
import { App } from './app';
import { demoFrameSrc, startDemo } from './demo';

const params = new URLSearchParams(location.search);
const demo = params.get('demo') === '1' || params.get('demo') === 'true';

if (demo) {
  const app = new App({
    frameSrc: (id) => demoFrameSrc(id) ?? '',
    requestRefresh: () => {},
  });
  app.setStatus('demo');
  startDemo(app);
} else {
  void initKey().then(startLive);
}

function startLive(): void {
  const base = resolveBase();
  let refreshTimer: number | null = null;
  let inflight: AbortController | null = null;

  const refresh = async () => {
    inflight?.abort();
    const ctl = new AbortController();
    inflight = ctl;
    try {
      app.applySnapshot(await fetchSnapshot(base, ctl.signal));
    } catch (err) {
      if (!ctl.signal.aborted) console.warn('[aira] snapshot failed', err);
    }
  };
  const refreshSoon = () => {
    if (refreshTimer !== null) clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(() => {
      refreshTimer = null;
      void refresh();
    }, 400);
  };

  const app = new App({ frameSrc: (id) => frameUrl(base, id), requestRefresh: refreshSoon });
  void refresh();
  // Safety net: if a stream silently stalls, the snapshot still converges.
  window.setInterval(() => void refresh(), 15000);

  new LiveConnection(base, {
    onEvent: (e) => app.applyEvent(e),
    onStatus: (s) => app.setStatus(s),
    onReconnect: () => void refresh(),
  }).start();
}
