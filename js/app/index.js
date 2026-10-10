// js/app/index.js
// The defo-alerts bundle (gui/widget/static/multiview.{js,css}, built by
// build.mjs): leaflet-multiview's anywidget adapter with the plugins whose
// tasks have landed, the app plugins, and the host CSS the package leaves to
// its page (Leaflet's and Geoman's).
import "leaflet/dist/leaflet.css";
import "@geoman-io/leaflet-geoman-free/dist/leaflet-geoman.css";
import "../leaflet-multiview/style.css";
import {L} from "./leaflet_shim.js";
// Geoman extends the shim's L (esbuild injects the shim for Geoman's free L).
// It is in the bundle before the draw plugin is: the shim exists because of
// it, and the page-L test means something only with Geoman's IIFE loaded.
import "@geoman-io/leaflet-geoman-free";
import {createWidget} from "../leaflet-multiview/src/widget/anywidget.js";
import {layerMenu} from "../leaflet-multiview/src/plugins/layer-menu/index.js";
import * as layerMenuWidget from "../leaflet-multiview/src/plugins/layer-menu/widget.js";
import {fitBounds} from "../leaflet-multiview/src/plugins/fit-bounds/index.js";
import * as fitBoundsWidget from "../leaflet-multiview/src/plugins/fit-bounds/widget.js";
import {draw} from "../leaflet-multiview/src/plugins/draw/index.js";
import * as drawWidget from "../leaflet-multiview/src/plugins/draw/widget.js";
import {holes} from "../leaflet-multiview/src/plugins/holes/index.js";
import {tileErrors} from "../leaflet-multiview/src/plugins/tile-errors/index.js";
import {resizeNudge} from "./resize_nudge.js";

export default createWidget({
  L,
  registry: {
    layerMenu: {plugin: layerMenu, bind: layerMenuWidget.bind},
    fitBounds: {plugin: fitBounds, bind: fitBoundsWidget.bind, options: fitBoundsWidget.options},
    draw: {plugin: draw, bind: drawWidget.bind},
    holes: {plugin: holes}, // no traits, so no widget binding
    tileErrors: {plugin: tileErrors}, // no traits, so no widget binding
  },
  always: [resizeNudge],
});
