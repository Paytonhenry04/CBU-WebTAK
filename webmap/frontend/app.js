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
        "fill-extrusion-color": [
          "case",
          ["!=", ["get", "name"], null],
          "#facc15",
          "#64748b",
        ],
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
    row("Departure/Arrival", '<em>no public source</em>') +
    row("Pilot", '<em>no public source</em>')
  );
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

// Clicking empty map (not on a plane or building) deselects.
map.on("click", (e) => {
  const hits = map.queryRenderedFeatures(e.point, {
    layers: [
      "aircraft-symbol",
      "aircraft-dot",
      "aircraft-highlight",
      "cbu-buildings-fill",
    ],
  });
  if (hits.length === 0 && selectedReg) deselectAircraft();
});

["cbu-buildings-fill", "aircraft-symbol", "aircraft-dot"].forEach((layer) => {
  map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
});

// --- Aircraft polling ---
const statusEl = document.getElementById("status");
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
    statusEl.textContent = `${data.length} aircraft tracked - updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    statusEl.textContent = `Connection issue: ${err.message}`;
  }
}

setInterval(pollAircraft, CONFIG.POLL_MS);
pollAircraft();
