import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { SearchGuideDialog, type SearchGuideData } from "./SearchGuide";
import { editTermInput, parseTermInput } from "./term-inputs.js";

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
type ProfileView = {
  profile: Profile;
  revision: number;
  protected: boolean;
  reviewed_at: string | null;
};
type OpportunityState = "none" | "following" | "dismissed";
type OpportunityTab = "todas" | "nuevas" | "siguiendo" | "descartadas";
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
  // Absent on snapshots published before novelty was exposed; those read as "not new".
  first_match_at?: string | null;
  last_match_at?: string | null;
  lot: {
    auction_id: string;
    title: string;
    description: string;
    price_label: string;
    lot_url: string;
    closing_at: string | null;
    image_url: string | null;
  };
};
type ClosingTodayItem = {
  profileId: string;
  profileName: string;
  opportunityKey: string;
  auctionGroupKey: string;
  title: string;
  lotUrl: string;
  closingAt: string;
};
type Snapshot = {
  payload: {
    run: { run_id: string; status: string };
    sources: Array<{
      source_id: string;
      status: string;
      inventory_authoritative: boolean;
      omission_authoritative?: boolean;
      errors: string[];
      warnings?: string[];
      skipped_groups?: Array<{
        group_id: string;
        title: string;
        status: "skipped_irrelevant";
        reason: "art_title";
      }>;
      diagnostics?: Array<{
        group_id: string;
        status: "adaptive_recovered" | "shadow_only";
        category: string;
        confidence: "high" | "medium" | "low";
        path?: string | null;
        fingerprint: string;
      }>;
    }>;
    profiles: Array<{ profile_id: string; matches: Match[] }>;
    user_states: Array<{ opportunity_key?: string; state: OpportunityState; version: number }>;
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
type TermField =
  | "keywords_any"
  | "keywords_all"
  | "exact_phrases"
  | "exclude_keywords"
  | "categories"
  | "schedule_times";
type TermInputs = Record<TermField, string>;

function SectionIcon({ path }: { path: string }) {
  return (
    <svg fill="none" height="14" stroke="#55b7a9" strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} viewBox="0 0 24 24" width="14">
      <path d={path} />
    </svg>
  );
}
const ICON_TERMS = "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm10 17-4.3-4.3";
const ICON_SCOPE = "M3 3v18h18M7 15l4-6 4 3 5-7";
const ICON_SOURCES = "M3 4h18v16H3zM3 9h18";
const ICON_BOLT = "M13 2 3 14h7l-1 8 10-12h-7l1-8Z";

function StatusIcon({ path, tone }: { path: string; tone: "teal" | "amber" | "green" }) {
  const colors = {
    teal: { bg: "#e5f5f1", fg: "#147267" },
    amber: { bg: "#fff3c7", fg: "#78611c" },
    green: { bg: "#d9f4ec", fg: "#13715e" },
  }[tone];
  return (
    <div className="status-strip-icon" style={{ background: colors.bg, color: colors.fg }}>
      <svg fill="none" height="16" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} viewBox="0 0 24 24" width="16">
        <path d={path} />
      </svg>
    </div>
  );
}
const ICON_CLOCK = "M12 12V7M12 12l3 3M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z";
const ICON_CALENDAR = "M12 8v5l4 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z";
const ICON_BELL = "M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9ZM13.7 21a2 2 0 0 1-3.4 0";

const sourceNames: Record<string, string> = {
  bavastro: "Bavastro",
  castells: "Castells",
  prado: "Prado",
  remotes: "Remotes",
  todoremates: "TodoRemates",
};
const sourceIds = Object.keys(sourceNames);
const join = (value: string[]) => value.join(", ");
const copy = (value: Profile) => JSON.parse(JSON.stringify(value)) as Profile;
const termInputsFromProfile = (profile: Profile): TermInputs => ({
  keywords_any: join(profile.keywords_any),
  keywords_all: join(profile.keywords_all),
  exact_phrases: join(profile.exact_phrases),
  exclude_keywords: join(profile.exclude_keywords),
  categories: join(profile.categories),
  schedule_times: join(profile.schedule.times),
});

function dateKey(date: Date, timezone: string): string | null {
  if (Number.isNaN(date.getTime())) return null;
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(date);
    const value = Object.fromEntries(
      parts
        .filter((part) => part.type !== "literal")
        .map((part) => [part.type, part.value]),
    );
    return `${value.year}-${value.month}-${value.day}`;
  } catch {
    return null;
  }
}

function closesToday(closingAt: string | null, timezone: string): boolean {
  if (!closingAt) return false;
  const today = dateKey(new Date(), timezone);
  return today !== null && dateKey(new Date(closingAt), timezone) === today;
}

// The backend identifier is [a-z0-9] joined by hyphens. Nobody should have to
// know that: "Máquinas de fotos" becomes "maquinas-de-fotos" on its own.
function toSlug(value: string, { trim = true }: { trim?: boolean } = {}): string {
  const base = value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+/, "");
  // While typing, a trailing hyphen is the separator the next word needs.
  return trim ? base.replace(/-+$/, "") : base;
}

function closingLabel(closingAt: string | null, timezone: string): string {
  if (!closingAt) return "Sin fecha de cierre";
  const when = new Date(closingAt);
  if (Number.isNaN(when.getTime())) return "Sin fecha de cierre";
  try {
    const time = new Intl.DateTimeFormat("es-UY", {
      timeZone: timezone,
      hour: "2-digit",
      minute: "2-digit",
    }).format(when);
    if (closesToday(closingAt, timezone)) return `Cierra hoy ${time}`;
    const day = new Intl.DateTimeFormat("es-UY", {
      timeZone: timezone,
      day: "2-digit",
      month: "2-digit",
    }).format(when);
    // Sources keep listing lots whose closing date already passed; announcing
    // those as still closing would be a plain lie.
    return `${when.getTime() < Date.now() ? "Cerró" : "Cierra"} ${day} ${time}`;
  } catch {
    return "Sin fecha de cierre";
  }
}

function hasClosed(closingAt: string | null): boolean {
  if (!closingAt) return false;
  const when = Date.parse(closingAt);
  return !Number.isNaN(when) && when < Date.now();
}

// Upcoming first (soonest on top), then undated, then already closed: a lot
// whose auction is over is the least actionable and must not lead the list.
function closingSoonest(left: Match, right: Match): number {
  const rank = (match: Match): [number, number] => {
    const at = match.lot.closing_at ? Date.parse(match.lot.closing_at) : Number.NaN;
    if (Number.isNaN(at)) return [1, 0];
    return at >= Date.now() ? [0, at] : [2, -at];
  };
  const [leftGroup, leftAt] = rank(left);
  const [rightGroup, rightAt] = rank(right);
  if (leftGroup !== rightGroup) return leftGroup - rightGroup;
  if (leftAt !== rightAt) return leftAt - rightAt;
  return left.opportunity_key.localeCompare(right.opportunity_key);
}

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
  termInputs,
  onChange,
  onTermInputChange,
  onSubmit,
}: {
  profile: Profile;
  selected: ProfileView | null;
  creating: boolean;
  busy: boolean;
  warnings: GuidanceWarning[];
  termInputs: TermInputs;
  onChange: <K extends keyof Profile>(key: K, value: Profile[K]) => void;
  onTermInputChange: (key: TermField, value: string) => void;
  onSubmit: (profile: Profile, bypassWarnings: boolean) => void;
}) {
  const [boosts, setBoosts] = useState(JSON.stringify(profile.boost_keywords, null, 2));
  const [contexts, setContexts] = useState(JSON.stringify(profile.context_rules, null, 2));
  useEffect(() => {
    setBoosts(JSON.stringify(profile.boost_keywords, null, 2));
    setContexts(JSON.stringify(profile.context_rules, null, 2));
  }, [profile.boost_keywords, profile.context_rules]);
  const [idTouched, setIdTouched] = useState(false);
  const locked = selected?.protected ?? false;
  const canBypassWarnings = warnings.every(
    (warning) => warning.code !== "no_positive_terms",
  );
  const update = <K extends keyof Profile>(key: K, value: Profile[K]) => onChange(key, value);
  function parsedProfile(): Profile {
    return {
      ...profile,
      id: toSlug(profile.id),
      keywords_any: parseTermInput(termInputs.keywords_any),
      keywords_all: parseTermInput(termInputs.keywords_all),
      exact_phrases: parseTermInput(termInputs.exact_phrases),
      exclude_keywords: parseTermInput(termInputs.exclude_keywords),
      categories: parseTermInput(termInputs.categories),
      schedule: {
        ...profile.schedule,
        times: parseTermInput(termInputs.schedule_times),
      },
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
              onBlur={(event) => update("id", toSlug(event.target.value))}
              onChange={(event) => {
                setIdTouched(true);
                update("id", toSlug(event.target.value, { trim: false }));
              }}
              placeholder="libros-usados"
              required
              value={profile.id}
            />
            <small className="field-hint">
              Se arma solo con el nombre. Lo podés cambiar.
            </small>
          </label>
          <label>
            Nombre visible
            <input
              onChange={(event) => {
                update("name", event.target.value);
                if (!idTouched) update("id", toSlug(event.target.value));
              }}
              placeholder="Libros usados"
              required
              value={profile.name}
            />
          </label>
        </div>
      )}
      <div className="field-section">
        <div className="field-section-head">
          <SectionIcon path={ICON_TERMS} />
          <h3>Términos de búsqueda</h3>
        </div>
        <label>
          Cualquiera de estos términos
          <input
            disabled={locked}
            onChange={(event) => onTermInputChange("keywords_any", event.target.value)}
            placeholder="libro, novela, edición"
            value={termInputs.keywords_any}
          />
        </label>
        <label>
          Debe incluir todos
          <input
            disabled={locked}
            onChange={(event) => onTermInputChange("keywords_all", event.target.value)}
            placeholder="mesa, ping pong"
            value={termInputs.keywords_all}
          />
        </label>
        <label>
          Frases exactas
          <input
            disabled={locked}
            onChange={(event) => onTermInputChange("exact_phrases", event.target.value)}
            placeholder="biblioteca de autor"
            value={termInputs.exact_phrases}
          />
        </label>
        <label>
          Excluir términos
          <input
            disabled={locked}
            onChange={(event) => onTermInputChange("exclude_keywords", event.target.value)}
            placeholder="réplica, incompleto"
            value={termInputs.exclude_keywords}
          />
        </label>
      </div>
      <div className="field-section">
        <div className="field-section-head">
          <SectionIcon path={ICON_SCOPE} />
          <h3>Alcance</h3>
        </div>
        <label>
          Categorías aceptadas
          <input
            disabled={locked}
            onChange={(event) => onTermInputChange("categories", event.target.value)}
            placeholder="libros, literatura"
            value={termInputs.categories}
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
      </div>
      <details>
        <summary>
          <SectionIcon path={ICON_BOLT} /> Boosts y reglas contextuales
        </summary>
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
        <h3>
          <SectionIcon path={ICON_SOURCES} /> Fuentes
        </h3>
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
        <h3>
          <SectionIcon path={ICON_BOLT} /> Precio, frecuencia y alertas
        </h3>
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
              onTermInputChange("schedule_times", event.target.value)
            }
            placeholder="09:00, 18:00"
            required={profile.schedule.enabled}
            value={termInputs.schedule_times}
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
  isNew,
  timezone,
  onState,
}: {
  match: Match;
  state: OpportunityState;
  isNew: boolean;
  timezone: string;
  onState: (key: string, state: "follow" | "discard" | "restore") => void;
}) {
  // Castells never publishes images, so roughly half the cards have none;
  // a broken one must collapse rather than leave a torn placeholder.
  const [imageBroken, setImageBroken] = useState(false);
  const ringPct = Math.max(0, Math.min(100, match.score));
  const closesTodayHere = closesToday(match.lot.closing_at, timezone);
  return (
    <article className={`opportunity-card ${state === "none" ? "" : state}`}>
      <div className="opportunity-main">
        <div
          className="score-ring"
          style={{ background: `conic-gradient(#1c766f ${ringPct * 3.6}deg, #e5f5f1 0deg)` }}
        >
          <div className="score-ring-inner">
            <strong>{match.score}</strong>
            <small>score</small>
          </div>
        </div>
        <div>
          {(isNew || state !== "none") && (
            <div className="opportunity-badges">
              {isNew && <span className="badge new">Nueva</span>}
              {state === "following" && <span className="badge following">Siguiendo</span>}
              {state === "dismissed" && <span className="badge dismissed">Descartada</span>}
            </div>
          )}
          <h3>{match.lot.title}</h3>
          <p>{match.lot.description || "Sin descripción"}</p>
          <div className="tags">
            {match.matched_terms.map((term) => (
              <span key={term}>{term}</span>
            ))}
          </div>
        </div>
        <div className="opportunity-thumb">
          {match.lot.image_url && !imageBroken && (
            <a href={match.lot.lot_url} rel="noreferrer" target="_blank">
              <img
                alt=""
                loading="lazy"
                onError={() => setImageBroken(true)}
                referrerPolicy="no-referrer"
                src={match.lot.image_url}
              />
            </a>
          )}
        </div>
      </div>
      <div className="opportunity-meta">
        <span>{match.lot.price_label || "Precio no informado"}</span>
        <span
          className={`closing${closesTodayHere ? " today" : ""}${
            hasClosed(match.lot.closing_at) ? " closed" : ""
          }`}
        >
          {closingLabel(match.lot.closing_at, timezone)}
        </span>
        <a href={match.lot.lot_url} rel="noreferrer" target="_blank">
          Ver publicación ↗
        </a>
        <div className="state-actions">
          {state === "dismissed" ? (
            <button onClick={() => onState(match.opportunity_key, "restore")}>Restaurar</button>
          ) : (
            <>
              {state === "following" ? (
                <button onClick={() => onState(match.opportunity_key, "restore")}>
                  Dejar de seguir
                </button>
              ) : (
                <button onClick={() => onState(match.opportunity_key, "follow")}>Seguir</button>
              )}
              <button
                className="discard"
                onClick={() => onState(match.opportunity_key, "discard")}
              >
                Descartar
              </button>
            </>
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
  const [termInputs, setTermInputs] = useState<TermInputs>(() =>
    termInputsFromProfile(emptyProfile()),
  );
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
  const [closingTodayAll, setClosingTodayAll] = useState<ClosingTodayItem[]>([]);
  const [closingTodayAllOpen, setClosingTodayAllOpen] = useState(false);
  const [tab, setTab] = useState<OpportunityTab>("todas");
  const [coverageOpen, setCoverageOpen] = useState(false);
  const [ignoredOpen, setIgnoredOpen] = useState(false);
  const [ignoredDraft, setIgnoredDraft] = useState("");
  const [ignoredBusy, setIgnoredBusy] = useState(false);
  const selected = profiles.find((item) => item.profile.id === selectedId) ?? null;

  const refreshClosingTodayAll = useCallback(async (list: ProfileView[]) => {
    const items: ClosingTodayItem[] = [];
    await Promise.all(
      list
        .filter((item) => item.profile.enabled)
        .map(async (item) => {
          let snap: Snapshot;
          try {
            snap = await api<Snapshot>(
              `/api/v1/profiles/${encodeURIComponent(item.profile.id)}/snapshot`,
            );
          } catch {
            return;
          }
          const dismissed = new Set(
            snap.payload.user_states
              .filter((state) => state.state === "dismissed")
              .map((state) => state.opportunity_key),
          );
          const timezone = item.profile.schedule.timezone || "UTC";
          const profileMatches =
            snap.payload.profiles.find((entry) => entry.profile_id === item.profile.id)
              ?.matches ?? [];
          for (const match of profileMatches) {
            if (dismissed.has(match.opportunity_key)) continue;
            if (!closesToday(match.lot.closing_at, timezone)) continue;
            items.push({
              profileId: item.profile.id,
              profileName: item.profile.name,
              opportunityKey: match.opportunity_key,
              auctionGroupKey: match.opportunity_key.split(":").slice(0, 3).join(":"),
              title: match.lot.title,
              lotUrl: match.lot.lot_url,
              closingAt: match.lot.closing_at ?? "",
            });
          }
        }),
    );
    items.sort((a, b) => a.closingAt.localeCompare(b.closingAt));
    setClosingTodayAll(items);
  }, []);

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
      void refreshClosingTodayAll(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudieron cargar los perfiles");
    } finally {
      setLoading(false);
    }
  }, [refreshClosingTodayAll]);

  const loadData = useCallback(async (profileId: string) => {
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
      if (reason instanceof Error && reason.message.includes("snapshot")) {
        setSnapshot(null);
      } else if (reason instanceof Error) {
        setError(reason.message);
      }
    }
  }, []);

  useEffect(() => {
    void loadProfiles();
  }, [loadProfiles]);

  useEffect(() => {
    if (selectedId && !creating) {
      void loadData(selectedId);
    }
    setTab("todas");
  }, [creating, loadData, selectedId]);

  useEffect(() => {
    if (selectedId && !creating) {
      const profile = copy(
        profiles.find((item) => item.profile.id === selectedId)?.profile ?? emptyProfile(),
      );
      setDraft(profile);
      setTermInputs(termInputsFromProfile(profile));
      setGuidanceWarnings([]);
    }
  }, [creating, profiles, selectedId]);

  const updateDraft = <K extends keyof Profile>(key: K, value: Profile[K]) => {
    setGuidanceWarnings([]);
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const updateTermInput = (key: TermField, value: string) => {
    const editing = editTermInput(value);
    setGuidanceWarnings([]);
    setTermInputs((current) => ({ ...current, [key]: editing.text }));
    setDraft((current) =>
      key === "schedule_times"
        ? { ...current, schedule: { ...current.schedule, times: editing.terms } }
        : { ...current, [key]: editing.terms },
    );
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
      const detail = reason instanceof Error ? reason.message : "";
      setError(
        detail.includes("lowercase slug")
          ? "El identificador sólo admite letras, números y guiones. Escribí un nombre y se arma solo."
          : detail || "No se pudo guardar el perfil",
      );
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
      // Some sources (Castells in particular) can regularly take over a
      // minute; keep polling close to the backend's own run lease (5 min)
      // instead of giving up early and leaving the button stuck mid-run.
      const deadline = Date.now() + 280000;
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
      void refreshClosingTodayAll(profiles);
    } catch (reason) {
      setMessage(null);
      setError(
        reason instanceof Error && reason.message === "timeout"
          ? "La corrida sigue en curso del lado del servidor; se va a reflejar sola cuando termine."
          : reason instanceof Error
            ? reason.message
            : "La corrida falló",
      );
      // Unstick the button either way: a stale "queued"/"running" run here
      // would otherwise disable "Actualizar ahora" forever.
      setRun(null);
      await loadData(selected.profile.id);
      void refreshClosingTodayAll(profiles);
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

  async function deleteProfile() {
    if (!selected || selected.protected) return;
    if (!window.confirm(`¿Borrar la búsqueda "${selected.profile.name}"? Esta acción no se puede deshacer.`)) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api(
        `/api/v1/profiles/${encodeURIComponent(selected.profile.id)}?expected_revision=${selected.revision}`,
        { method: "DELETE" },
      );
      const remaining = profiles.filter((item) => item.profile.id !== selected.profile.id);
      setProfiles(remaining);
      setSelectedId(remaining[0]?.profile.id ?? null);
      setSnapshot(null);
      setHistory([]);
      setNotifications([]);
      setMessage("Búsqueda borrada.");
      void refreshClosingTodayAll(remaining);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo borrar la búsqueda");
    } finally {
      setBusy(false);
    }
  }

  async function setState(key: string, state: "follow" | "discard" | "restore") {
    if (!selected || !snapshot) return;
    const previous = snapshot;
    const existing = snapshot.payload.user_states.find((item) => item.opportunity_key === key);
    const decided: OpportunityState =
      state === "follow" ? "following" : state === "discard" ? "dismissed" : "none";
    // The server bumps the version the same way, so a second action on this
    // card still sends the version the server actually holds.
    setSnapshot({
      ...snapshot,
      payload: {
        ...snapshot.payload,
        user_states: [
          ...snapshot.payload.user_states.filter((item) => item.opportunity_key !== key),
          { opportunity_key: key, state: decided, version: (existing?.version ?? 0) + 1 },
        ],
      },
    });
    try {
      await api(`/api/v1/profiles/${encodeURIComponent(selected.profile.id)}/opportunities/state`, {
        method: "POST",
        body: JSON.stringify({
          opportunity_key: key,
          state,
          expected_version: existing?.version,
        }),
      });
      void refreshClosingTodayAll(profiles);
    } catch (reason) {
      setSnapshot(previous);
      setError(reason instanceof Error ? reason.message : "No se pudo actualizar la oportunidad");
    }
  }

  async function openIgnored() {
    setIgnoredOpen(true);
    try {
      const current = await api<{ patterns: string[] }>("/api/v1/ignored-auctions");
      setIgnoredDraft(current.patterns.join("\n"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo cargar la lista");
    }
  }

  async function saveIgnored() {
    setIgnoredBusy(true);
    try {
      const saved = await api<{ patterns: string[] }>("/api/v1/ignored-auctions", {
        method: "PUT",
        body: JSON.stringify({ patterns: ignoredDraft.split("\n") }),
      });
      setIgnoredDraft(saved.patterns.join("\n"));
      setIgnoredOpen(false);
      setMessage(
        saved.patterns.length === 1
          ? "1 remate ignorado a partir de la próxima corrida."
          : `${saved.patterns.length} remates ignorados a partir de la próxima corrida.`,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo guardar la lista");
    } finally {
      setIgnoredBusy(false);
    }
  }

  async function markReviewed() {
    if (!selected) return;
    try {
      const result = await api<{ profile_id: string; reviewed_at: string }>(
        `/api/v1/profiles/${encodeURIComponent(selected.profile.id)}/reviewed`,
        { method: "POST" },
      );
      setProfiles((items) =>
        items.map((item) =>
          item.profile.id === result.profile_id
            ? { ...item, reviewed_at: result.reviewed_at }
            : item,
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudieron marcar como vistas");
    }
  }

  const matches = useMemo(
    () =>
      snapshot?.payload.profiles.find((item) => item.profile_id === selectedId)?.matches ?? [],
    [selectedId, snapshot],
  );
  const stateByKey = useMemo(() => {
    const decisions = new Map<string, OpportunityState>();
    for (const item of snapshot?.payload.user_states ?? []) {
      if (item.opportunity_key) decisions.set(item.opportunity_key, item.state);
    }
    return decisions;
  }, [snapshot?.payload.user_states]);
  const buckets = useMemo(() => {
    // A search never acknowledged has no anchor, so everything it found is new.
    const reviewedAt = selected?.reviewed_at ? Date.parse(selected.reviewed_at) : null;
    const isNew = (match: Match) =>
      match.first_match_at
        ? reviewedAt === null || Date.parse(match.first_match_at) > reviewedAt
        : false;
    const sorted = [...matches].sort(closingSoonest);
    const active = sorted.filter((match) => stateByKey.get(match.opportunity_key) !== "dismissed");
    return {
      todas: active,
      nuevas: active.filter(isNew),
      siguiendo: active.filter(
        (match) => stateByKey.get(match.opportunity_key) === "following",
      ),
      descartadas: sorted.filter(
        (match) => stateByKey.get(match.opportunity_key) === "dismissed",
      ),
      isNew,
    };
  }, [matches, selected?.reviewed_at, stateByKey]);
  const todayClosingMatches = useMemo(
    () =>
      buckets.todas.filter((match) =>
        closesToday(match.lot.closing_at, selected?.profile.schedule.timezone ?? "UTC"),
      ),
    [buckets.todas, selected?.profile.schedule.timezone],
  );
  const todayClosingAuctionCount = useMemo(
    () => new Set(todayClosingMatches.map((match) => match.lot.auction_id)).size,
    [todayClosingMatches],
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
  const diagnosticSources =
    snapshot?.payload.sources.filter((source) => (source.diagnostics?.length ?? 0) > 0) ?? [];
  const coverageNotes = [
    degradedSources.length > 0 &&
      degradedSources.map((source) => sourceNames[source.source_id] ?? source.source_id).join(", "),
    skippedSources.length > 0 &&
      `${skippedSources.reduce(
        (total, source) => total + (source.skipped_groups?.length ?? 0),
        0,
      )} remates omitidos`,
    diagnosticSources.length > 0 &&
      `${diagnosticSources.reduce(
        (total, source) =>
          total +
          (source.diagnostics?.filter((item) => item.status === "shadow_only").length ?? 0),
        0,
      )} en sombra`,
  ].filter((note): note is string => typeof note === "string" && note.length > 0);
  const lastRun = history[0] ?? null;
  const lastRunLabel = lastRun
    ? {
        completed: "completa",
        partial: "parcial",
        failed: "falló",
        queued: "en cola",
        running: "en curso",
      }[lastRun.status]
    : null;
  const pendingNotifications = notifications.filter(
    (item) => item.status === "pending" || item.status === "sending",
  ).length;

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
            const profile = emptyProfile();
            setDraft(profile);
            setTermInputs(termInputsFromProfile(profile));
            setSnapshot(null);
            setGuidanceWarnings([]);
          }}
        >
          + Nueva búsqueda
        </button>
        <p className="eyebrow">PERFILES</p>
        <div className="sidebar-profiles">
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
        </div>
      </aside>
      <main className="content">
        {closingTodayAll.length > 0 && (
          <div className="global-closing-today">
            <StatusIcon path={ICON_CLOCK} tone="amber" />
            <span>
              {(() => {
                const auctionCount = new Set(
                  closingTodayAll.map((item) => item.auctionGroupKey),
                ).size;
                return auctionCount === 1
                  ? "1 subasta cierra hoy en tus búsquedas"
                  : `${auctionCount} subastas cierran hoy en tus búsquedas`;
              })()}
            </span>
            <button
              className="global-closing-today-toggle"
              onClick={() => setClosingTodayAllOpen((open) => !open)}
            >
              {closingTodayAllOpen ? "Ocultar" : "Ver"}
            </button>
            {closingTodayAllOpen && (
              <ul className="global-closing-today-list">
                {closingTodayAll.map((item) => (
                  <li key={item.opportunityKey}>
                    <span className="global-closing-today-profile">{item.profileName}</span>
                    <a href={item.lotUrl} rel="noreferrer" target="_blank">
                      {item.title}
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
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
            <button className="button secondary" onClick={() => void openIgnored()}>
              Remates ignorados
            </button>
            {selected && (
              <>
                <span className={`status-pill ${selected.profile.enabled ? "on" : "off"}`}>
                  {selected.profile.enabled ? "Activo" : "Pausado"}
                </span>
                <button className="button secondary" disabled={busy} onClick={() => void toggleProfile()}>
                  {selected.profile.enabled ? "Pausar" : "Reanudar"}
                </button>
                {!selected.protected && (
                  <button className="button danger-ghost" disabled={busy} onClick={() => void deleteProfile()}>
                    Borrar búsqueda
                  </button>
                )}
              </>
            )}
          </div>
        </header>
        {error && <div className="notice error">{error}</div>}
        {message && <div className="notice success">{message}</div>}
        {selected && !creating && (
          <div className="status-strip">
            <div className="status-strip-item">
              <StatusIcon path={ICON_CLOCK} tone="teal" />
              <div className="status-strip-text">
                <div className="status-strip-label">Última corrida</div>
                <div className="status-strip-value">
                  {lastRun
                    ? `${lastRunLabel}${
                        lastRun.finished_at
                          ? ` · ${new Date(lastRun.finished_at).toLocaleString()}`
                          : ""
                      }`
                    : "Sin corridas todavía"}
                </div>
              </div>
            </div>
            <div className="status-strip-item">
              <StatusIcon path={ICON_CALENDAR} tone="amber" />
              <div className="status-strip-text">
                <div className="status-strip-label">Próxima corrida</div>
                <div className="status-strip-value">
                  {selected.profile.schedule.enabled && selected.profile.schedule.times.length > 0
                    ? `${selected.profile.schedule.times.join(", ")} · ${selected.profile.schedule.timezone}`
                    : "Automatización desactivada"}
                </div>
              </div>
            </div>
            <div className="status-strip-item">
              <StatusIcon path={ICON_BELL} tone="green" />
              <div className="status-strip-text">
                <div className="status-strip-label">Notificaciones</div>
                <div className="status-strip-value">
                  {notifications.length === 0
                    ? "Sin notificaciones"
                    : pendingNotifications > 0
                      ? `${pendingNotifications} pendientes`
                      : `${notifications.length} entregadas`}
                </div>
              </div>
            </div>
          </div>
        )}
        {creating || selected ? (
          <div className="workspace">
            <Editor
              busy={busy}
              creating={creating}
              onChange={updateDraft}
              onTermInputChange={updateTermInput}
              onSubmit={(profile, bypassWarnings) =>
                void saveProfile(profile, bypassWarnings)
              }
              profile={draft}
              selected={selected}
              termInputs={termInputs}
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
              {todayClosingAuctionCount > 0 && (
                <div className="coverage-warning urgent">
                  <strong>
                    {todayClosingAuctionCount === 1
                      ? "1 subasta de esta búsqueda cierra hoy"
                      : `${todayClosingAuctionCount} subastas de esta búsqueda cierran hoy`}
                  </strong>
                  <span>
                    {todayClosingMatches.length === 1
                      ? "1 lote encontrado con cierre previsto para hoy."
                      : `${todayClosingMatches.length} lotes encontrados con cierre previsto para hoy.`}
                  </span>
                </div>
              )}
              {snapshot && coverageNotes.length > 0 && (
                <div className={`coverage-note${authoritative ? "" : " degraded"}`}>
                  <button
                    className="coverage-note-head"
                    onClick={() => setCoverageOpen((open) => !open)}
                  >
                    <span className="coverage-note-summary">
                      {authoritative ? "Cobertura completa" : "Cobertura parcial"}
                      {coverageNotes.length > 0 && ` · ${coverageNotes.join(" · ")}`}
                    </span>
                    <span className="coverage-note-toggle">
                      {coverageOpen ? "Ocultar" : "Ver detalle"}
                    </span>
                  </button>
                  {coverageOpen && (
                    <div className="coverage-note-body">
                      {!authoritative && (
                        <>
                          <strong>Cobertura parcial</strong>
                          <span>No se interpreta como “sin resultados”.</span>
                          {degradedSources.map((source) => (
                            <small key={source.source_id}>
                              {sourceNames[source.source_id] ?? source.source_id}: {source.status}
                              {source.errors.length > 0 ? ` — ${source.errors.join("; ")}` : ""}
                            </small>
                          ))}
                        </>
                      )}
                      {skippedSources.length > 0 && (
                        <>
                          <strong>Remates descartados por título</strong>
                          <span>
                            Se omitieron únicamente grupos inequívocamente artísticos antes de
                            consultar sus lotes.
                          </span>
                          {skippedSources.map((source) => (
                            <small key={source.source_id}>
                              {sourceNames[source.source_id] ?? source.source_id}:{" "}
                              {source.skipped_groups?.length ?? 0}
                            </small>
                          ))}
                        </>
                      )}
                      {diagnosticSources.length > 0 && (
                        <>
                          <strong>Decodificación adaptativa</strong>
                          <span>
                            Sólo los envelopes inequívocos se publican; los demás quedan en sombra.
                          </span>
                          {diagnosticSources.map((source) => {
                            const recovered =
                              source.diagnostics?.filter(
                                (item) => item.status === "adaptive_recovered",
                              ).length ?? 0;
                            const shadow =
                              source.diagnostics?.filter((item) => item.status === "shadow_only")
                                .length ?? 0;
                            return (
                              <small key={source.source_id}>
                                {sourceNames[source.source_id] ?? source.source_id}: {recovered}{" "}
                                recuperados, {shadow} en sombra
                              </small>
                            );
                          })}
                        </>
                      )}
                    </div>
                  )}
                </div>
              )}
              {snapshot && (
                <div className="opportunity-tabs">
                  {(
                    [
                      ["todas", "Todas"],
                      ["nuevas", "Nuevas"],
                      ["siguiendo", "Siguiendo"],
                      ["descartadas", "Descartadas"],
                    ] as Array<[OpportunityTab, string]>
                  ).map(([id, label]) => (
                    <button
                      className={`opportunity-tab${tab === id ? " selected" : ""}${
                        id === "nuevas" && buckets.nuevas.length > 0 ? " accent" : ""
                      }`}
                      key={id}
                      onClick={() => setTab(id)}
                    >
                      {label} <span className="opportunity-tab-count">{buckets[id].length}</span>
                    </button>
                  ))}
                  {buckets.nuevas.length > 0 && (
                    <button className="mark-reviewed" onClick={() => void markReviewed()}>
                      Marcar como vistas
                    </button>
                  )}
                </div>
              )}
              {!snapshot && (
                <div className="empty-state">
                  <span>◌</span>
                  <strong>Todavía no hay un snapshot.</strong>
                  <p>Actualizá para consultar las fuentes seleccionadas.</p>
                </div>
              )}
              {snapshot && buckets[tab].length === 0 && (
                <div className="empty-state">
                  <span>○</span>
                  <strong>
                    {tab === "nuevas"
                      ? "Sin novedades desde la última vez que miraste."
                      : tab === "siguiendo"
                        ? "No estás siguiendo ninguna."
                        : tab === "descartadas"
                          ? "No descartaste ninguna."
                          : matches.length === 0
                            ? "Sin oportunidades por ahora."
                            : "Descartaste todas las oportunidades."}
                  </strong>
                  <p>
                    {tab === "todas" && matches.length === 0 && authoritative
                      ? "La cobertura fue autoritativa en la última corrida."
                      : tab === "todas" && matches.length > 0
                        ? "Están en la solapa «Descartadas»."
                        : ""}
                  </p>
                </div>
              )}
              <div className="opportunity-list">
                {buckets[tab].map((match) => (
                  <Opportunity
                    isNew={buckets.isNew(match)}
                    key={match.opportunity_key}
                    match={match}
                    onState={(key, state) => void setState(key, state)}
                    state={stateByKey.get(match.opportunity_key) ?? "none"}
                    timezone={selected?.profile.schedule.timezone ?? "UTC"}
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
                const profile = emptyProfile();
                setDraft(profile);
                setTermInputs(termInputsFromProfile(profile));
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
      {ignoredOpen && (
        <div className="dialog-backdrop" onMouseDown={() => setIgnoredOpen(false)} role="presentation">
          <div
            className="search-guide-dialog ignored-dialog"
            onMouseDown={(event) => event.stopPropagation()}
            role="dialog"
          >
            <header className="guide-header">
              <div>
                <p className="eyebrow">FUERA DE TODA BÚSQUEDA</p>
                <h2>Remates ignorados</h2>
              </div>
              <button
                aria-label="Cerrar"
                className="dialog-close"
                onClick={() => setIgnoredOpen(false)}
              >
                ×
              </button>
            </header>
            <div className="guide-content">
              <p>
                Un remate cuyo nombre contenga alguno de estos textos no se consulta: no se
                bajan sus lotes ni aparecen en ninguna búsqueda. Sirve para los que no se
                delatan por el rubro, como una colección o el nombre de un artista.
              </p>
              <label>
                Un texto por línea
                <textarea
                  onChange={(event) => setIgnoredDraft(event.target.value)}
                  placeholder={"torres garcia\njulio zelman"}
                  value={ignoredDraft}
                />
              </label>
              <div className="ignored-actions">
                <button
                  className="button primary"
                  disabled={ignoredBusy}
                  onClick={() => void saveIgnored()}
                >
                  Guardar
                </button>
                <span className="muted">
                  Se aplica en la próxima corrida.
                </span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
