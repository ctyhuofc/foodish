// Foodish configuration (see README.md).
window.FOODISH_CONFIG = {
  // The cities Foodish covers. Keep in step with cities.json (tests/test_cities.py checks they match).
  // dataUrl is where each city's daily deal file lives. While testing on your computer you can use the
  // copies next to this page instead: "data/deals.json", "data/vancouver/deals.json" and "data/toronto/deals.json".
  CITIES: [
    {
      id: "edmonton", name: "Edmonton", label: "Downtown Edmonton",
      center: [53.5444, -113.4909], timezone: "America/Edmonton",
      dataUrl: "https://raw.githubusercontent.com/ctyhuofc/foodish/main/data/deals.json",
    },
    {
      id: "vancouver", name: "Vancouver", label: "Downtown Vancouver",
      center: [49.2827, -123.1207], timezone: "America/Vancouver",
      dataUrl: "https://raw.githubusercontent.com/ctyhuofc/foodish/main/data/vancouver/deals.json",
    },
    {
      id: "toronto", name: "Toronto", label: "Downtown Toronto",
      center: [43.6532, -79.3832], timezone: "America/Toronto",
      dataUrl: "https://raw.githubusercontent.com/ctyhuofc/foodish/main/data/toronto/deals.json",
    },
  ],
  // Where corrections, removal requests and emailed deals go (also on /bot and /privacy).
  CONTACT_EMAIL: "yates@foodish.ca",

  // Shown when we don't know where the visitor is and they haven't picked a city.
  DEFAULT_CITY: "edmonton",

  // Cloudflare Worker URL for alerts + deal submissions (leave "" until set up).
  // e.g. "https://foodish-api.YOUR-SUBDOMAIN.workers.dev"
  API_URL: "",

  // Public VAPID key for web push (generated in setup step 4). Leave "" to hide push alerts.
  VAPID_PUBLIC_KEY: "",
};
