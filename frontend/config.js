const isLocalhost = ["localhost", "127.0.0.1", "::1"].includes(
  window.location.hostname,
);
const productionApi = "https://projectile-api-production.up.railway.app";

window.PROJECTILE_CONFIG = {
  // The API also serves an integrated demo frontend at its own origin.
  apiBase: isLocalhost
    ? "http://localhost:8000"
    : window.location.origin === productionApi
      ? window.location.origin
      : productionApi,
};
