// Foodish configuration — edit these three values after setup (see README.md).
window.FOODISH_CONFIG = {
  // Where the daily deal file lives. While testing, the copy next to this page works.
  // After setup, point it at your GitHub repo's raw file, e.g.
  // "https://raw.githubusercontent.com/YOUR-GITHUB-NAME/foodish/main/data/deals.json"
  DATA_URL: "data/deals.json",

  // Cloudflare Worker URL for alerts + deal submissions (leave "" until set up).
  // e.g. "https://foodish-api.YOUR-SUBDOMAIN.workers.dev"
  API_URL: "",

  // Public VAPID key for web push (generated in setup step 4). Leave "" to hide push alerts.
  VAPID_PUBLIC_KEY: "",

  // Fallback map centre if the visitor declines location (Downtown Edmonton).
  DEFAULT_CENTER: [53.5444, -113.4909],
  TIMEZONE: "America/Edmonton",
  CITY: "Edmonton",
};
