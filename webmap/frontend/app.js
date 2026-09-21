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

function makePlaneIconData(size = 40, color = "#38bdf8") {
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.translate(size / 2, size / 2);
  ctx.fillStyle = color;
  ctx.strokeStyle = "#0f172a";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(0, -size * 0.42);
  ctx.lineTo(size * 0.30, size * 0.36);
  ctx.lineTo(0, size * 0.18);
  ctx.lineTo(-size * 0.30, size * 0.36);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  return ctx.getImageData(0, 0, size, size);
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

function addCustomLayers() {
  if (!map.getImage("plane-icon")) {
    map.addImage("plane-icon", makePlaneIconData(), { pixelRatio: 2 });
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

  // Live aircraft.
  if (!map.getSource("aircraft")) {
    map.addSource("aircraft", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
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

map.on("click", "aircraft-symbol", (e) => {
  const p = e.features[0].properties;
  const heading = p.heading != null && p.heading !== "" ? `${Math.round(p.heading)}°` : "unknown";
  new maplibregl.Popup()
    .setLngLat(e.lngLat)
    .setHTML(
      `<strong>${p.callsign || p.reg}</strong><br>` +
        `Registration: ${p.reg}<br>` +
        `Type: ${p.type || "unknown"}<br>` +
        `Owner: ${p.owner || "unknown"}<br>` +
        `Altitude: ${p.alt_ft} ft<br>` +
        `Speed: ${p.speed_kt} kt<br>` +
        `Heading: ${heading}<br>` +
        `Departure/Arrival: not available (no public source)<br>` +
        `Pilot: not available (no public source)`
    )
    .addTo(map);
});

["cbu-buildings-fill", "aircraft-symbol"].forEach((layer) => {
  map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
});

// --- Aircraft polling ---
const statusEl = document.getElementById("status");

async function pollAircraft() {
  try {
    const res = await fetch("/api/aircraft");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
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
    statusEl.textContent = `${data.length} aircraft tracked - updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    statusEl.textContent = `Connection issue: ${err.message}`;
  }
}

setInterval(pollAircraft, CONFIG.POLL_MS);
pollAircraft();
