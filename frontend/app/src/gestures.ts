/**
 * Whole-screen gestures:
 *  - double tap (second tap within 350 ms)  -> onDoubleTap
 *  - long press (held >= 600 ms)           -> onLongPress (fires while still held)
 *  - single tap (no second tap in 350 ms)   -> onSingleTap
 * Screen readers (TalkBack/VoiceOver) swallow raw touches and deliver one synthetic click
 * for their own "double tap to activate"; that click (no pointer sequence) maps to onDoubleTap.
 * Keyboard: Enter/Space -> onDoubleTap, Escape -> onLongPress.
 */
export interface GestureHandlers {
  onDoubleTap: () => void;
  onLongPress: () => void;
  onSingleTap: () => void;
  /** Called on every pointerdown/keydown/click — use to unlock audio on iOS. */
  onAnyGesture: () => void;
}

const DOUBLE_TAP_MS = 350;
const LONG_PRESS_MS = 600;
const MOVE_TOLERANCE_PX = 30;

export function attachGestures(el: HTMLElement, h: GestureHandlers): void {
  let downAt = 0;
  let downX = 0;
  let downY = 0;
  let pressTimer: number | undefined;
  let longFired = false;
  let lastTapAt = 0;
  let singleTimer: number | undefined;
  let lastPointerAt = 0;
  let activePointer: number | null = null;

  const clearPress = (): void => {
    window.clearTimeout(pressTimer);
    pressTimer = undefined;
  };

  el.addEventListener("pointerdown", (e: PointerEvent) => {
    if (activePointer !== null) return; // ignore multi-touch
    activePointer = e.pointerId;
    h.onAnyGesture();
    lastPointerAt = performance.now();
    downAt = lastPointerAt;
    downX = e.clientX;
    downY = e.clientY;
    longFired = false;
    clearPress();
    pressTimer = window.setTimeout(() => {
      longFired = true;
      lastTapAt = 0;
      window.clearTimeout(singleTimer);
      h.onLongPress();
    }, LONG_PRESS_MS);
  });

  el.addEventListener("pointermove", (e: PointerEvent) => {
    if (e.pointerId !== activePointer || pressTimer === undefined) return;
    if (Math.hypot(e.clientX - downX, e.clientY - downY) > MOVE_TOLERANCE_PX) clearPress();
  });

  const end = (e: PointerEvent, cancelled: boolean): void => {
    if (e.pointerId !== activePointer) return;
    activePointer = null;
    clearPress();
    lastPointerAt = performance.now();
    if (cancelled || longFired) return;
    if (lastPointerAt - downAt >= LONG_PRESS_MS) return;
    const now = lastPointerAt;
    if (now - lastTapAt < DOUBLE_TAP_MS) {
      lastTapAt = 0;
      window.clearTimeout(singleTimer);
      h.onDoubleTap();
    } else {
      lastTapAt = now;
      window.clearTimeout(singleTimer);
      singleTimer = window.setTimeout(() => {
        if (lastTapAt === now) {
          lastTapAt = 0;
          h.onSingleTap();
        }
      }, DOUBLE_TAP_MS);
    }
  };
  el.addEventListener("pointerup", (e) => end(e, false));
  el.addEventListener("pointercancel", (e) => end(e, true));

  // Screen-reader activation / synthetic clicks.
  el.addEventListener("click", (e: MouseEvent) => {
    e.preventDefault();
    if (performance.now() - lastPointerAt < 800) return; // already handled via pointer events
    h.onAnyGesture();
    h.onDoubleTap();
  });

  el.addEventListener("keydown", (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (e.repeat) return;
      h.onAnyGesture();
      h.onDoubleTap();
    } else if (e.key === "Escape") {
      e.preventDefault();
      h.onAnyGesture();
      h.onLongPress();
    }
  });

  el.addEventListener("contextmenu", (e) => e.preventDefault());
}
