/**
 * CopyDaySheet — bring another day's meals onto the day being viewed.
 * Opens on the last day you logged anything; step to another day, tick the
 * meals you want, copy. Each entry
 * keeps its meal, amount and macros; adjust afterwards from the log.
 */

import { useEffect, useState } from "react"

import { ApiError, copyDay, getDay, getLastLoggedDay } from "@/api"
import { formatKcal, formatLong, isoToday, shiftDate } from "@/lib/format"
import { groupByMeal } from "@/lib/meals"
import type { Day, Entry } from "@/types"

function Chevron({ direction }: { direction: "left" | "right" }) {
  return (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d={direction === "left" ? "m15 18-6-6 6-6" : "m9 18 6-6-6-6"}/>
    </svg>
  )
}

function dayName(iso: string, target: string) {
  const today = isoToday()
  if (iso === shiftDate(target, -1)) return target === today ? "Yesterday" : "The day before"
  if (iso === today) return "Today"
  return null
}

const stepButton = {
  all: "unset", boxSizing: "border-box", width: 44, height: 44, borderRadius: 10,
  display: "inline-flex", alignItems: "center", justifyContent: "center",
  cursor: "pointer", color: "var(--me-ink)", flexShrink: 0,
} as const

export function CopyDaySheet({
  date,
  onCopied,
  onCancel,
}: {
  /** The day the entries are copied onto. */
  date: string
  onCopied: (entries: Entry[], fromDate: string) => void
  onCancel: () => void
}) {
  // null until we know the last day with anything logged (yesterday if none).
  const [from, setFrom] = useState<string | null>(null)
  const [source, setSource] = useState<Day | null>(null)
  const [off, setOff] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    getLastLoggedDay(date)
      .then((last) => { if (active) setFrom((current) => current ?? last.date ?? shiftDate(date, -1)) })
      .catch(() => { if (active) setFrom((current) => current ?? shiftDate(date, -1)) })
    return () => { active = false }
  }, [date])

  useEffect(() => {
    if (!from) return
    let active = true
    getDay(from)
      .then((day) => {
        if (!active) return
        setSource(day)
        setOff(new Set())
        setError(null)
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof ApiError ? err.message : "Could not load that day")
      })
    return () => { active = false }
  }, [from])

  function step(direction: 1 | -1) {
    setFrom((current) => {
      if (!current) return current
      const next = shiftDate(current, direction)
      return next === date ? shiftDate(next, direction) : next // never the day we're copying onto
    })
  }

  const loaded = from && source && source.date === from ? source : null
  const groups = loaded ? groupByMeal(loaded.entries) : []
  const chosen = groups.filter((g) => !off.has(g.id))
  const count = chosen.reduce((sum, g) => sum + g.entries.length, 0)
  const targetLabel = date === isoToday() ? "today" : formatLong(date)
  const relative = from ? dayName(from, date) : null

  async function copy() {
    if (count === 0 || !from) return
    setBusy(true)
    setError(null)
    try {
      const all = chosen.length === groups.length
      const result = await copyDay({
        from_date: from,
        to_date: date,
        ...(all ? {} : { meals: chosen.map((g) => (g.id === "misc" ? null : g.id)) }),
      })
      onCopied(result.entries, from)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not copy that day")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Which day */}
      <div style={{ display: "flex", alignItems: "center", gap: 4, margin: "0 -10px" }}>
        <button type="button" aria-label="Earlier day" style={stepButton} onClick={() => step(-1)}>
          <Chevron direction="left" />
        </button>
        <div style={{ flex: 1, textAlign: "center", minWidth: 0 }} aria-live="polite">
          <div style={{ fontSize: 16, fontWeight: 600 }}>{from ? relative ?? formatLong(from) : "\u00a0"}</div>
          {from && relative && <div style={{ fontSize: 13, color: "var(--me-muted)", marginTop: 1 }}>{formatLong(from)}</div>}
        </div>
        <button type="button" aria-label="Later day" style={stepButton} onClick={() => step(1)}>
          <Chevron direction="right" />
        </button>
      </div>

      {/* Its meals */}
      <div style={{ minHeight: 120 }}>
        {!loaded && !error && <p style={{ margin: 0, fontSize: 14, color: "var(--me-faint)" }}>Loading…</p>}
        {loaded && groups.length === 0 && (
          <p style={{ margin: 0, fontSize: 14, color: "var(--me-muted)" }}>
            Nothing was logged that day. Try another one.
          </p>
        )}
        {groups.map((group) => {
          const on = !off.has(group.id)
          const kcal = group.entries.reduce((sum, e) => sum + e.kcal, 0)
          return (
            <button
              key={group.id}
              type="button"
              role="checkbox"
              aria-checked={on}
              onClick={() => setOff((current) => {
                const next = new Set(current)
                if (on) next.add(group.id)
                else next.delete(group.id)
                return next
              })}
              style={{
                all: "unset", boxSizing: "border-box", width: "100%", cursor: "pointer",
                display: "flex", gap: 12, alignItems: "flex-start", padding: "12px 0",
                borderBottom: "1px solid var(--me-mist)", opacity: on ? 1 : 0.55,
              }}
            >
              <span className="pick-box" data-on={on} style={{ marginTop: 1 }}>
                <svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                  <path d="m5 12.5 4.5 4.5L19 7.5"/>
                </svg>
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 14, fontWeight: 500 }}>
                  <span>{group.label}</span>
                  <span className="num">{formatKcal(kcal)} kcal</span>
                </span>
                <span style={{ display: "block", marginTop: 3, fontSize: 13, color: "var(--me-muted)", lineHeight: 1.4 }}>
                  {group.entries.map((e) => e.name).join(", ")}
                </span>
              </span>
            </button>
          )
        })}
      </div>

      {error && <p style={{ margin: 0, fontSize: 14, color: "var(--me-roast)" }} role="alert">{error}</p>}

      <div style={{ display: "flex", gap: 10 }}>
        <button
          type="button"
          onClick={onCancel}
          style={{
            font: "inherit", fontSize: 14, fontWeight: 500, height: 44, padding: "0 20px",
            borderRadius: 10, border: "1px solid var(--me-line-strong)", background: "transparent",
            color: "inherit", cursor: "pointer",
          }}
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={busy || count === 0}
          onClick={() => void copy()}
          style={{
            font: "inherit", fontSize: 14, fontWeight: 500, height: 44, padding: "0 16px",
            borderRadius: 10, border: "none", background: "var(--me-green)", color: "var(--me-on-green)",
            cursor: busy || count === 0 ? "not-allowed" : "pointer",
            opacity: busy || count === 0 ? 0.6 : 1, flex: 1, minWidth: 0,
          }}
        >
          {busy ? "Copying…" : count === 0 ? "Nothing to copy" : `Copy ${count} ${count === 1 ? "item" : "items"} to ${targetLabel}`}
        </button>
      </div>
    </div>
  )
}
