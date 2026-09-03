window.PROJECTILE_CONFIG = {
  // The integrated demo is served by the API itself; local standalone frontend
  // development keeps using its separate backend port.
  apiBase:
    window.location.hostname === "localhost" && window.location.port === "5173"
      ? "http://localhost:8000"
      : window.location.origin,
};
