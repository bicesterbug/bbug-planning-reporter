export default function Home() {
  return (
    <main style={{ fontFamily: "system-ui", padding: "2rem", maxWidth: 640 }}>
      <h1>BBUG Planning Reporter</h1>
      <p>
        Serverless control plane for the Cherwell cycling-advocacy assessment agent
        (Claude Managed Agents). This is a backend service — see <code>README.md</code>.
      </p>
      <ul>
        <li><code>POST /api/trigger</code> — start a review (autonomous or cowork)</li>
        <li><code>POST /api/webhook</code> — Anthropic session webhook (driver)</li>
      </ul>
    </main>
  );
}
