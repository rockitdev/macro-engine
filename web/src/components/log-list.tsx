import { useState } from "react"
import { formatGrams, formatKcal } from "@/lib/format"
import { MEALS, type Meal } from "@/lib/meals"
import type { Entry } from "@/types"

type Section = { id: string; label: string; entries: Entry[] }

function sections(entries: Entry[]): Section[] {
  const known = new Set<string>(MEALS.map((m) => m.id))
  const grouped: Section[] = MEALS.map((m) => ({
    id: m.id,
    label: m.label,
    entries: entries.filter((e) => e.meal === m.id),
  })).filter((s) => s.entries.length > 0)
  const loose = entries.filter((e) => !e.meal || !known.has(e.meal))
  if (loose.length > 0) grouped.push({ id: "misc", label: "No meal", entries: loose })
  return grouped
}

function DotsIcon() {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
      <circle cx="12" cy="5" r="1.6"/>
      <circle cx="12" cy="12" r="1.6"/>
      <circle cx="12" cy="19" r="1.6"/>
    </svg>
  )
}

export function LogList({
  entries,
  pendingId,
  onRemove,
  onMove,
}: {
  entries: Entry[]
  pendingId: number | null
  onRemove: (id: number) => void
  onMove: (id: number, meal: Meal | null) => void
}) {
  const groups = sections(entries)
  const [openMenu, setOpenMenu] = useState<number | null>(null)

  if (entries.length === 0) {
    return (
      <section className="me-card" style={{ padding: "8px 0" }}>
        <div style={{ padding: "12px 20px", display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Eaten</h2>
          <span className="num" style={{ fontSize: 13, color: "#5f6b65" }}>0 items</span>
        </div>
        <p style={{ padding: "4px 20px 16px", fontSize: 14, color: "#5f6b65" }}>Nothing logged yet.</p>
      </section>
    )
  }

  return (
    <section className="me-card" style={{ padding: "8px 0" }}>
      <div style={{ padding: "12px 20px", display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Eaten</h2>
        <span className="num" style={{ fontSize: 13, color: "#5f6b65" }}>
          {entries.length} {entries.length === 1 ? "item" : "items"}
        </span>
      </div>

      {groups.map((group) => {
        const kcal = group.entries.reduce((sum, e) => sum + e.kcal, 0)
        return (
          <div key={group.id}>
            {/* Section header */}
            <div style={{
              padding: "8px 20px",
              background: "#f7f8f7",
              borderTop: "1px solid #eef1ef",
              borderBottom: "1px solid #eef1ef",
              display: "flex",
              justifyContent: "space-between",
              fontSize: 12,
              fontWeight: 500,
              textTransform: "uppercase",
              letterSpacing: ".05em",
              color: "#5f6b65",
            }}>
              <span>{group.label}</span>
              <span className="num">{formatKcal(kcal)} kcal</span>
            </div>

            {/* Entries */}
            {group.entries.map((entry, idx) => {
              const isLast = idx === group.entries.length - 1
              const isPending = pendingId === entry.log_id
              const menuOpen = openMenu === entry.log_id
              return (
                <div
                  key={entry.log_id}
                  style={{
                    padding: "12px 20px",
                    display: "flex",
                    gap: 12,
                    alignItems: "center",
                    borderBottom: isLast ? "none" : "1px solid #eef1ef",
                    opacity: isPending ? 0.5 : 1,
                    position: "relative",
                  }}
                >
                  {/* Food info */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 500, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                      {entry.name}
                      {entry.estimated && (
                        <span style={{ fontSize: 11, fontWeight: 500, color: "#8a5a00", background: "#fbf1da", padding: "1px 7px", borderRadius: 99 }}>
                          Estimate
                        </span>
                      )}
                    </div>
                    <div className="num" style={{ fontSize: 12, color: "#5f6b65", marginTop: 2 }}>
                      {entry.qty_desc && <>{entry.qty_desc} · </>}
                      P {formatGrams(entry.protein_g)} · C {formatGrams(entry.carb_g)} · F {formatGrams(entry.fat_g)}
                    </div>
                  </div>

                  {/* kcal */}
                  <div className="num" style={{ fontSize: 14, fontWeight: 500 }}>{formatKcal(entry.kcal)}</div>

                  {/* Actions menu */}
                  <div style={{ position: "relative" }}>
                    <button
                      type="button"
                      aria-label={`Actions for ${entry.name}`}
                      aria-expanded={menuOpen}
                      disabled={isPending}
                      onClick={() => setOpenMenu(menuOpen ? null : entry.log_id)}
                      style={{
                        all: "unset", boxSizing: "border-box",
                        width: 32, height: 32, borderRadius: 8,
                        display: "inline-flex", alignItems: "center", justifyContent: "center",
                        cursor: "pointer", color: "#5f6b65",
                      }}
                    >
                      <DotsIcon />
                    </button>
                    {menuOpen && (
                      <div
                        style={{
                          position: "absolute", right: 0, top: "calc(100% + 4px)",
                          background: "#fff", border: "1px solid #e3e7e4",
                          borderRadius: 10, padding: 4, zIndex: 10,
                          boxShadow: "0 4px 12px rgba(0,0,0,.1)",
                          minWidth: 160,
                        }}
                      >
                        {/* Move to meal */}
                        {MEALS.filter((m) => m.id !== entry.meal).map((m) => (
                          <button
                            key={m.id}
                            type="button"
                            onClick={() => { onMove(entry.log_id, m.id); setOpenMenu(null) }}
                            style={{
                              all: "unset", boxSizing: "border-box", width: "100%",
                              padding: "7px 12px", borderRadius: 7, fontSize: 14, cursor: "pointer",
                              display: "block",
                            }}
                            onMouseOver={(e) => (e.currentTarget.style.background = "#f7f8f7")}
                            onMouseOut={(e) => (e.currentTarget.style.background = "transparent")}
                          >
                            Move to {m.label}
                          </button>
                        ))}
                        <div style={{ height: 1, background: "#eef1ef", margin: "4px 0" }} />
                        <button
                          type="button"
                          onClick={() => { onRemove(entry.log_id); setOpenMenu(null) }}
                          style={{
                            all: "unset", boxSizing: "border-box", width: "100%",
                            padding: "7px 12px", borderRadius: 7, fontSize: 14, cursor: "pointer",
                            display: "block", color: "#c2512e",
                          }}
                          onMouseOver={(e) => (e.currentTarget.style.background = "#fbeee9")}
                          onMouseOut={(e) => (e.currentTarget.style.background = "transparent")}
                        >
                          Remove
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )
      })}

      {/* Click-away */}
      {openMenu !== null && (
        <div
          style={{ position: "fixed", inset: 0, zIndex: 9 }}
          onClick={() => setOpenMenu(null)}
          aria-hidden="true"
        />
      )}
    </section>
  )
}
