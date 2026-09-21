// CBU/KRAL live webmap - config
// Focal-point coordinates resolved via OpenStreetMap Nominatim during planning:
//   CBU campus  -> relation 13218766, centroid 33.9283239, -117.4259206
//   KRAL airport -> way 127837011, centroid 33.9510105, -117.4405868

const CONFIG = {
  POLL_MS: 2000,

  MODEL_3D: {
    // Below this zoom the flat icon is used instead - a to-scale aircraft
    // is sub-pixel when zoomed out, so 3D only earns its keep up close.
    MIN_ZOOM: 12.5,
    // A Cessna/Piper is ~8m long, which is invisible at map scale, so the
    // model is deliberately exaggerated. Tune this if planes look wrong.
    LENGTH_METERS: 120,
    // Flip if the nose ends up pointing the wrong way along the track.
    HEADING_OFFSET_DEG: 0,
  },

  FOCAL_POINTS: {
    overview: {
      label: "Overview",
      center: [-117.4433, 33.9430],
      zoom: 10.3,
      pitch: 0,
      bearing: 0,
    },
    cbu: {
      label: "CBU Campus",
      center: [-117.4259206, 33.9283239],
      zoom: 16.5,
      pitch: 60,
      bearing: -20,
    },
    kral: {
      label: "KRAL Airport",
      center: [-117.4405868, 33.9510105],
      zoom: 15.5,
      pitch: 55,
      bearing: 30,
    },
  },

  TILES: {
    STREET_STYLE_URL: "https://tiles.openfreemap.org/styles/liberty",
  },

  GEO_DATA: {
    // Served by the backend rather than as a static file so that local
    // name/description overrides in data/building_info.json are merged in.
    CBU_BUILDINGS: "/api/buildings",
    CBU_CAMPUS: "/static/geo/cbu_campus.geojson",
    KRAL_AIRPORT: "/static/geo/kral_airport.geojson",
  },
};
