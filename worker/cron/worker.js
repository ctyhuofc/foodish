// Foodish exact-time trigger: asks GitHub to run the daily deals update at 4:00 am Edmonton time,
// instead of waiting on GitHub's own scheduler (which can run hours late).
// Needs one secret: GITHUB_TOKEN — a fine-grained token for ctyhuofc/foodish with
// "Actions: Read and write" permission only.  Set it with:  npx wrangler secret put GITHUB_TOKEN

const RUN_AT_HOUR = 4; // Edmonton local time

function edmontonHour(date) {
  return Number(new Intl.DateTimeFormat("en-CA", { timeZone: "America/Edmonton", hour: "numeric", hourCycle: "h23" }).format(date));
}

async function dispatch(env, task) {
  const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.WORKFLOW}/dispatches`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      accept: "application/vnd.github+json",
      "x-github-api-version": "2022-11-28",
      "user-agent": "foodish-cron",
    },
    body: JSON.stringify({ ref: "main", inputs: { task } }),
  });
  if (res.status !== 204) throw new Error(`GitHub said ${res.status}: ${await res.text()}`);
}

export default {
  async scheduled(event, env, ctx) {
    if (edmontonHour(new Date(event.scheduledTime)) !== RUN_AT_HOUR) return; // the other UTC slot (daylight saving)
    await dispatch(env, "scrape");
    console.log("Asked GitHub to refresh Foodish deals");
  },
  // Visiting the worker's URL shows whether it's set up (it never reveals the token).
  async fetch(request, env) {
    const ready = Boolean(env.GITHUB_TOKEN);
    return new Response(ready ? "Foodish trigger is set up. Next refresh: 4:00 am Edmonton time.\n"
                              : "Missing GITHUB_TOKEN. Run: npx wrangler secret put GITHUB_TOKEN\n",
      { status: ready ? 200 : 500, headers: { "content-type": "text/plain" } });
  },
};
