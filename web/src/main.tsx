import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Health = {
  ok: boolean;
  service: string;
  version: string;
};

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [available, setAvailable] = useState<boolean | null>(null);

  useEffect(() => {
    fetch("/api/v1/health")
      .then((response) => {
        setAvailable(response.ok);
        return response.json() as Promise<Health>;
      })
      .then(setHealth)
      .catch(() => setAvailable(false));
  }, []);

  return (
    <main>
      <h1>Auction Watch</h1>
      <p className={available ? "available" : "unavailable"}>
        {available === null ? "Consultando estado…" : available ? "Disponible" : "No disponible"}
      </p>
      {health && <small>Servicio {health.service} · versión {health.version}</small>}
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
