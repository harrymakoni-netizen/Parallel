// The browser application is hosted on Vercel, but the Parallel API, workers, database and engines run on
// long-lived servers (see docs/deployment.md). Until an API is connected, every /api request gets this
// explicit answer, in the API's own error shape, instead of the application's HTML.
// To connect a hosted API, replace the first rewrite in vercel.json with
//   { "source": "/api/(.*)", "destination": "https://your-api.example.org/api/$1" }
export default function handler(_req, res) {
  res.statusCode = 503;
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  res.end(JSON.stringify({
    error: {
      code: "backend_unavailable",
      message: "This deployment is the Parallel web application only; its API is not connected yet.",
      details: null,
    },
  }));
}
