// Rule 11: the same shared/constants.json the Python core reads.  Vite inlines
// the JSON at build time, so there is exactly one file to edit and no drift.
//
//   import { C } from "./constants";
//   C.ui.motion_ms       -- typed, autocompleted, never a magic number in JSX
import constants from "../../shared/constants.json";

export const C = constants;
export type Constants = typeof constants;

// Anything derived from a constant belongs here too, never inline in a component.
export const API = (path: string) => `/api/${path.replace(/^\//, "")}`;
