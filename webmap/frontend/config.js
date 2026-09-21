// CBU/KRAL live webmap - config
// Focal-point coordinates resolved via OpenStreetMap Nominatim during planning:
//   CBU campus  -> relation 13218766, centroid 33.9283239, -117.4259206
//   KRAL airport -> way 127837011, centroid 33.9510105, -117.4405868

const CONFIG = {
  POLL_MS: 5000,

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

  // Simple client-side geofence circle around CBU (we only have building
  // footprints from OSM, not an official campus boundary polygon, so a
  // fixed-radius circle is the honest way to draw "a geofence around CBU").
  CBU_GEOFENCE_CENTER: [-117.4259206, 33.9283239],
  CBU_GEOFENCE_RADIUS_M: 900,

  TILES: {
    STREET_STYLE_URL: "https://tiles.openfreemap.org/styles/liberty",
    SATELLITE_TILE_URL:
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    SATELLITE_ATTRIBUTION: "Esri, Maxar, Earthstar Geographics",
  },

  GEO_DATA: {
    CBU_BUILDINGS: "/static/geo/cbu_buildings.geojson",
    KRAL_AIRPORT: "/static/geo/kral_airport.geojson",
  },
};
