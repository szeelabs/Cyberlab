import { api } from "../api";
import { ErrorNotice, Pill, useLoad } from "../ui";

const RISK = { low: "ok", medium: "warn", high: "bad" } as const;

export function Tools() {
  const data = useLoad(() => Promise.all([api.categories(), api.toolPacks()]));
  if (data.error) return <ErrorNotice error={data.error} />;
  if (!data.data) return <div className="muted">Loading…</div>;
  const [categories, packs] = data.data;
  return (
    <>
      <h1>Tool universe</h1>
      <p className="muted">
        Tool packs you can add to an environment's <code>toolPacks</code>. Tools marked "not installable yet" are listed for
        reference but aren't installed by CyberLab today.
      </p>
      {categories.map((c) => {
        const inCat = packs.filter((p) => p.category === c.id);
        return (
          <section key={c.id} className="panel" aria-label={c.name}>
            <div className="panel-head">
              <h2>{c.name}</h2>
              <span className="muted small">{c.description}</span>
            </div>
            {inCat.length === 0 && <div className="muted small">No tool packs in this category yet.</div>}
            {inCat.map((p) => (
              <div key={p.id} className="panel" style={{ background: "var(--panel-2)" }}>
                <div className="row">
                  <h3 className="mono">{p.id}</h3>
                  <Pill kind={RISK[p.risk]}>{p.risk} risk</Pill>
                  <span className="muted small">works on {p.compatibleBases.join(", ")}</span>
                </div>
                <div className="muted">{p.description}</div>
                <ul className="findings">
                  {p.tools.map((t) => (
                    <li key={t.name}>
                      <strong>{t.homepage ? <a href={t.homepage} target="_blank" rel="noreferrer">{t.name}</a> : t.name}</strong>
                      <span className="muted">{t.purpose}</span>
                      {!t.package && <Pill kind="warn">not installable yet</Pill>}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </section>
        );
      })}
    </>
  );
}
