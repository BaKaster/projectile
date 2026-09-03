const isLocalhost = ["localhost", "127.0.0.1", "::1"].includes(
  window.location.hostname,
);

window.PROJECTILE_CONFIG = {
  apiBase: isLocalhost
    ? "http://localhost:8000"
    : "https://projectile-api-production.up.railway.app",
};
