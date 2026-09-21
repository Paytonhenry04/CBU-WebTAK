// CBU/KRAL live webmap - main app logic

function circlePolygon(centerLonLat, radiusMeters, points = 64) {
  const [lon, lat] = centerLonLat;
  const earthRadius = 6371000;
  const coords = [];
  for (let i = 0; i <= points; i++) {
    const angle = (i / points) * 2 * Math.PI;
    const dx = radiusMeters * Math.cos(angle);
    const dy = radiusMeters * Math.sin(angle);
    const dLat = (dy / earthRadius) * (180 / Math.PI);
    const dLon =
      (dx / (earthRadius * Math.cos((lat * Math.PI) / 180))) * (180 / Math.PI);
    coords.push([lon + dLon, lat + dLat]);
  }
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: { name: "CBU Campus geofence" },
        geometry: { type: "Polygon", coordinates: [coords] },
      },
    ],
  };
}

// Loads the plane icon via a plain DOM Image element instead of MapLibre's
// own loadImage() pipeline (which has tripped up twice now on this
// project - wrong callback signature, then an unconfirmed resolve shape).
// new Image() + onload + addImage() is the simplest, most broadly-supported
// path and doesn't depend on MapLibre's internal request/decode handling.
function loadPlaneIcon() {
  return new Promise((resolve) => {
    if (map.hasImage("plane-icon")) {
      resolve();
      return;
    }
    const svg =
      '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">' +
      '<polygon points="24,4 40,42 24,32 8,42" fill="#38bdf8" stroke="#0f172a" stroke-width="2"/>' +
      "</svg>";
    const img = new Image();
    img.onload = () => {
      if (!map.hasImage("plane-icon")) {
        map.addImage("plane-icon", img);
      }
      resolve();
    };
    img.onerror = (err) => {
      console.error("Failed to load plane icon:", err);
      resolve();
    };
    img.src = "data:image/svg+xml;base64," + btoa(svg);
  });
}

function satelliteStyle() {
  return {
    version: 8,
    glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
    sources: {
      esri: {
        type: "raster",
        tiles: [CONFIG.TILES.SATELLITE_TILE_URL],
        tileSize: 256,
        attribution: CONFIG.TILES.SATELLITE_ATTRIBUTION,
      },
    },
    layers: [{ id: "esri-satellite", type: "raster", source: "esri" }],
  };
}

let usingSatellite = false;

const map = new maplibregl.Map({
  container: "map",
  style: CONFIG.TILES.STREET_STYLE_URL,
  center: CONFIG.FOCAL_POINTS.overview.center,
  zoom: CONFIG.FOCAL_POINTS.overview.zoom,
  pitch: CONFIG.FOCAL_POINTS.overview.pitch,
  bearing: CONFIG.FOCAL_POINTS.overview.bearing,
  attributionControl: true,
});

map.addControl(new maplibregl.NavigationControl(), "top-right");

async function addCustomLayers() {
  await loadPlaneIcon();

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
        "fill-extrusion-height": ["coalesce", ["get", "render_height"], 8],
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

  // CBU geofence circle - always visible, marks the campus as a focal point.
  if (!map.getSource("cbu-geofence")) {
    map.addSource("cbu-geofence", {
      type: "geojson",
      data: circlePolygon(
        CONFIG.CBU_GEOFENCE_CENTER,
        CONFIG.CBU_GEOFENCE_RADIUS_M
      ),
    });
  }
  if (!map.getLayer("cbu-geofence-line")) {
    map.addLayer({
      id: "cbu-geofence-line",
      type: "line",
      source: "cbu-geofence",
      paint: { "line-color": "#facc15", "line-width": 3 },
    });
    map.addLayer({
      id: "cbu-geofence-fill",
      type: "fill",
      source: "cbu-geofence",
      paint: { "fill-color": "#facc15", "fill-opacity": 0.06 },
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
      id: "kral-outline",
      type: "line",
      source: "kral-airport",
      paint: { "line-color": "#f87171", "line-width": 3 },
    });
    map.addLayer({
      id: "kral-fill",
      type: "fill",
      source: "kral-airport",
      paint: { "fill-color": "#f87171", "fill-opacity": 0.06 },
    });
  }

  // Flight trail for whichever aircraft is currently selected (since
  // takeoff, per adsb_poller.py's ground/airborne transition tracking).
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
      paint: { "line-color": "#22c55e", "line-width": 2.5, "line-opacity": 0.9 },
    });
  }

  // Live aircraft.
  if (!map.getSource("aircraft")) {
    map.addSource("aircraft", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
  }
  // Guaranteed-visible fallback marker, always drawn under the plane icon -
  // if the custom icon image ever fails to load, position is still visible.
  if (!map.getLayer("aircraft-dot")) {
    map.addLayer({
      id: "aircraft-dot",
      type: "circle",
      source: "aircraft",
      paint: {
        "circle-radius": 6,
        "circle-color": "#38bdf8",
        "circle-stroke-color": "#0f172a",
        "circle-stroke-width": 1.5,
      },
    });
  }
  if (!map.getLayer("aircraft-symbol")) {
    map.addLayer({
      id: "aircraft-symbol",
      type: "symbol",
      source: "aircraft",
      layout: {
        "icon-image": "plane-icon",
        "icon-rotate": ["coalesce", ["get", "heading"], 0],
        "icon-rotation-alignment": "map",
        "icon-allow-overlap": true,
        "icon-size": 0.8,
        "text-field": ["get", "callsign"],
        "text-offset": [0, 1.4],
        "text-size": 11,
        "text-allow-overlap": true,
      },
      paint: {
        "text-color": "#ffffff",
        "text-halo-color": "#000000",
        "text-halo-width": 1.2,
      },
    });
  }
}

map.on("load", addCustomLayers);

// --- Basemap toggle ---
document.getElementById("toggle-basemap").addEventListener("click", () => {
  usingSatellite = !usingSatellite;
  document.getElementById("toggle-basemap").textContent = usingSatellite
    ? "Switch to Street Map"
    : "Switch to Satellite";
  map.once("style.load", addCustomLayers);
  map.setStyle(usingSatellite ? satelliteStyle() : CONFIG.TILES.STREET_STYLE_URL);
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
  const type = p.building && p.building !== "yes" ? p.building : p.amenity || "unknown";
  new maplibregl.Popup()
    .setLngLat(e.lngLat)
    .setHTML(`<strong>${label}</strong><br>Type: ${type}<br><em>Source: OpenStreetMap</em>`)
    .addTo(map);
});

function aircraftPopupHTML(p) {
  const heading = p.heading != null && p.heading !== "" ? `${Math.round(p.heading)}°` : "unknown";
  return (
    `<strong>${p.callsign || p.reg}</strong><br>` +
    `Registration: ${p.reg}<br>` +
    `Type: ${p.type || "unknown"}<br>` +
    `Owner: ${p.owner || "unknown"}<br>` +
    `Altitude: ${p.alt_ft} ft<br>` +
    `Speed: ${p.speed_kt} kt<br>` +
    `Heading: ${heading}<br>` +
    `Departure/Arrival: not available (no public source)<br>` +
    `Pilot: not available (no public source)`
  );
}

// Fetches and renders the selected aircraft's flight trail since takeoff.
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
let selectedPopup = null;

function deselectAircraft() {
  selectedReg = null;
  if (selectedPopup) {
    // Null it before remove() - remove() fires "close", which is wired
    // to this same function, so this avoids re-entrant double-removal.
    const popup = selectedPopup;
    selectedPopup = null;
    popup.off("close", deselectAircraft);
    popup.remove();
  }
  clearTrack();
  document
    .querySelectorAll(".aircraft-row.selected")
    .forEach((el) => el.classList.remove("selected"));
}

function selectAircraft(reg, lngLat) {
  if (selectedReg === reg) {
    deselectAircraft();
    return;
  }
  const ac = latestAircraft.find((a) => a.reg === reg);
  if (!ac) return;

  if (selectedPopup) selectedPopup.remove();
  selectedReg = reg;

  const at = lngLat || [ac.lon, ac.lat];
  map.easeTo({ center: [ac.lon, ac.lat], zoom: 13, duration: 1200 });
  selectedPopup = new maplibregl.Popup({ closeOnClick: false })
    .setLngLat(at)
    .setHTML(aircraftPopupHTML(ac))
    .addTo(map);
  selectedPopup.on("close", deselectAircraft);
  showTrack(reg);

  document
    .querySelectorAll(".aircraft-row")
    .forEach((el) => el.classList.toggle("selected", el.dataset.reg === reg));
}

map.on("click", "aircraft-symbol", (e) => {
  selectAircraft(e.features[0].properties.reg, e.lngLat);
});
map.on("click", "aircraft-dot", (e) => {
  selectAircraft(e.features[0].properties.reg, e.lngLat);
});

// Clicking empty map (not on a plane or building) deselects.
map.on("click", (e) => {
  const hits = map.queryRenderedFeatures(e.point, {
    layers: ["aircraft-symbol", "aircraft-dot", "cbu-buildings-fill"],
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
    statusEl.textContent = `${data.length} aircraft tracked - updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    statusEl.textContent = `Connection issue: ${err.message}`;
  }
}

setInterval(pollAircraft, CONFIG.POLL_MS);
pollAircraft();
