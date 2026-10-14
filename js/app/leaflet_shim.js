// js/app/leaflet_shim.js
// Injected by esbuild for every free ``L`` in the bundle (build.mjs), Geoman's
// IIFE included. Our Leaflet stays private: the page's L is put back.
import {hadL, outerL} from "./keep_global_l.js";
import * as Leaflet from "leaflet";

if (hadL) globalThis.L = outerL;
else delete globalThis.L;

// Mutable: Geoman assigns L.PM; a module namespace object is frozen.
export const L = {...Leaflet};
