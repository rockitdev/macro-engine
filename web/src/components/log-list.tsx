import { useState } from "react"
import { AmountSheet, type AmountChange } from "@/components/amount-sheet"
import { BottomSheet, BottomSheetContent, BottomSheetTitle } from "@/components/ui/bottom-sheet"
import { formatGrams, formatKcal } from "@/lib/format"
import { groupByMeal, MEALS, type Meal } from "@/lib/meals"
import type { Entry } from "@/types"

function DotsIcon() {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
      <circle cx="12" cy="5" r="1.6"/>
      <circle cx="12" cy="12" r="1.6"/>
      <circle cx="12" cy="19" r="1.6"/>
    </svg>
  )
}

function Tick() {
  return (
    <svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <path d="m5 12.5 4.5 4.5L19 7.5"/>
    </svg>
  )
}

export function LogList({
  entries,
  pendingId,
  onRemove,
  onMove,
  onDuplicate,
  onAmount,
  onKitchen,
  onBulkMove,
  onBulkDuplicate,
  onBulkRemove,
  onSaveRecipe,
  onCopyDay,
  onSelectStart,
}: {
  entries: Entry[]
  pendingId: number | null
  onRemove: (id: number) => void
  onMove: (id: number, meal: Meal | null) => void
  onDuplicate: (id: number) => void
  onAmount: (id: number, change: AmountChange) => Promise<boolean>
  onKitchen: (entry: Entry, inKitchen: boolean) => void
  /** Bulk actions resolve true when they worked, which ends the selection. */
  onBulkMove: (ids: number[], meal: Meal) => Promise<boolean>
  onBulkDuplicate: (ids: number[]) => Promise<boolean>
  onBulkRemove: (ids: number[]) => Promise<boolean>
  onSaveRecipe: (entries: Entry[]) => void
  onCopyDay: () => void
  /** Selection is starting: the bar is about to take the bottom of the screen. */
  onSelectStart: () => void
}) {
  const groups = groupByMeal(entries)
  const [openMenu, setOpenMenu] = useState<number | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const editing = entries.find((e) => e.log_id === editingId) ?? null

  // Selecting several entries at once.
  const [selecting, setSelecting] = useState(false)
  const [pickedIds, setPickedIds] = useState<Set<number>>(new Set())
  const [moving, setMoving] = useState(false)
  const [working, setWorking] = useState(false)
  const picked = entries.filter((e) => pickedIds.has(e.log_id))
  const pickedKcal = picked.reduce((sum, e) => sum + e.kcal, 0)

  function stopSelecting() {
    setSelecting(false)
    setMoving(false)
    setPickedIds(new Set())
  }

  function toggle(ids: number[]) {
    setPickedIds((current) => {
      const next = new Set(current)
      const allOn = ids.every((id) => next.has(id))
      for (const id of ids) {
        if (allOn) next.delete(id)
        else next.add(id)
      }
      return next
    })
  }

  async function run(action: (ids: number[]) => Promise<boolean>) {
    setWorking(true)
    try {
      if (await action(picked.map((e) => e.log_id))) stopSelecting()
    } finally {
      setWorking(false)
    }
  }

  const header = (
    <div style={{ padding: "12px 20px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, minWidth: 0 }}>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Eaten</h2>
        <span className="num" style={{ fontSize: 13, color: "var(--me-muted)" }}>
          {entries.length} {entries.length === 1 ? "item" : "items"}
        </span>
      </div>
      <div style={{ display: "flex", gap: 20 }}>
        {selecting ? (
          <button type="button" className="text-btn" onClick={stopSelecting}>Done</button>
        ) : (
          <>
            <button type="button" className="text-btn" onClick={onCopyDay}>Copy a day</button>
            {entries.length > 0 && (
              <button type="button" className="text-btn" onClick={() => { setOpenMenu(null); setSelecting(true); onSelectStart() }}>Select</button>
            )}
          </>
        )}
      </div>
    </div>
  )

  if (entries.length === 0) {
    return (
      <section className="me-card" style={{ padding: "8px 0" }}>
        {header}
        <p style={{ margin: 0, padding: "4px 20px 16px", fontSize: 14, color: "var(--me-muted)", lineHeight: 1.45 }}>
          Nothing logged yet. Eating the same as another day? Copy it over and adjust from there.
        </p>
      </section>
    )
  }

  return (
    // While selecting, leave room to scroll the last entries clear of the bar.
    <section className="me-card" style={{ padding: "8px 0", marginBottom: selecting ? 104 : 0 }}>
      {header}

      {groups.map((group) => {
        const kcal = group.entries.reduce((sum, e) => sum + e.kcal, 0)
        const groupIds = group.entries.map((e) => e.log_id)
        const groupOn = groupIds.every((id) => pickedIds.has(id))
        const sectionStyle = {
          all: "unset", boxSizing: "border-box", width: "100%",
          padding: "8px 20px",
          background: "var(--me-bg)",
          borderTop: "1px solid var(--me-mist)",
          borderBottom: "1px solid var(--me-mist)",
          display: "flex",
          alignItems: "center",
          gap: 12,
          fontSize: 12,
          fontWeight: 500,
          textTransform: "uppercase",
          letterSpacing: ".05em",
          color: "var(--me-muted)",
        } as const
        const sectionBody = (
          <>
            {selecting && <span className="pick-box" data-on={groupOn}><Tick /></span>}
            <span style={{ flex: 1 }}>{group.label}</span>
            <span className="num">{formatKcal(kcal)} kcal</span>
          </>
        )
        return (
          <div key={group.id}>
            {/* Section header; while selecting, tapping it takes the whole meal */}
            {selecting ? (
              <button
                type="button"
                role="checkbox"
                aria-checked={groupOn}
                aria-label={`All of ${group.label}`}
                style={{ ...sectionStyle, cursor: "pointer" }}
                onClick={() => toggle(groupIds)}
              >
                {sectionBody}
              </button>
            ) : (
              <div style={sectionStyle}>{sectionBody}</div>
            )}

            {/* Entries */}
            {group.entries.map((entry, idx) => {
              const isLast = idx === group.entries.length - 1
              const isPending = pendingId === entry.log_id
              const menuOpen = openMenu === entry.log_id
              const on = pickedIds.has(entry.log_id)
              const rowStyle = {
                boxSizing: "border-box", width: "100%",
                padding: "12px 20px",
                display: "flex",
                gap: 12,
                alignItems: "center",
                borderBottom: isLast ? "none" : "1px solid var(--me-mist)",
                opacity: isPending ? 0.5 : 1,
                position: "relative",
              } as const
              const info = (
                <>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 500, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                      {entry.name}
                      {entry.estimated && (
                        <span style={{ fontSize: 11, fontWeight: 500, color: "var(--me-grain-ink)", background: "var(--me-grain-soft)", padding: "1px 7px", borderRadius: 99 }}>
                          Estimate
                        </span>
                      )}
                    </div>
                    <div className="num" style={{ fontSize: 12, color: "var(--me-muted)", marginTop: 2 }}>
                      {entry.qty_desc && !entry.estimated && <>{entry.qty_desc} · </>}
                      P {formatGrams(entry.protein_g)} · C {formatGrams(entry.carb_g)} · F {formatGrams(entry.fat_g)}
                    </div>
                  </div>
                  <div className="num" style={{ fontSize: 14, fontWeight: 500 }}>{formatKcal(entry.kcal)}</div>
                </>
              )

              if (selecting) {
                return (
                  <button
                    key={entry.log_id}
                    type="button"
                    role="checkbox"
                    aria-checked={on}
                    style={{ all: "unset", ...rowStyle, cursor: "pointer", background: on ? "var(--me-green-faint)" : "transparent" }}
                    onClick={() => toggle([entry.log_id])}
                  >
                    <span className="pick-box" data-on={on}><Tick /></span>
                    {info}
                  </button>
                )
              }

              return (
                <div key={entry.log_id} style={rowStyle}>
                  {info}

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
                        cursor: "pointer", color: "var(--me-muted)",
                      }}
                    >
                      <DotsIcon />
                    </button>
                    {menuOpen && (
                      <div
                        style={{
                          position: "absolute", right: 0, top: "calc(100% + 4px)",
                          background: "var(--me-surface)", border: "1px solid var(--me-line)",
                          borderRadius: 10, padding: 4, zIndex: 10,
                          boxShadow: "0 4px 12px rgba(0,0,0,.1)",
                          minWidth: 176,
                        }}
                      >
                        <button type="button" className="menu-item" onClick={() => { setEditingId(entry.log_id); setOpenMenu(null) }}>
                          Change amount
                        </button>
                        <button type="button" className="menu-item" onClick={() => { onDuplicate(entry.log_id); setOpenMenu(null) }}>
                          Duplicate
                        </button>
                        {entry.food_id != null && (
                          <button type="button" className="menu-item" onClick={() => { onKitchen(entry, !entry.in_kitchen); setOpenMenu(null) }}>
                            {entry.in_kitchen ? "Remove from kitchen" : "Add to kitchen"}
                          </button>
                        )}
                        <div style={{ height: 1, background: "var(--me-mist)", margin: "4px 0" }} />
                        {MEALS.filter((m) => m.id !== entry.meal).map((m) => (
                          <button key={m.id} type="button" className="menu-item" onClick={() => { onMove(entry.log_id, m.id); setOpenMenu(null) }}>
                            Move to {m.label}
                          </button>
                        ))}
                        <div style={{ height: 1, background: "var(--me-mist)", margin: "4px 0" }} />
                        <button type="button" className="menu-item" data-tone="danger" onClick={() => { onRemove(entry.log_id); setOpenMenu(null) }}>
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

      {/* Selection bar */}
      {selecting && (
        <div className="float-bar" role="region" aria-label="Selected entries">
          <div className="select-bar-head" aria-live="polite">
            {moving ? (
              <span>Move {picked.length} to</span>
            ) : picked.length === 0 ? (
              <span>Tap entries to select them</span>
            ) : (
              <>
                <span>{picked.length} selected</span>
                <span className="num">{formatKcal(pickedKcal)} kcal</span>
              </>
            )}
          </div>
          {moving ? (
            <div className="select-bar-actions">
              {MEALS.map((m) => (
                <button key={m.id} type="button" className="inv-btn" disabled={working} onClick={() => void run((ids) => onBulkMove(ids, m.id))}>
                  {m.label}
                </button>
              ))}
              <button type="button" className="inv-btn" data-tone="accent" onClick={() => setMoving(false)}>Back</button>
            </div>
          ) : (
            <div className="select-bar-actions">
              <button type="button" className="inv-btn" disabled={picked.length === 0 || working} onClick={() => setMoving(true)}>Move</button>
              <button type="button" className="inv-btn" disabled={picked.length === 0 || working} onClick={() => void run(onBulkDuplicate)}>Duplicate</button>
              <button type="button" className="inv-btn" disabled={picked.length === 0 || working} onClick={() => onSaveRecipe(picked)}>Save as recipe</button>
              <button type="button" className="inv-btn" data-tone="danger" disabled={picked.length === 0 || working} onClick={() => void run(onBulkRemove)}>Remove</button>
            </div>
          )}
        </div>
      )}

      {/* Change amount */}
      <BottomSheet open={editing !== null} onOpenChange={(open) => { if (!open) setEditingId(null) }}>
        <BottomSheetContent aria-label="Change amount" header={<BottomSheetTitle>Change amount</BottomSheetTitle>}>
          {editing && (
            <AmountSheet
              key={editing.log_id}
              entry={editing}
              busy={pendingId === editing.log_id}
              onCancel={() => setEditingId(null)}
              onSave={(change) => {
                void onAmount(editing.log_id, change).then((ok) => { if (ok) setEditingId(null) })
              }}
            />
          )}
        </BottomSheetContent>
      </BottomSheet>
    </section>
  )
}
