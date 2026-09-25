// CBU/KRAL live webmap - main app logic

// A real aircraft silhouette (fuselage, swept wings, tailplane), drawn
// nose-up so MapLibre's icon-rotate can point it along the heading.
const PLANE_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">' +
  '<path d="M32 4 C34.2 4 35.8 7.2 35.8 11.4 L35.8 23.6 L58 36.4 L58 41.6 ' +
  "L35.8 35.4 L35.8 47.6 L42.4 52.6 L42.4 56.4 L32 53.6 L21.6 56.4 " +
  'L21.6 52.6 L28.2 47.6 L28.2 35.4 L6 41.6 L6 36.4 L28.2 23.6 ' +
  'L28.2 11.4 C28.2 7.2 29.8 4 32 4 Z" ' +
  'fill="#38bdf8" stroke="#0f172a" stroke-width="2.5" stroke-linejoin="round"/>' +
  "</svg>";

function loadPlaneIcon() {
  return new Promise((resolve) => {
    if (map.hasImage("plane-icon")) {
      resolve();
      return;
    }
    const img = new Image();
    img.onload = () => {
      if (!map.hasImage("plane-icon")) map.addImage("plane-icon", img);
      resolve();
    };
    img.onerror = (err) => {
      console.error("Failed to load plane icon:", err);
      resolve();
    };
    img.src = "data:image/svg+xml;base64," + btoa(PLANE_SVG);
  });
}

// Top-down bus silhouette, nose-up (windshield at top) so icon-rotate works
// the same way it does for PLANE_SVG - this is the 2D (zoomed-out) shape.
const BUS_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">' +
  '<rect x="20" y="6" width="24" height="52" rx="6" ' +
  'fill="#f97316" stroke="#7c2d12" stroke-width="2.5"/>' +
  '<rect x="24" y="12" width="16" height="10" rx="2" fill="#fed7aa"/>' +
  '<rect x="24" y="26" width="16" height="6" rx="1" fill="#7c2d12" opacity="0.5"/>' +
  '<rect x="24" y="36" width="16" height="6" rx="1" fill="#7c2d12" opacity="0.5"/>' +
  '<rect x="24" y="46" width="16" height="6" rx="1" fill="#7c2d12" opacity="0.5"/>' +
  "</svg>";

function loadBusIcon() {
  return new Promise((resolve) => {
    if (map.hasImage("bus-icon")) {
      resolve();
      return;
    }
    const img = new Image();
    img.onload = () => {
      if (!map.hasImage("bus-icon")) map.addImage("bus-icon", img);
      resolve();
    };
    img.onerror = (err) => {
      console.error("Failed to load bus icon:", err);
      resolve();
    };
    img.src = "data:image/svg+xml;base64," + btoa(BUS_SVG);
  });
}

// Buses render as a small 3D box (fill-extrusion), same technique as the
// campus buildings, rather than a flat icon - this builds that box's
// footprint: a real-world-sized rectangle (roughly bus-sized) centered on
// the bus's position and rotated to its heading.
const BUS_LENGTH_M = 16;   // slightly larger than a real ~40ft bus, for visibility
const BUS_WIDTH_M = 3.4;
const BUS_3D_MINZOOM = 16.5; // 2D dot/icon below this, 3D box at/above it
const METERS_PER_DEG_LAT = 111320;

// The 3D box: four height bands sharing one footprint (see busPolygon()),
// not one solid-color slab - dark chassis, orange body, glassy windows,
// light roof. fill-extrusion can't texture a single volume, so a real-ish
// look means stacking a few flat-colored slices instead.
const BUS_BAND_LAYERS = [
  { id: "route1-bus-wheels", base: 0, height: 0.4, color: "#1f2937" },
  { id: "route1-bus-body", base: 0.4, height: 2.6, color: "#f97316" },
  { id: "route1-bus-windows", base: 2.6, height: 3.3, color: "#7dd3fc" },
  { id: "route1-bus-roof", base: 3.3, height: 4.5, color: "#e2e8f0" },
];
const BUS_2D_LAYERS = ["route1-bus-dot", "route1-bus-symbol"];
const BUS_CLICKABLE_LAYERS = [...BUS_2D_LAYERS, ...BUS_BAND_LAYERS.map((b) => b.id)];

function busPolygon(lat, lon, bearingDeg) {
  const theta = (bearingDeg * Math.PI) / 180;
  const halfLen = BUS_LENGTH_M / 2;
  const halfWid = BUS_WIDTH_M / 2;
  // Local (right, forward) offsets for each corner, forward = the bus's nose.
  const corners = [
    [-halfWid, halfLen],
    [halfWid, halfLen],
    [halfWid, -halfLen],
    [-halfWid, -halfLen],
  ];
  const metersPerDegLon = METERS_PER_DEG_LAT * Math.cos((lat * Math.PI) / 180);
  const ring = corners.map(([right, forward]) => {
    const east = right * Math.cos(theta) + forward * Math.sin(theta);
    const north = -right * Math.sin(theta) + forward * Math.cos(theta);
    return [lon + east / metersPerDegLon, lat + north / METERS_PER_DEG_LAT];
  });
  ring.push(ring[0]); // close the ring
  return ring;
}

const map = new maplibregl.Map({
  container: "map",
  style: CONFIG.TILES.STREET_STYLE_URL,
  center: CONFIG.FOCAL_POINTS.overview.center,
  zoom: CONFIG.FOCAL_POINTS.overview.zoom,
  pitch: CONFIG.FOCAL_POINTS.overview.pitch,
  bearing: CONFIG.FOCAL_POINTS.overview.bearing,
  attributionControl: true,
});

map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");

async function addCustomLayers() {
  await loadPlaneIcon();
  await loadBusIcon();

  // Real CBU campus outline (OSM amenity=university multipolygon), not a
  // synthetic circle - the campus is several separate parcels.
  if (!map.getSource("cbu-campus")) {
    map.addSource("cbu-campus", {
      type: "geojson",
      data: CONFIG.GEO_DATA.CBU_CAMPUS,
    });
  }
  if (!map.getLayer("cbu-campus-fill")) {
    map.addLayer({
      id: "cbu-campus-fill",
      type: "fill",
      source: "cbu-campus",
      paint: { "fill-color": "#2563eb", "fill-opacity": 0.12 },
    });
    map.addLayer({
      id: "cbu-campus-line",
      type: "line",
      source: "cbu-campus",
      paint: { "line-color": "#2563eb", "line-width": 3 },
    });
  }

  // CBU buildings: fill-extrusion, only rendered/clickable once zoomed
  // into the campus focal point (minzoom gate does this implicitly).
  if (!map.getSource("cbu-buildings")) {
    map.addSource("cbu-buildings", {
      type: "geojson",
      data: CONFIG.GEO_DATA.CBU_BUILDINGS,
    });
  }
  if (!map.getLayer("cbu-buildings-fill")) {
    map.addLayer({
      id: "cbu-buildings-fill",
      type: "fill-extrusion",
      source: "cbu-buildings",
      minzoom: 15,
      paint: {
        "fill-extrusion-height": ["get", "render_height"],
        "fill-extrusion-color": "#cbd5e1",
        // Progress colouring for filling in data/building_info.json - off now
        // that every building is named and described. Swap it back in for
        // the plain color above to see what still needs data:
        //   black  = campus building with no name yet
        //   red    = named, but still missing a description
        //   yellow = name and description both done
        // "fill-extrusion-color": [
        //   "case",
        //   [
        //     "any",
        //     ["==", ["get", "name"], null],
        //     ["==", ["get", "name"], ""],
        //   ],
        //   "#111827",
        //   [
        //     "all",
        //     ["!=", ["get", "description"], null],
        //     ["!=", ["get", "description"], ""],
        //   ],
        //   "#facc15",
        //   "#ef4444",
        // ],
        "fill-extrusion-opacity": 0.85,
      },
    });
  }

  // KRAL airport boundary - always visible geofence.
  if (!map.getSource("kral-airport")) {
    map.addSource("kral-airport", {
      type: "geojson",
      data: CONFIG.GEO_DATA.KRAL_AIRPORT,
    });
  }
  if (!map.getLayer("kral-outline")) {
    map.addLayer({
      id: "kral-fill",
      type: "fill",
      source: "kral-airport",
      paint: { "fill-color": "#f87171", "fill-opacity": 0.08 },
    });
    map.addLayer({
      id: "kral-outline",
      type: "line",
      source: "kral-airport",
      paint: { "line-color": "#f87171", "line-width": 3 },
    });
  }

  // Flight trail for whichever aircraft is currently selected.
  if (!map.getSource("aircraft-track")) {
    map.addSource("aircraft-track", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
  }
  if (!map.getLayer("aircraft-track-line")) {
    map.addLayer({
      id: "aircraft-track-line",
      type: "line",
      source: "aircraft-track",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#22c55e", "line-width": 3, "line-opacity": 0.9 },
    });
  }

  // Live aircraft.
  if (!map.getSource("aircraft")) {
    map.addSource("aircraft", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
  }
  // Highlight ring under whichever aircraft is selected. Filtered to the
  // selected registration, so it renders for exactly one aircraft (or none).
  if (!map.getLayer("aircraft-highlight")) {
    map.addLayer({
      id: "aircraft-highlight",
      type: "circle",
      source: "aircraft",
      filter: ["==", ["get", "reg"], ""],
      paint: {
        "circle-radius": 18,
        "circle-color": "#22c55e",
        "circle-opacity": 0.25,
        "circle-stroke-color": "#22c55e",
        "circle-stroke-width": 2.5,
      },
    });
  }
  // Guaranteed-visible fallback marker under the icon - if the icon image
  // ever fails to load, aircraft position is still visible.
  if (!map.getLayer("aircraft-dot")) {
    map.addLayer({
      id: "aircraft-dot",
      type: "circle",
      source: "aircraft",
      paint: {
        "circle-radius": 4,
        "circle-color": "#38bdf8",
        "circle-stroke-color": "#0f172a",
        "circle-stroke-width": 1,
      },
    });
  }
  // No text-field/glyphs here on purpose - callsign is shown in the popup
  // and the Active Flights list, and a broken glyphs URL can take a whole
  // symbol layer's rendering down with it, icon included.
  if (!map.getLayer("aircraft-symbol")) {
    map.addLayer({
      id: "aircraft-symbol",
      type: "symbol",
      source: "aircraft",
      layout: {
        "icon-image": "plane-icon",
        "icon-rotate": ["get", "heading"],
        "icon-rotation-alignment": "map",
        "icon-allow-overlap": true,
        "icon-size": 0.55,
      },
    });
  }

  // Route 1's real road path(s) - hidden (filtered to nothing) until a bus
  // or stop is clicked, so all variants don't clutter the map by default.
  if (!map.getSource("route1-shapes")) {
    map.addSource("route1-shapes", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
  }
  if (!map.getLayer("route1-shapes-line")) {
    map.addLayer({
      id: "route1-shapes-line",
      type: "line",
      source: "route1-shapes",
      filter: ["==", ["get", "shape_id"], "__none__"],
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#f97316", "line-width": 4, "line-opacity": 0.75 },
    });
  }

  // Route 1 stops - near-static, fetched once (see loadRoute1Stops below).
  // A visible marker (white center, orange ring) rather than a flat dot.
  if (!map.getSource("route1-stops")) {
    map.addSource("route1-stops", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
  }
  if (!map.getLayer("route1-stops-dot")) {
    map.addLayer({
      id: "route1-stops-dot",
      type: "circle",
      source: "route1-stops",
      // Hidden by default - all 134 stops at once is clutter. selectBus()/
      // deselectBus() toggle this to "visible" only while a bus is selected.
      layout: { visibility: "none" },
      paint: {
        "circle-radius": 6,
        "circle-color": "#ffffff",
        "circle-opacity": 0.9,
        "circle-stroke-color": "#f97316",
        "circle-stroke-width": 2.5,
      },
    });
  }

  // Live Route 1 buses. Two sources for the same buses: a real-world-sized
  // rectangle (see busPolygon()) rendered as a 3D box, same technique as the
  // campus buildings - and a point, rendered as a fixed-pixel-size dot. The
  // box alone isn't reliably visible: a real bus (12m) is sub-pixel at most
  // zooms this app actually uses, the same reason buildings need minzoom 15
  // - but buses, like aircraft, need to be visible at any zoom. The dot
  // guarantees that; the box adds real 3D depth once you're zoomed in or
  // tilted, mirroring aircraft-dot/aircraft-symbol's fallback pattern.
  if (!map.getSource("route1-buses")) {
    map.addSource("route1-buses", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
  }
  if (!map.getSource("route1-bus-points")) {
    map.addSource("route1-bus-points", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
  }
  // Selection ring - not a recolored bus, same approach as aircraft-highlight:
  // a separate ring under whichever bus is selected, at any zoom (2D or 3D).
  if (!map.getLayer("route1-bus-highlight")) {
    map.addLayer({
      id: "route1-bus-highlight",
      type: "circle",
      source: "route1-bus-points",
      filter: ["==", ["get", "vehicle_id"], ""],
      paint: {
        "circle-radius": 16,
        "circle-color": "#22c55e",
        "circle-opacity": 0.25,
        "circle-stroke-color": "#22c55e",
        "circle-stroke-width": 2.5,
      },
    });
  }
  // Fallback dot in case the bus icon image ever fails to load, same guard
  // as aircraft-dot for the plane icon.
  if (!map.getLayer("route1-bus-dot")) {
    map.addLayer({
      id: "route1-bus-dot",
      type: "circle",
      source: "route1-bus-points",
      // 2D below the 3D box's threshold - above it, the real-scale box is
      // finally big enough on screen to read clearly, so these step aside
      // instead of doubling up on top of it.
      maxzoom: BUS_3D_MINZOOM,
      paint: {
        "circle-radius": 4,
        "circle-color": "#f97316",
        "circle-stroke-color": "#7c2d12",
        "circle-stroke-width": 1,
      },
    });
  }
  if (!map.getLayer("route1-bus-symbol")) {
    map.addLayer({
      id: "route1-bus-symbol",
      type: "symbol",
      source: "route1-bus-points",
      maxzoom: BUS_3D_MINZOOM,
      layout: {
        "icon-image": "bus-icon",
        // A null bearing renders nose-up instead of erroring, rather than
        // silently coalescing to a misleading heading.
        "icon-rotate": ["coalesce", ["get", "bearing"], 0],
        "icon-rotation-alignment": "map",
        "icon-allow-overlap": true,
        "icon-size": 0.5,
      },
    });
  }

  // Four height-banded fill-extrusion layers (dark chassis, orange body,
  // glassy windows, light roof) - see BUS_BAND_LAYERS near the top of this
  // file for why this beats one solid-color slab.
  BUS_BAND_LAYERS.forEach(({ id, base, height, color }) => {
    if (map.getLayer(id)) return;
    map.addLayer({
      id,
      type: "fill-extrusion",
      source: "route1-buses",
      minzoom: BUS_3D_MINZOOM,
      paint: {
        "fill-extrusion-base": base,
        "fill-extrusion-height": height,
        "fill-extrusion-color": color,
        "fill-extrusion-opacity": 0.95,
      },
    });
  });

}

map.on("load", addCustomLayers);

// --- Middle-mouse drag to tilt (pitch) the camera, for the 3D buildings ---
// Hold the middle mouse button and drag up/down. Drag left/right at the same
// time to rotate. MapLibre only binds right-click/ctrl+drag for this natively.
let pitchDragging = false;
let pitchLastY = 0;
let pitchLastX = 0;

const mapCanvas = map.getCanvasContainer();

mapCanvas.addEventListener("mousedown", (e) => {
  if (e.button !== 1) return;
  e.preventDefault(); // stop the browser's middle-click autoscroll
  pitchDragging = true;
  pitchLastY = e.clientY;
  pitchLastX = e.clientX;
  mapCanvas.style.cursor = "ns-resize";
});

window.addEventListener("mousemove", (e) => {
  if (!pitchDragging) return;
  const dy = e.clientY - pitchLastY;
  const dx = e.clientX - pitchLastX;
  pitchLastY = e.clientY;
  pitchLastX = e.clientX;
  // Drag up => tilt toward the horizon, drag down => back to top-down.
  map.setPitch(Math.min(85, Math.max(0, map.getPitch() - dy * 0.4)));
  if (Math.abs(dx) > 0) map.setBearing(map.getBearing() + dx * 0.3);
});

window.addEventListener("mouseup", (e) => {
  if (e.button !== 1 || !pitchDragging) return;
  pitchDragging = false;
  mapCanvas.style.cursor = "";
});

// Chrome shows the autoscroll widget on middle-click unless this is killed.
mapCanvas.addEventListener("auxclick", (e) => {
  if (e.button === 1) e.preventDefault();
});

// --- Focal point buttons ---
document.querySelectorAll("#focal-buttons button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document
      .querySelectorAll("#focal-buttons button")
      .forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const focal = CONFIG.FOCAL_POINTS[btn.dataset.focal];
    map.easeTo({
      center: focal.center,
      zoom: focal.zoom,
      pitch: focal.pitch,
      bearing: focal.bearing,
      duration: 1500,
    });
  });
});

// --- Tracker tabs: show one quick-view list at a time, expanding the panel
// as needed instead of scrolling within a fixed-height list. ---
document.querySelectorAll("#tracker-tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#tracker-tabs button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tracker = btn.dataset.tracker;
    document.getElementById("aircraft-list-wrap").classList.toggle("hidden", tracker !== "planes");
    document.getElementById("bus-list-wrap").classList.toggle("hidden", tracker !== "buses");
  });
});

// --- Click popups ---
map.on("click", "cbu-buildings-fill", (e) => {
  const p = e.features[0].properties;
  const label = p.name || "Unnamed building";
  // p.type comes from data/building_info.json when set; otherwise fall back
  // to whatever OpenStreetMap's tags imply.
  const type =
    p.type ||
    (p.building && p.building !== "yes" ? p.building : p.amenity) ||
    "unknown";

  let html = `<strong>${label}</strong><br>Type: ${type}`;
  if (p.description) html += `<br>${p.description}`;
  // osm_id is shown so it can be pasted into data/building_info.json to
  // give this building a name/description.
  html +=
    `<br><span class="osm-id">osm_id: <code>${p.osm_id}</code></span>`;

  new maplibregl.Popup()
    .setLngLat(e.lngLat)
    .setHTML(html)
    .addTo(map);
});

// Shows one bus's own shape_id, or (with no argument) every Route 1
// variant at once - a stop doesn't belong to a single direction, so
// clicking one shows the whole route instead of guessing.
function showRouteShapes(shapeId) {
  if (!map.getLayer("route1-shapes-line")) return;
  map.setFilter(
    "route1-shapes-line",
    shapeId ? ["==", ["get", "shape_id"], shapeId] : null
  );
}

function hideRouteShapes() {
  if (!map.getLayer("route1-shapes-line")) return;
  map.setFilter("route1-shapes-line", ["==", ["get", "shape_id"], "__none__"]);
}

// A bus has few enough facts (headsign, speed, id, time) that a transient
// Popup is enough - unlike aircraft, which get the persistent details panel.
function showBusPopup(e) {
  const p = e.features[0].properties;
  const speed = p.speed_mph != null && p.speed_mph !== "" ? `${p.speed_mph} mph` : "unknown";
  const observed = p.observed_at
    ? new Date(p.observed_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "unknown";
  const html =
    `<strong>${p.headsign || "Route 1"}</strong><br>` +
    `Speed: ${speed}<br>` +
    `Vehicle: ${p.vehicle_id || "unknown"}<br>` +
    `As of ${observed}`;
  new maplibregl.Popup().setLngLat(e.lngLat).setHTML(html).addTo(map);
  selectBus(p.vehicle_id);
}

BUS_CLICKABLE_LAYERS.forEach((layer) => {
  map.on("click", layer, showBusPopup);
  map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
});

map.on("click", "route1-stops-dot", (e) => {
  const p = e.features[0].properties;
  new maplibregl.Popup()
    .setLngLat(e.lngLat)
    .setHTML(`<strong>${p.name || "Route 1 stop"}</strong>`)
    .addTo(map);
  showRouteShapes(null); // a stop serves both directions - show the full route
});
map.on("mouseenter", "route1-stops-dot", () => (map.getCanvas().style.cursor = "pointer"));
map.on("mouseleave", "route1-stops-dot", () => (map.getCanvas().style.cursor = ""));

// Aircraft details render into the bottom-left panel rather than a map
// popup, which used to sit on top of the aircraft it described.
function aircraftDetailsHTML(p) {
  const heading = p.heading != null && p.heading !== "" ? `${Math.round(p.heading)}°` : "unknown";
  const row = (label, value) =>
    `<div class="detail-row"><span>${label}</span><span>${value}</span></div>`;
  return (
    `<div class="detail-title">${p.callsign || p.reg}</div>` +
    row("Registration", p.reg) +
    row("Type", p.type || "unknown") +
    row("Owner", p.owner || "unknown") +
    row("Altitude", `${p.alt_ft} ft`) +
    row("Speed", `${p.speed_kt} kt`) +
    row("Heading", heading) +
    flightRows(p.flight) +
    row("Pilot", '<em>no public source</em>')
  );
}

// Departure/arrival are derived from observed ground<->airborne transitions
// matched to the nearest aerodrome - not a filed flight plan, so they're
// labelled as observed and left blank rather than guessed when unknown.
function flightRows(flight) {
  const row = (label, value) =>
    `<div class="detail-row"><span>${label}</span><span>${value}</span></div>`;
  const place = (ap) => (ap ? ap.code || ap.name || "unknown" : null);
  const time = (t) =>
    t ? new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";

  if (!flight) return row("Departed", "<em>not seen departing</em>");

  const dep = place(flight.departure);
  const arr = place(flight.arrival);
  let html = dep
    ? row("Departed", `${dep} <span class="muted">${time(flight.departure_time)}</span>`)
    : row("Departed", "<em>not seen departing</em>");

  if (arr) {
    html += row("Arrived", `${arr} <span class="muted">${time(flight.arrival_time)}</span>`);
  } else if (dep) {
    html += row("Arrived", "<em>in flight</em>");
  }
  if (flight.touch_and_go > 0) {
    html += row("Touch &amp; go", flight.touch_and_go);
  }
  return html;
}

// Fetches and renders the selected aircraft's flight trail.
async function showTrack(reg) {
  try {
    const res = await fetch(`/api/aircraft/${encodeURIComponent(reg)}/track`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const points = await res.json();
    const src = map.getSource("aircraft-track");
    if (!src) return;
    if (points.length < 2) {
      src.setData({ type: "FeatureCollection", features: [] });
      return;
    }
    src.setData({
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: { reg },
          geometry: {
            type: "LineString",
            coordinates: points.map((p) => [p.lon, p.lat]),
          },
        },
      ],
    });
  } catch (err) {
    console.error("Failed to load flight track:", err);
  }
}

function clearTrack() {
  const src = map.getSource("aircraft-track");
  if (src) src.setData({ type: "FeatureCollection", features: [] });
}

// --- Selection state: only one aircraft's details/trail shown at a time.
// Icons themselves are always visible regardless of selection. ---
let selectedReg = null;

const detailsPanel = document.getElementById("aircraft-details");
const detailsBody = document.getElementById("details-body");

// Drives the highlight ring. Filtering to "" matches nothing, which is how
// the ring is hidden when there's no selection.
function setHighlight(reg) {
  if (!map.getLayer("aircraft-highlight")) return;
  map.setFilter("aircraft-highlight", ["==", ["get", "reg"], reg || ""]);
}

function deselectAircraft() {
  selectedReg = null;
  setHighlight(null);
  clearTrack();
  detailsPanel.classList.add("hidden");
  detailsBody.innerHTML = "";
  document
    .querySelectorAll(".aircraft-row.selected")
    .forEach((el) => el.classList.remove("selected"));
}

function selectAircraft(reg) {
  if (selectedReg === reg) {
    deselectAircraft();
    return;
  }
  const ac = latestAircraft.find((a) => a.reg === reg);
  if (!ac) return;

  selectedReg = reg;
  setHighlight(reg);
  map.easeTo({ center: [ac.lon, ac.lat], zoom: 13, duration: 1200 });
  detailsBody.innerHTML = aircraftDetailsHTML(ac);
  detailsPanel.classList.remove("hidden");
  showTrack(reg);

  document
    .querySelectorAll(".aircraft-row")
    .forEach((el) => el.classList.toggle("selected", el.dataset.reg === reg));
}

document.getElementById("details-close").addEventListener("click", deselectAircraft);

["aircraft-symbol", "aircraft-dot", "aircraft-highlight"].forEach((layer) => {
  map.on("click", layer, (e) => selectAircraft(e.features[0].properties.reg));
});

// Clicking empty map (not on a plane, building, bus, or stop) deselects.
map.on("click", (e) => {
  const hits = map.queryRenderedFeatures(e.point, {
    layers: [
      "aircraft-symbol",
      "aircraft-dot",
      "aircraft-highlight",
      "cbu-buildings-fill",
      ...BUS_CLICKABLE_LAYERS,
      "route1-stops-dot",
    ],
  });
  if (hits.length === 0) {
    if (selectedReg) deselectAircraft();
    if (selectedBusId) deselectBus();
  }
});

["cbu-buildings-fill", "aircraft-symbol", "aircraft-dot"].forEach((layer) => {
  map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
});

// --- Status line: combines aircraft-poll status and bus-feed status, since
// both share the single #status element. Each side tracks its own text/warn
// state and calls renderStatus() rather than writing statusEl directly, so
// neither poller (1s aircraft, 15s transit) clobbers the other's half. ---
const statusEl = document.getElementById("status");
let aircraftStatusText = "Loading...";
let aircraftWarn = false;
let busStatusText = "";
let busWarn = false;

function renderStatus() {
  statusEl.textContent = busStatusText
    ? `${aircraftStatusText} | ${busStatusText}`
    : aircraftStatusText;
  statusEl.classList.toggle("warn", aircraftWarn || busWarn);
}

// --- Aircraft polling ---
const aircraftListEl = document.getElementById("aircraft-list");
let latestAircraft = [];

function renderAircraftList(data) {
  if (data.length === 0) {
    aircraftListEl.textContent = "None currently tracked";
    return;
  }
  aircraftListEl.innerHTML = "";
  data.forEach((a) => {
    const row = document.createElement("button");
    row.className = "aircraft-row" + (a.reg === selectedReg ? " selected" : "");
    row.dataset.reg = a.reg;
    row.textContent = `${a.callsign || a.reg} (${a.type || "?"})`;
    row.addEventListener("click", () => selectAircraft(a.reg));
    aircraftListEl.appendChild(row);
  });
}

async function pollAircraft() {
  try {
    const res = await fetch("/api/aircraft");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    latestAircraft = data;
    const fc = {
      type: "FeatureCollection",
      features: data.map((a) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [a.lon, a.lat] },
        properties: a,
      })),
    };
    const src = map.getSource("aircraft");
    if (src) src.setData(fc);
    renderAircraftList(data);
    if (selectedReg) {
      // Keep the trail growing and the details live as the aircraft moves.
      showTrack(selectedReg);
      const ac = data.find((a) => a.reg === selectedReg);
      if (ac) detailsBody.innerHTML = aircraftDetailsHTML(ac);
      else deselectAircraft(); // went stale / landed out of coverage
    }
    aircraftStatusText = `${data.length} aircraft via TAK - updated ${new Date().toLocaleTimeString()}`;
    aircraftWarn = false;
    renderStatus();
    checkTakLink();
  } catch (err) {
    aircraftStatusText = `Connection issue: ${err.message}`;
    aircraftWarn = true;
    renderStatus();
  }
}

// All aircraft data arrives as CoT from FreeTAKServer, so an empty map can
// mean "no flights" or "the TAK feed is down" - say which.
async function checkTakLink() {
  try {
    const res = await fetch("/api/health");
    if (!res.ok) return;
    const h = await res.json();
    const age = h.seconds_since_last_cot;
    const stale = age === null || age > 60;
    if (!h.tak_connected) {
      aircraftStatusText = "TAK link down - no CoT source";
      aircraftWarn = true;
    } else if (stale && h.tracked_aircraft === 0) {
      aircraftStatusText = "TAK connected - no CoT received (is adsb_tak.py running?)";
      aircraftWarn = true;
    } else {
      aircraftWarn = false;
    }
    renderStatus();
  } catch (err) {
    /* status already reflects the fetch failure */
  }
}

setInterval(pollAircraft, CONFIG.POLL_MS);
pollAircraft();

// --- Weather widget ---
const FLIGHT_CATEGORY_COLORS = {
  VFR: "#22c55e",
  MVFR: "#2563eb",
  IFR: "#ef4444",
  LIFR: "#c026d3",
};

const weatherEl = document.getElementById("weather");
const weatherCategoryEl = document.getElementById("weather-category");
const weatherTempEl = document.getElementById("weather-temp");
const weatherWindEl = document.getElementById("weather-wind");
const weatherVisEl = document.getElementById("weather-vis");
const weatherUpdatedEl = document.getElementById("weather-updated");

function formatWind(wind) {
  if (!wind || (wind.speed_kt == null && !wind.variable)) return "Calm";
  if (wind.speed_kt === 0) return "Calm";
  if (wind.variable) return `Variable ${wind.speed_kt}kt`;
  if (wind.dir_deg == null) return `${wind.speed_kt}kt`;
  return `${wind.dir_deg}° ${wind.speed_kt}kt`;
}

function renderWeather(w) {
  if (!w.available) {
    weatherCategoryEl.textContent = "N/A";
    weatherCategoryEl.style.background = "#6b7280";
    weatherTempEl.textContent = "Weather unavailable";
    weatherWindEl.textContent = "";
    weatherVisEl.textContent = "";
    weatherUpdatedEl.textContent = w.error || "";
    weatherEl.classList.add("stale");
    weatherEl.title = "";
    return;
  }

  const cat = w.flight_category || "UNK";
  weatherCategoryEl.textContent = cat;
  weatherCategoryEl.style.background = FLIGHT_CATEGORY_COLORS[cat] || "#6b7280";
  weatherTempEl.textContent = w.temp_c != null ? `${Math.round(w.temp_c)}°C` : "--";
  weatherWindEl.textContent = formatWind(w.wind);
  weatherVisEl.textContent = w.visibility_sm != null ? `${w.visibility_sm}sm vis` : "";

  const asOf = w.observed_at
    ? new Date(w.observed_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "unknown";
  weatherUpdatedEl.textContent = w.stale ? `As of ${asOf} (stale)` : `As of ${asOf}`;
  weatherEl.classList.toggle("stale", w.stale);

  const dewpoint = w.dewpoint_c != null ? `${Math.round(w.dewpoint_c)}°C` : "unknown";
  const altimeter = w.altimeter_inhg != null ? `${w.altimeter_inhg} inHg` : "unknown";
  weatherEl.title =
    `Dewpoint: ${dewpoint}\nAltimeter: ${altimeter}\n${w.raw_metar || ""}`;
}

async function pollWeather() {
  try {
    const res = await fetch("/api/weather");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    renderWeather(await res.json());
  } catch (err) {
    console.error("Failed to poll weather:", err);
  }
}

setInterval(pollWeather, CONFIG.WEATHER_POLL_MS);
pollWeather();

// --- Route 1 bus polling ---
async function loadRoute1Stops() {
  try {
    const res = await fetch(CONFIG.GEO_DATA.ROUTE1_STOPS);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const stops = await res.json();
    const src = map.getSource("route1-stops");
    if (!src) return;
    src.setData({
      type: "FeatureCollection",
      features: stops.map((s) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [s.lon, s.lat] },
        properties: s,
      })),
    });
  } catch (err) {
    console.error("Failed to load Route 1 stops:", err);
  }
}

// Fetched once - RTA's own feed only publishes a few shape_ids per route,
// so this is small and near-static (route geometry doesn't change live).
async function loadRoute1Shapes() {
  try {
    const res = await fetch(CONFIG.GEO_DATA.ROUTE1_SHAPES);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const src = map.getSource("route1-shapes");
    if (src) src.setData(await res.json());
  } catch (err) {
    console.error("Failed to load Route 1 shapes:", err);
  }
}

// RTA's own feed only actually moves a vehicle every ~20-25s (confirmed by
// sampling it), far slower than aircraft CoT (~1s) - polling faster than
// that wouldn't get fresher data, it would just re-fetch the same fix. So
// instead each real fix is animated: busAnim holds a from/to pair per
// vehicle, and animateBuses() interpolates between them every frame so
// buses glide continuously between polls instead of jumping in place.
const busAnim = {}; // vehicle_id -> {from:{lat,lon}, to:{lat,lon}, start, duration, props}

function _lerpBusPos(anim, now) {
  const t = Math.min(1, (now - anim.start) / anim.duration);
  return {
    lat: anim.from.lat + (anim.to.lat - anim.from.lat) * t,
    lon: anim.from.lon + (anim.to.lon - anim.from.lon) * t,
  };
}

let _lastBusRender = 0;
function animateBuses(now) {
  requestAnimationFrame(animateBuses);
  // Throttled to ~10fps - plenty smooth for a route with a dozen-odd buses,
  // far cheaper than rebuilding the GeoJSON source every animation frame.
  if (now - _lastBusRender < 100) return;
  _lastBusRender = now;

  const polySrc = map.getSource("route1-buses");
  const pointSrc = map.getSource("route1-bus-points");
  if (!polySrc || !pointSrc) return;

  const positions = Object.values(busAnim).map((anim) => ({
    pos: _lerpBusPos(anim, now),
    props: anim.props,
  }));

  polySrc.setData({
    type: "FeatureCollection",
    features: positions.map(({ pos, props }) => ({
      type: "Feature",
      // A null bearing draws the box facing north instead of erroring,
      // rather than silently coalescing to a misleading heading.
      geometry: { type: "Polygon", coordinates: [busPolygon(pos.lat, pos.lon, props.bearing ?? 0)] },
      properties: props,
    })),
  });
  pointSrc.setData({
    type: "FeatureCollection",
    features: positions.map(({ pos, props }) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [pos.lon, pos.lat] },
      properties: props,
    })),
  });
}
requestAnimationFrame(animateBuses);

// --- Bus quick-view list + selection (mirrors the aircraft list/selection:
// one selected at a time, list rows and map both drive the same state). ---
const busListEl = document.getElementById("bus-list");
let latestBuses = [];
let selectedBusId = null;

const BUS_PAGE_SIZE = 5;
let busPage = 0;
const busPagerEl = document.getElementById("bus-pager");
const busPageEl = document.getElementById("bus-page");
const busPrevEl = document.getElementById("bus-prev");
const busNextEl = document.getElementById("bus-next");

// Stable order, so a bus doesn't hop between pages on every 15s refresh.
function sortedBuses(vehicles) {
  return [...vehicles].sort(
    (a, b) =>
      (a.headsign || "").localeCompare(b.headsign || "") ||
      String(a.vehicle_id).localeCompare(String(b.vehicle_id))
  );
}

function renderBusList(vehicles) {
  const pages = Math.max(1, Math.ceil(vehicles.length / BUS_PAGE_SIZE));
  busPage = Math.min(Math.max(busPage, 0), pages - 1); // fleet can shrink between polls
  busPagerEl.classList.toggle("hidden", pages <= 1);
  busPageEl.textContent = `${busPage + 1} / ${pages}`;
  busPrevEl.disabled = busPage === 0;
  busNextEl.disabled = busPage >= pages - 1;

  if (vehicles.length === 0) {
    busListEl.textContent = "None currently tracked";
    return;
  }
  busListEl.innerHTML = "";
  sortedBuses(vehicles)
    .slice(busPage * BUS_PAGE_SIZE, (busPage + 1) * BUS_PAGE_SIZE)
    .forEach((b) => {
      const row = document.createElement("button");
      row.className = "bus-row" + (b.vehicle_id === selectedBusId ? " selected" : "");
      row.dataset.vehicleId = b.vehicle_id;
      row.textContent = b.headsign || `Route 1 #${b.vehicle_id}`;
      row.addEventListener("click", () => selectBus(b.vehicle_id));
      busListEl.appendChild(row);
    });
}

busPrevEl.addEventListener("click", () => {
  busPage -= 1;
  renderBusList(latestBuses);
});
busNextEl.addEventListener("click", () => {
  busPage += 1;
  renderBusList(latestBuses);
});

// Drives the highlight ring, same approach as aircraft's setHighlight() -
// a separate ring under the bus rather than recoloring the bus itself,
// which also means it works identically in both 2D and 3D mode.
function setBusHighlight(vehicleId) {
  if (!map.getLayer("route1-bus-highlight")) return;
  map.setFilter("route1-bus-highlight", ["==", ["get", "vehicle_id"], vehicleId || ""]);
}

function setStopsVisible(visible) {
  if (!map.getLayer("route1-stops-dot")) return;
  map.setLayoutProperty("route1-stops-dot", "visibility", visible ? "visible" : "none");
}

function selectBus(vehicleId) {
  if (selectedBusId === vehicleId) {
    deselectBus();
    return;
  }
  const bus = latestBuses.find((b) => b.vehicle_id === vehicleId);
  if (!bus) return;

  selectedBusId = vehicleId;
  setBusHighlight(vehicleId);
  setStopsVisible(true);
  showRouteShapes(bus.shape_id || null);
  // Past BUS_3D_MINZOOM, so selecting a bus actually shows its 3D box.
  map.easeTo({ center: [bus.lon, bus.lat], zoom: 17, duration: 1200 });

  // Clicked on the map, the bus may be on another page of the list - flip to it.
  const idx = sortedBuses(latestBuses).findIndex((b) => b.vehicle_id === vehicleId);
  busPage = Math.floor(idx / BUS_PAGE_SIZE);
  renderBusList(latestBuses);
}

function deselectBus() {
  selectedBusId = null;
  setBusHighlight(null);
  setStopsVisible(false);
  hideRouteShapes();
  document.querySelectorAll(".bus-row.selected").forEach((el) => el.classList.remove("selected"));
}

async function pollTransit() {
  try {
    const res = await fetch("/api/transit/route1");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const now = performance.now();
    const seen = new Set();

    data.vehicles.forEach((v) => {
      const id = v.vehicle_id || v.trip_id;
      if (!id) return;
      seen.add(id);
      const existing = busAnim[id];
      // A newly-seen bus starts at its real position, not animated in from
      // nowhere; an existing one continues from wherever it currently is
      // (not its old target), so a fix that arrives early never causes a
      // backwards jump.
      const from = existing ? _lerpBusPos(existing, now) : { lat: v.lat, lon: v.lon };
      busAnim[id] = {
        from,
        to: { lat: v.lat, lon: v.lon },
        start: now,
        duration: CONFIG.TRANSIT_POLL_MS,
        props: v,
      };
    });
    // Drop buses no longer in the feed (off-shift, out of range, etc).
    Object.keys(busAnim).forEach((id) => {
      if (!seen.has(id)) delete busAnim[id];
    });

    latestBuses = data.vehicles;
    renderBusList(data.vehicles);
    if (selectedBusId && !data.vehicles.some((b) => b.vehicle_id === selectedBusId)) {
      deselectBus(); // went stale / dropped out of the feed
    }

    if (!data.available) {
      busStatusText = "bus feed unavailable";
      busWarn = true;
    } else if (data.stale) {
      busStatusText = "bus feed stale";
      busWarn = true;
    } else if (data.vehicle_count === 0) {
      busStatusText = "Route 1 not currently running";
      busWarn = false;
    } else {
      busStatusText = `${data.vehicle_count} buses on Route 1`;
      busWarn = false;
    }
    renderStatus();
  } catch (err) {
    busStatusText = "bus feed unavailable";
    busWarn = true;
    renderStatus();
  }
}

map.on("load", loadRoute1Stops);
map.on("load", loadRoute1Shapes);
setInterval(pollTransit, CONFIG.TRANSIT_POLL_MS);
pollTransit();
