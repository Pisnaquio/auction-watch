import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { SearchGuideDialog, type SearchGuideData } from "./SearchGuide";

type Rule = { term: string; required_any: string[]; excluded_any: string[] };
type Profile = {
  id: string;
  name: string;
  kind: "system" | "user";
  locked: boolean;
  enabled: boolean;
  keywords_any: string[];
  keywords_all: string[];
  exact_phrases: string[];
  exclude_keywords: string[];
  categories: string[];
  boost_keywords: Record<string, number>;
  risk_keywords: Record<string, number>;
  context_rules: Rule[];
  source_ids: string[];
  minimum_score: number;
  price_filter: {
    maximum: string;
    currency: string;
    on_unknown: "include" | "exclude";
  } | null;
  notification_mode: "disabled" | "matches" | "matches_or_failure";
  schedule: { enabled: boolean; times: string[]; timezone: string };
};
type ProfileView = { profile: Profile; revision: number; protected: boolean };
type Run = {
  run_id: string;
  profile_id: string;
  status: "queued" | "running" | "completed" | "partial" | "failed";
  attempt: number;
  error?: string | null;
  finished_at?: string | null;
};
type Notification = {
  dedupe_key: string;
  status: "pending" | "sending" | "sent" | "failed" | "uncertain";
  notification_type: "matches" | "failure";
  attempts: number;
};
type Match = {
  opportunity_key: string;
  score: number;
  matched_terms: string[];
  lot: { title: string; description: string; price_label: string; lot_url: string };
};
type Snapshot = {
  payload: {
    run: { run_id: string; status: string };
    sources: Array<{
      source_id: string;
      status: string;
      inventory_authoritative: boolean;
      errors: string[];
      warnings?: string[];
      skipped_groups?: Array<{
        group_id: string;
        title: string;
        status: "skipped_irrelevant";
        reason: "art_title";
      }>;
    }>;
    profiles: Array<{ profile_id: string; matches: Match[] }>;
    user_states: Array<{ opportunity_key?: string; state: string; version: number }>;
  };
};
type GuidanceWarning = { code: string; field: string; message: string };
type RuntimeState = {
  worker_enabled: boolean;
  worker_running: boolean;
  scheduler_enabled: boolean;
  scheduler_active: boolean;
  timezone: string;
};

const sourceNames: Record<string, string> = {
  bavastro: "Bavastro",
  castells: "Castells",
  prado: "Prado",
  remotes: "Remotes",
  todoremates: "TodoRemates",
};
const sourceIds = Object.keys(sourceNames);
const split = (value: string) =>
  value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
const join = (value: string[]) => value.join(", ");
const copy = (value: Profile) => JSON.parse(JSON.stringify(value)) as Profile;

function emptyProfile(): Profile {
  return {
    id: "",
    name: "",
    kind: "user",
    locked: false,
    enabled: true,
    keywords_any: [],
    keywords_all: [],
    exact_phrases: [],
    exclude_keywords: [],
    categories: [],
    boost_keywords: {},
    risk_keywords: {},
    context_rules: [],
    source_ids: [...sourceIds],
    minimum_score: 0,
    price_filter: null,
    notification_mode: "disabled",
    schedule: { enabled: false, times: [], timezone: "America/Montevideo" },
  };
}

function requestError(path: string, status: number, detail: unknown): Error {
  if (typeof detail === "string") return new Error(`${path}: ${detail}`);
  if (Array.isArray(detail)) {
    const first = detail.find(
      (item): item is { loc?: unknown; msg?: unknown } =>
        Boolean(item && typeof item === "object"),
    );
    if (first) {
      const field = Array.isArray(first.loc)
        ? first.loc.filter((part) => part !== "body").join(".")
        : "";
      const message = typeof first.msg === "string" ? first.msg : "solicitud inválida";
      return new Error(`${path}: ${field ? `${field}: ` : ""}${message}`);
    }
  }
  return new Error(`${path}: Error ${status}`);
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path.replace(/^\//, ""), {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw requestError(path, response.status, body.detail);
  return body as T;
}

function Editor({
  profile,
  selected,
  creating,
  busy,
  warnings,
  onChange,
  onSubmit,
}: {
  profile: Profile;
  selected: ProfileView | null;
  creating: boolean;
  busy: boolean;
  warnings: GuidanceWarning[];
  onChange: <K extends keyof Profile>(key: K, value: Profile[K]) => void;
  onSubmit: (profile: Profile, bypassWarnings: boolean) => void;
}) {
  const [boosts, setBoosts] = useState(JSON.stringify(profile.boost_keywords, null, 2));
  const [contexts, setContexts] = useState(JSON.stringify(profile.context_rules, null, 2));
  useEffect(() => {
    setBoosts(JSON.stringify(profile.boost_keywords, null, 2));
    setContexts(JSON.stringify(profile.context_rules, null, 2));
  }, [profile.boost_keywords, profile.context_rules]);
  const locked = selected?.protected ?? false;
  const canBypassWarnings = warnings.every(
    (warning) => warning.code !== "no_positive_terms",
  );
  const update = <K extends keyof Profile>(key: K, value: Profile[K]) => onChange(key, value);
  function parsedProfile(): Profile {
    return {
      ...profile,
      boost_keywords: JSON.parse(boosts) as Record<string, number>,
      context_rules: JSON.parse(contexts) as Rule[],
    };
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    try {
      onSubmit(parsedProfile(), false);
    } catch {
      window.alert("Revisá los JSON de boosts y reglas contextuales.");
    }
  }
  function saveAnyway() {
    try {
      onSubmit(parsedProfile(), true);
    } catch {
      window.alert("Revisá los JSON de boosts y reglas contextuales.");
    }
  }
  return (
    <form className="panel editor" onSubmit={submit}>
      <div className="panel-heading">
        <div>
          <p className="eyebrow">CRITERIOS DE BÚSQUEDA</p>
          <h2>{creating ? "¿Qué querés encontrar?" : "Editar criterios"}</h2>
        </div>
        {locked && <span className="protected-badge">Perfil protegido</span>}
      </div>
      {creating && (
        <div className="field-grid two">
          <label>
            Identificador
            <input
              onChange={(event) => update("id", event.target.value)}
              placeholder="libros-usados"
              required
              value={profile.id}
            />
          </label>
          <label>
            Nombre visible
            <input
              onChange={(event) => update("name", event.target.value)}
              placeholder="Libros usados"
              required
              value={profile.name}
            />
          </label>
        </div>
      )}
      <label>
        Cualquiera de estos términos
        <input
          disabled={locked}
          onChange={(event) => update("keywords_any", split(event.target.value))}
          placeholder="libro, novela, edición"
          value={join(profile.keywords_any)}
        />
      </label>
      <label>
        Debe incluir todos
        <input
          disabled={locked}
          onChange={(event) => update("keywords_all", split(event.target.value))}
          placeholder="mesa, ping pong"
          value={join(profile.keywords_all)}
        />
      </label>
      <label>
        Frases exactas
        <input
          disabled={locked}
          onChange={(event) => update("exact_phrases", split(event.target.value))}
          placeholder="biblioteca de autor"
          value={join(profile.exact_phrases)}
        />
      </label>
      <label>
        Excluir términos
        <input
          disabled={locked}
          onChange={(event) => update("exclude_keywords", split(event.target.value))}
          placeholder="réplica, incompleto"
          value={join(profile.exclude_keywords)}
        />
      </label>
      <label>
        Categorías aceptadas
        <input
          disabled={locked}
          onChange={(event) => update("categories", split(event.target.value))}
          placeholder="libros, literatura"
          value={join(profile.categories)}
        />
      </label>
      <div className="field-grid two">
        <label>
          Puntaje mínimo
          <input
            disabled={locked}
            min="0"
            onChange={(event) => update("minimum_score", Number(event.target.value))}
            type="number"
            value={profile.minimum_score}
          />
        </label>
        <label>
          Zona horaria
          <input
            disabled={locked}
            onChange={(event) =>
              update("schedule", { ...profile.schedule, timezone: event.target.value })
            }
            value={profile.schedule.timezone}
          />
        </label>
      </div>
      <details>
        <summary>Boosts y reglas contextuales</summary>
        <label>
          Boosts (JSON)
          <textarea disabled={locked} onChange={(event) => setBoosts(event.target.value)} value={boosts} />
        </label>
        <label>
          Reglas contextuales (JSON)
          <textarea disabled={locked} onChange={(event) => setContexts(event.target.value)} value={contexts} />
        </label>
      </details>
      <div className="panel-heading compact">
        <h3>Fuentes</h3>
        <span className="muted">Sólo se consultan las seleccionadas</span>
      </div>
      <div className="source-grid">
        {sourceIds.map((sourceId) => (
          <label className="check" key={sourceId}>
            <input
              checked={profile.source_ids.includes(sourceId)}
              disabled={locked}
              onChange={(event) =>
                update(
                  "source_ids",
                  event.target.checked
                    ? [...profile.source_ids, sourceId]
                    : profile.source_ids.filter((item) => item !== sourceId),
                )
              }
              type="checkbox"
            />
            {sourceNames[sourceId]}
          </label>
        ))}
      </div>
      <div className="panel-heading compact">
        <h3>Precio, frecuencia y alertas</h3>
      </div>
      <label className="check">
        <input
          checked={profile.schedule.enabled}
          disabled={locked}
          onChange={(event) =>
            update("schedule", { ...profile.schedule, enabled: event.target.checked })
          }
          type="checkbox"
        />
        Automatización diaria activa para este perfil
      </label>
      <div className="field-grid three">
        <label>
          Máximo
          <input
            disabled={locked}
            min="0"
            onChange={(event) =>
              update(
                "price_filter",
                event.target.value
                  ? {
                      maximum: event.target.value,
                      currency: profile.price_filter?.currency ?? "UYU",
                      on_unknown: profile.price_filter?.on_unknown ?? "include",
                    }
                  : null,
              )
            }
            step="0.01"
            type="number"
            value={profile.price_filter?.maximum ?? ""}
          />
        </label>
        <label>
          Moneda
          <input
            disabled={locked}
            onChange={(event) =>
              update(
                "price_filter",
                profile.price_filter
                  ? { ...profile.price_filter, currency: event.target.value.toUpperCase() }
                  : {
                      maximum: "1",
                      currency: event.target.value.toUpperCase(),
                      on_unknown: "include",
                    },
              )
            }
            value={profile.price_filter?.currency ?? ""}
          />
        </label>
        <label>
          Horarios
          <input
            disabled={locked}
            onChange={(event) =>
              update("schedule", {
                ...profile.schedule,
                times: split(event.target.value),
              })
            }
            placeholder="09:00, 18:00"
            required={profile.schedule.enabled}
            value={join(profile.schedule.times)}
          />
        </label>
      </div>
      <label>
        Notificaciones
        <select
          disabled={locked}
          onChange={(event) =>
            update("notification_mode", event.target.value as Profile["notification_mode"])
          }
          value={profile.notification_mode}
        >
          <option value="disabled">Desactivadas</option>
          <option value="matches">Nuevos hallazgos o cambios</option>
          <option value="matches_or_failure">Hallazgos y fallos</option>
        </select>
      </label>
      {!locked && warnings.length > 0 && (
        <div className="guidance-warning" role="alert">
          <strong>Revisá antes de guardar</strong>
          <ul>
            {warnings.map((warning) => (
              <li key={warning.code}>{warning.message}</li>
            ))}
          </ul>
          {canBypassWarnings && (
            <button className="button secondary" disabled={busy} onClick={saveAnyway} type="button">
              Guardar de todos modos
            </button>
          )}
        </div>
      )}
      {!locked && (
        <button className="button primary" disabled={busy} type="submit">
          {busy
            ? "Guardando…"
            : warnings.length > 0
              ? "Volver a revisar"
              : creating
                ? "Crear perfil"
                : "Guardar cambios"}
        </button>
      )}
    </form>
  );
}

function Opportunity({
  match,
  state,
  onState,
}: {
  match: Match;
  state: string;
  onState: (key: string, state: "follow" | "discard" | "restore") => void;
}) {
  return (
    <article className={`opportunity-card ${state === "dismissed" ? "dismissed" : ""}`}>
      <div className="opportunity-main">
        <div className="score">
          {match.score}
          <small>score</small>
        </div>
        <div>
          <h3>{match.lot.title}</h3>
          <p>{match.lot.description || "Sin descripción"}</p>
          <div className="tags">
            {match.matched_terms.map((term) => (
              <span key={term}>{term}</span>
            ))}
          </div>
        </div>
      </div>
      <div className="opportunity-meta">
        <span>{match.lot.price_label || "Precio no informado"}</span>
        <a href={match.lot.lot_url} rel="noreferrer" target="_blank">
          Ver publicación ↗
        </a>
        <div className="state-actions">
          {state === "following" ? (
            <button onClick={() => onState(match.opportunity_key, "restore")}>Dejar de seguir</button>
          ) : (
            <button onClick={() => onState(match.opportunity_key, "follow")}>Seguir</button>
          )}
          {state === "dismissed" ? (
            <button onClick={() => onState(match.opportunity_key, "restore")}>Restaurar</button>
          ) : (
            <button onClick={() => onState(match.opportunity_key, "discard")}>Descartar</button>
          )}
        </div>
      </div>
    </article>
  );
}

function App() {
  const [profiles, setProfiles] = useState<ProfileView[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState(emptyProfile());
  const [creating, setCreating] = useState(false);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [retryKey, setRetryKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [guidanceWarnings, setGuidanceWarnings] = useState<GuidanceWarning[]>([]);
  const [guideOpen, setGuideOpen] = useState(false);
  const [guideLoading, setGuideLoading] = useState(false);
  const [guide, setGuide] = useState<SearchGuideData | null>(null);
  const [runtime, setRuntime] = useState<RuntimeState | null>(null);
  const selected = profiles.find((item) => item.profile.id === selectedId) ?? null;

  const loadProfiles = useCallback(async () => {
    setLoading(true);
    try {
      const [result, runtimeState] = await Promise.all([
        api<ProfileView[]>("/api/v1/profiles"),
        api<RuntimeState>("/api/v1/runtime"),
      ]);
      setProfiles(result);
      setRuntime(runtimeState);
      setSelectedId((current) =>
        current && result.some((item) => item.profile.id === current)
          ? current
          : (result[0]?.profile.id ?? null),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudieron cargar los perfiles");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadData = useCallback(async (profileId: string) => {
    setSnapshot(null);
    try {
      const [runs, mails] = await Promise.all([
        api<Run[]>(`/api/v1/profiles/${encodeURIComponent(profileId)}/runs`),
        api<Notification[]>(`/api/v1/profiles/${encodeURIComponent(profileId)}/notifications`),
      ]);
      setHistory(runs);
      setNotifications(mails);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo cargar el historial");
    }
    try {
      setSnapshot(await api<Snapshot>(`/api/v1/profiles/${encodeURIComponent(profileId)}/snapshot`));
    } catch (reason) {
      if (reason instanceof Error && !reason.message.includes("snapshot")) setError(reason.message);
    }
  }, []);

  useEffect(() => {
    void loadProfiles();
  }, [loadProfiles]);

  useEffect(() => {
    if (selectedId && !creating) {
      setDraft(copy(profiles.find((item) => item.profile.id === selectedId)?.profile ?? emptyProfile()));
      setGuidanceWarnings([]);
      void loadData(selectedId);
    }
  }, [creating, loadData, profiles, selectedId]);

  const updateDraft = <K extends keyof Profile>(key: K, value: Profile[K]) => {
    setGuidanceWarnings([]);
    setDraft((current) => ({ ...current, [key]: value }));
  };

  async function openGuide() {
    setGuideOpen(true);
    if (guide) return;
    setGuideLoading(true);
    try {
      setGuide(await api<SearchGuideData>("/api/v1/search-guide"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo cargar la guía");
    } finally {
      setGuideLoading(false);
    }
  }

  async function saveProfile(profile: Profile, bypassWarnings: boolean) {
    setDraft(profile);
    if (!profile.id || !profile.name) return;
    setBusy(true);
    setError(null);
    try {
      if (!bypassWarnings) {
        const guidance = await api<{ warnings: GuidanceWarning[] }>("/api/v1/search-guidance", {
          method: "POST",
          body: JSON.stringify({ profile }),
        });
        if (guidance.warnings.length > 0) {
          setGuidanceWarnings(guidance.warnings);
          return;
        }
      }
      setGuidanceWarnings([]);
      if (creating) {
        const created = await api<ProfileView>("/api/v1/profiles", {
          method: "POST",
          body: JSON.stringify({ profile }),
        });
        setProfiles((items) =>
          [...items, created].sort((a, b) => a.profile.id.localeCompare(b.profile.id)),
        );
        setSelectedId(created.profile.id);
        setCreating(false);
        setMessage("Perfil creado.");
      } else if (selected) {
        const updated = await api<ProfileView>(
          `/api/v1/profiles/${encodeURIComponent(selected.profile.id)}`,
          {
            method: "PATCH",
            body: JSON.stringify({ profile, expected_revision: selected.revision }),
          },
        );
        setProfiles((items) =>
          items.map((item) => (item.profile.id === updated.profile.id ? updated : item)),
        );
        setMessage("Cambios guardados.");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo guardar el perfil");
    } finally {
      setBusy(false);
    }
  }

  async function startRun() {
    if (!selected || run?.status === "queued" || run?.status === "running") return;
    const key =
      retryKey ??
      (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
    setRetryKey(key);
    setError(null);
    setMessage("Enviando solicitud…");
    try {
      let current = await api<Run>("/api/v1/runs", {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: JSON.stringify({ profile_id: selected.profile.id }),
      });
      setRun(current);
      setMessage("Corrida encolada…");
      const deadline = Date.now() + 30000;
      while (current.status === "queued" || current.status === "running") {
        if (Date.now() >= deadline) throw new Error("timeout");
        await new Promise((resolve) => window.setTimeout(resolve, 500));
        current = await api<Run>(`/api/v1/runs/${encodeURIComponent(current.run_id)}`);
        setRun(current);
      }
      setRetryKey(null);
      setMessage(
        current.status === "completed"
          ? "Corrida completa."
          : current.status === "partial"
            ? "Resultado parcial: revisá la cobertura."
            : "La corrida falló.",
      );
      await loadData(selected.profile.id);
    } catch (reason) {
      setMessage(null);
      setError(
        reason instanceof Error && reason.message === "timeout"
          ? "La corrida sigue en curso; podés reintentar de forma segura."
          : reason instanceof Error
            ? reason.message
            : "La corrida falló",
      );
    }
  }

  async function toggleProfile() {
    if (!selected) return;
    setBusy(true);
    try {
      const action = selected.profile.enabled ? "pause" : "resume";
      const updated = await api<ProfileView>(
        `/api/v1/profiles/${encodeURIComponent(selected.profile.id)}/${action}`,
        { method: "POST" },
      );
      setProfiles((items) =>
        items.map((item) => (item.profile.id === updated.profile.id ? updated : item)),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo cambiar el estado");
    } finally {
      setBusy(false);
    }
  }

  async function setState(key: string, state: "follow" | "discard" | "restore") {
    if (!selected) return;
    const existing = snapshot?.payload.user_states.find((item) => item.opportunity_key === key);
    try {
      await api(`/api/v1/profiles/${encodeURIComponent(selected.profile.id)}/opportunities/state`, {
        method: "POST",
        body: JSON.stringify({
          opportunity_key: key,
          state,
          expected_version: existing?.version,
        }),
      });
      await loadData(selected.profile.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo actualizar la oportunidad");
    }
  }

  const matches = useMemo(
    () =>
      snapshot?.payload.profiles.find((item) => item.profile_id === selectedId)?.matches ?? [],
    [selectedId, snapshot],
  );
  const authoritative =
    snapshot?.payload.sources.every(
      (source) => source.status === "complete" && source.inventory_authoritative,
    ) ?? false;
  const degradedSources =
    snapshot?.payload.sources.filter(
      (source) => source.status !== "complete" || !source.inventory_authoritative,
    ) ?? [];
  const skippedSources =
    snapshot?.payload.sources.filter((source) => (source.skipped_groups?.length ?? 0) > 0) ?? [];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">AW</span>
          <div>
            <strong>Auction Watch</strong>
            <small>perfiles independientes</small>
          </div>
        </div>
        <button
          className="new-profile"
          onClick={() => {
            setCreating(true);
            setSelectedId(null);
            setDraft(emptyProfile());
            setSnapshot(null);
            setGuidanceWarnings([]);
          }}
        >
          + Nueva búsqueda
        </button>
        <p className="eyebrow">PERFILES</p>
        {loading ? (
          <p className="muted">Cargando perfiles…</p>
        ) : (
          profiles.map((item) => (
            <button
              className={`profile-link ${item.profile.id === selectedId ? "selected" : ""}`}
              key={item.profile.id}
              onClick={() => {
                setCreating(false);
                setSelectedId(item.profile.id);
                setGuidanceWarnings([]);
              }}
            >
              <span>{item.profile.name}</span>
              <small>{item.protected ? "Protegido" : item.profile.enabled ? "Activo" : "Pausado"}</small>
            </button>
          ))
        )}
      </aside>
      <main className="content">
        <header className="topbar">
          <div>
            <p className="eyebrow">MONITOR DE OPORTUNIDADES</p>
            <h1>{creating ? "Crear búsqueda" : (selected?.profile.name ?? "Tus perfiles")}</h1>
          </div>
          <div className="top-actions">
            {runtime && (
              <span
                className={`status-pill ${runtime.scheduler_active ? "on" : "off"}`}
                title={`Worker ${runtime.worker_running ? "activo" : "inactivo"}; zona ${runtime.timezone}`}
              >
                Automatización {runtime.scheduler_active ? "activa" : "inactiva"}
              </span>
            )}
            <button className="button help-button" onClick={() => void openGuide()}>
              ? Cómo buscar mejor
            </button>
            {selected && (
              <>
                <span className={`status-pill ${selected.profile.enabled ? "on" : "off"}`}>
                  {selected.profile.enabled ? "Activo" : "Pausado"}
                </span>
                <button className="button secondary" disabled={busy} onClick={() => void toggleProfile()}>
                  {selected.profile.enabled ? "Pausar" : "Reanudar"}
                </button>
              </>
            )}
          </div>
        </header>
        {error && <div className="notice error">{error}</div>}
        {message && <div className="notice success">{message}</div>}
        {creating || selected ? (
          <div className="workspace">
            <Editor
              busy={busy}
              creating={creating}
              onChange={updateDraft}
              onSubmit={(profile, bypassWarnings) =>
                void saveProfile(profile, bypassWarnings)
              }
              profile={draft}
              selected={selected}
              warnings={guidanceWarnings}
            />
            <section className="panel opportunities">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">RESULTADO CANÓNICO</p>
                  <h2>Oportunidades</h2>
                </div>
                <button
                  className="button primary"
                  disabled={run?.status === "queued" || run?.status === "running" || busy}
                  onClick={() => void startRun()}
                >
                  {run?.status === "queued"
                    ? "En cola…"
                    : run?.status === "running"
                      ? "Consultando…"
                      : "Actualizar ahora"}
                </button>
              </div>
              {snapshot && !authoritative && (
                <div className="coverage-warning">
                  <strong>Cobertura parcial</strong>
                  <span>No se interpreta como “sin resultados”.</span>
                  {degradedSources.map((source) => (
                    <small key={source.source_id}>
                      {sourceNames[source.source_id] ?? source.source_id}: {source.status}
                      {source.errors.length > 0 ? ` — ${source.errors.join("; ")}` : ""}
                    </small>
                  ))}
                </div>
              )}
              {snapshot && skippedSources.length > 0 && (
                <div className="coverage-warning">
                  <strong>Remates descartados por título</strong>
                  <span>
                    Se omitieron únicamente grupos inequívocamente artísticos antes de consultar
                    sus lotes.
                  </span>
                  {skippedSources.map((source) => (
                    <small key={source.source_id}>
                      {sourceNames[source.source_id] ?? source.source_id}: {source.skipped_groups?.length ?? 0}
                    </small>
                  ))}
                </div>
              )}
              {snapshot && authoritative && matches.length === 0 && (
                <div className="empty-state">
                  <span>○</span>
                  <strong>Sin oportunidades por ahora.</strong>
                  <p>La cobertura fue autoritativa en la última corrida.</p>
                </div>
              )}
              {!snapshot && (
                <div className="empty-state">
                  <span>◌</span>
                  <strong>Todavía no hay un snapshot.</strong>
                  <p>Actualizá para consultar las fuentes seleccionadas.</p>
                </div>
              )}
              <div className="opportunity-list">
                {matches.map((match) => (
                  <Opportunity
                    key={match.opportunity_key}
                    match={match}
                    onState={(key, state) => void setState(key, state)}
                    state={
                      snapshot?.payload.user_states.find(
                        (item) => item.opportunity_key === match.opportunity_key,
                      )?.state ?? "none"
                    }
                  />
                ))}
              </div>
              <div className="history">
                <div className="panel-heading compact">
                  <h3>Historial de corridas</h3>
                </div>
                {history.length === 0 ? (
                  <p className="muted">No hay corridas registradas.</p>
                ) : (
                  history.slice(0, 5).map((item) => (
                    <div className="history-row" key={item.run_id}>
                      <span className={`run-dot ${item.status}`} />
                      <span>
                        {item.status === "completed"
                          ? "Completa"
                          : item.status === "partial"
                            ? "Parcial"
                            : item.status === "failed"
                              ? "Falló"
                              : item.status === "queued"
                                ? "En cola"
                                : "En curso"}
                      </span>
                      <small>
                        {item.finished_at ? new Date(item.finished_at).toLocaleString() : "Pendiente"}
                      </small>
                    </div>
                  ))
                )}
              </div>
              <div className="history">
                <div className="panel-heading compact">
                  <h3>Estado de entrega</h3>
                </div>
                {notifications.length === 0 ? (
                  <p className="muted">Sin notificaciones pendientes.</p>
                ) : (
                  notifications.slice(0, 5).map((item) => (
                    <div className="history-row" key={item.dedupe_key}>
                      <span className={`run-dot ${item.status}`} />
                      <span>
                        {item.status === "sent"
                          ? "Enviado"
                          : item.status === "failed"
                            ? "Falló"
                            : item.status === "pending"
                              ? "Pendiente"
                              : "Enviando"}
                      </span>
                      <small>
                        {item.notification_type === "failure" ? "Fallo de corrida" : "Nuevo hallazgo"}
                      </small>
                    </div>
                  ))
                )}
              </div>
            </section>
          </div>
        ) : (
          <div className="empty-state welcome">
            <span>✦</span>
            <strong>Creá una búsqueda independiente.</strong>
            <p>Elegí términos, fuentes, precio y frecuencia.</p>
            <button
              className="button primary"
              onClick={() => {
                setCreating(true);
                setDraft(emptyProfile());
                setGuidanceWarnings([]);
              }}
            >
              Crear primera búsqueda
            </button>
          </div>
        )}
      </main>
      {guideOpen && (
        <SearchGuideDialog
          guide={guide}
          loading={guideLoading}
          onClose={() => setGuideOpen(false)}
        />
      )}
    </div>
  );
}

export default App;
