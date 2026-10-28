/**
 * The voice orb: three dashed sound rings around a glowing core with the four-point AIRA spark.
 * Purely visual (aria-hidden). Its colour follows `--hue` from the nearest `.screen[data-status]`,
 * and `setLevel` (0..1, mic loudness) swells it.
 */
const SVG = "http://www.w3.org/2000/svg";
const STAR = "M50 5 Q55 45 95 50 Q55 55 50 95 Q45 55 5 50 Q45 45 50 5 Z";
const RINGS = [47, 41, 35];

export function buildOrb(): HTMLElement {
  const orb = document.createElement("span");
  orb.className = "orb";

  const rings = document.createElementNS(SVG, "svg");
  rings.setAttribute("class", "orb-rings");
  rings.setAttribute("viewBox", "0 0 100 100");
  rings.setAttribute("focusable", "false");
  for (const r of RINGS) {
    const c = document.createElementNS(SVG, "circle");
    c.setAttribute("class", "orb-ring");
    c.setAttribute("cx", "50");
    c.setAttribute("cy", "50");
    c.setAttribute("r", String(r));
    c.setAttribute("pathLength", "100");
    rings.appendChild(c);
  }

  const core = document.createElement("span");
  core.className = "orb-core";
  const glow = document.createElement("span");
  glow.className = "orb-glow";
  const spark = document.createElementNS(SVG, "svg");
  spark.setAttribute("class", "orb-spark");
  spark.setAttribute("viewBox", "0 0 100 100");
  spark.setAttribute("focusable", "false");
  const path = document.createElementNS(SVG, "path");
  path.setAttribute("d", STAR);
  spark.appendChild(path);
  core.append(glow, spark);

  orb.append(rings, core);
  return orb;
}

/** Fill every `[data-orb]` slot under `root` with an orb; returns the orbs created. */
export function mountOrbs(root: ParentNode = document): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>("[data-orb]")).map((slot) => {
    const orb = buildOrb();
    slot.replaceChildren(orb);
    return orb;
  });
}

export function setLevel(orb: HTMLElement, level: number): void {
  const lv = Number.isFinite(level) ? Math.min(1, Math.max(0, level)) : 0;
  orb.style.setProperty("--lv", lv.toFixed(3));
}
