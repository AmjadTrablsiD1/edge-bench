// Rule 1: two themes, both hued, applied as CSS variables from shared/themes.json.
// Components then use var(--surface), var(--accent)... and never a hex literal.
import themes from "../../shared/themes.json";
import { C } from "./constants";

export type ThemeName = Exclude<keyof typeof themes, "_comment">;
export const THEMES = themes as Record<ThemeName, Record<string, string>>;
const KEY = `${C.app.id}.theme`;

export function applyTheme(name: ThemeName): void {
  const theme = THEMES[name];
  const root = document.documentElement;
  for (const [token, value] of Object.entries(theme)) {
    if (token === "label" || token === "scheme") continue;
    root.style.setProperty(`--${token}`, value);
  }
  // Tells the browser to draw native scrollbars and form controls to match.
  root.style.colorScheme = theme.scheme;
  root.dataset.theme = name;
  localStorage.setItem(KEY, name);
}

export function initialTheme(): ThemeName {
  const saved = localStorage.getItem(KEY) as ThemeName | null;
  if (saved && saved in THEMES) return saved;
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const fallback = C.ui.default_theme as ThemeName;
  return prefersDark ? fallback : ("daylight" as ThemeName);
}
