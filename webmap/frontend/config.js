// CBU/KRAL live webmap - config
// Focal-point coordinates resolved via OpenStreetMap Nominatim during planning:
//   CBU campus  -> relation 13218766, centroid 33.9283239, -117.4259206
//   KRAL airport -> way 127837011, centroid 33.9510105, -117.4405868

const CONFIG = {
  POLL_MS: 1000,
  WEATHER_POLL_MS: 60000,   // client poll of our own cheap cache - the 5min
                            // NOAA refresh cadence lives backend-side in
                            // weather.py, this just keeps "as of"/staleness
                            // current without hitting NOAA any harder.
  TRANSIT_POLL_MS: 15000,  // matches transit.py's own backend refresh cadence

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
    ROUTE1_STOPS: "/api/transit/route1/stops",
    ROUTE1_SHAPES: "/api/transit/route1/shapes",
  },
};
