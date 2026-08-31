export type SearchGuideData = {
  title: string;
  intro: string;
  fields: Array<{
    id: string;
    title: string;
    description: string;
    example: string;
    warning?: string;
  }>;
  sources: Array<{ id: string; name: string; coverage: string }>;
  source_advice: string;
  no_results: string;
  statuses: Array<{ status: "complete" | "partial" | "failed"; meaning: string; action: string }>;
  recipes: Array<{
    name: string;
    keywords_any: string;
    exact_phrases?: string;
    keywords_all?: string;
    exclude: string;
  }>;
  flow: string[];
};

export function SearchGuideDialog({
  guide,
  loading,
  onClose,
}: {
  guide: SearchGuideData | null;
  loading: boolean;
  onClose: () => void;
}) {
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        aria-labelledby="search-guide-title"
        aria-modal="true"
        className="search-guide-dialog"
        onMouseDown={(event) => event.stopPropagation()}
        role="dialog"
      >
        <header className="guide-header">
          <div>
            <p className="eyebrow">AYUDA CONTEXTUAL</p>
            <h2 id="search-guide-title">{guide?.title ?? "Cómo buscar mejor"}</h2>
          </div>
          <button aria-label="Cerrar guía" className="dialog-close" onClick={onClose}>
            ×
          </button>
        </header>
        {loading && <p className="muted">Cargando la guía…</p>}
        {guide && (
          <div className="guide-content">
            <p className="guide-intro">{guide.intro}</p>
            <section>
              <h3>Armá los criterios</h3>
              <div className="guide-grid">
                {guide.fields.map((field) => (
                  <article className="guide-card" key={field.id}>
                    <h4>{field.title}</h4>
                    <p>{field.description}</p>
                    <small>Ejemplo: {field.example}</small>
                    {field.warning && <div className="guide-note">{field.warning}</div>}
                  </article>
                ))}
              </div>
            </section>
            <section>
              <h3>Fuentes y cobertura</h3>
              <p>{guide.source_advice}</p>
              <div className="guide-source-list">
                {guide.sources.map((source) => (
                  <div key={source.id}>
                    <strong>{source.name}</strong>
                    <span>{source.coverage}</span>
                  </div>
                ))}
              </div>
              <div className="guide-status-list">
                {guide.statuses.map((status) => (
                  <article className={`guide-status ${status.status}`} key={status.status}>
                    <strong>{status.status}</strong>
                    <span>{status.meaning}</span>
                    <small>{status.action}</small>
                  </article>
                ))}
              </div>
              <div className="guide-note">{guide.no_results}</div>
            </section>
            <section>
              <h3>Recetas para empezar</h3>
              <div className="guide-recipes">
                {guide.recipes.map((recipe) => (
                  <article key={recipe.name}>
                    <strong>{recipe.name}</strong>
                    <span>Cualquiera: {recipe.keywords_any}</span>
                    {recipe.keywords_all && <span>Todos: {recipe.keywords_all}</span>}
                    {recipe.exact_phrases && <span>Frases: {recipe.exact_phrases}</span>}
                    <span>Excluir: {recipe.exclude}</span>
                  </article>
                ))}
              </div>
            </section>
            <section>
              <h3>Flujo recomendado</h3>
              <ol className="guide-flow">
                {guide.flow.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
            </section>
          </div>
        )}
      </section>
    </div>
  );
}
