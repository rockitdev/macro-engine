/**
 * AmountSheet — change how much of one logged entry was eaten.
 *
 * What you edit depends on how the entry was logged:
 *   - "2 x 1 large"  → the count of that portion
 *   - "150 g"        → grams
 *   - an estimate with no weight → a multiplier on what's logged now
 * The server scales the entry's own stored macros, so this never re-resolves
 * the food.
 */

import { useState } from "react"

import { formatGrams, formatKcal } from "@/lib/format"
import type { Entry } from "@/types"

export type AmountChange = { grams: number } | { scale: number }

type Mode =
  | { kind: "count"; start: number; label: string; step: number }
  | { kind: "grams"; start: number; step: number }
  | { kind: "scale"; start: number; step: number }

function modeFor(entry: Entry): Mode {
  const count = entry.food_id != null ? /^(\d+(?:\.\d+)?) x (.+)$/.exec(entry.qty_desc ?? "") : null
  if (count && Number(count[1]) > 0) {
    return { kind: "count", start: Number(count[1]), label: count[2], step: 1 }
  }
  if (entry.grams != null && entry.grams > 0) {
    return { kind: "grams", start: Math.round(entry.grams * 10) / 10, step: 10 }
  }
  return { kind: "scale", start: 1, step: 0.5 }
}

const stepButton = {
  all: "unset",
  boxSizing: "border-box",
  width: 44,
  height: 44,
  borderRadius: 10,
  border: "1px solid var(--me-line-strong)",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  fontSize: 22,
  cursor: "pointer",
  flexShrink: 0,
} as const

export function AmountSheet({
  entry,
  busy,
  onSave,
  onCancel,
}: {
  entry: Entry
  busy: boolean
  onSave: (change: AmountChange) => void
  onCancel: () => void
}) {
  const mode = modeFor(entry)
  const [text, setText] = useState(String(mode.start))

  const value = parseFloat(text)
  const valid = Number.isFinite(value) && value > 0
  const factor = !valid ? 0 : mode.kind === "scale" ? value : value / mode.start
  const changed = valid && Math.abs(factor - 1) > 1e-9

  function bump(direction: 1 | -1) {
    const from = valid ? value : mode.start
    // Never step to zero: the smallest step down lands on half a step.
    const next = Math.max(mode.step / 2, from + direction * mode.step)
    setText(String(Math.round(next * 100) / 100))
  }

  function save() {
    if (!valid) return
    if (!changed) { onCancel(); return }
    onSave(mode.kind === "grams" ? { grams: value } : { scale: factor })
  }

  const label =
    mode.kind === "count" ? `How many (${mode.label})`
    : mode.kind === "grams" ? "Amount (grams)"
    : "Servings (1 = what's logged now)"

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); save() }}
      style={{ display: "flex", flexDirection: "column", gap: 16 }}
    >
      <p style={{ margin: 0, fontSize: 16, fontWeight: 600, lineHeight: 1.3 }}>{entry.name}</p>

      <div>
        <label htmlFor="amount-value" style={{ display: "block", fontSize: 13, color: "var(--me-muted)", marginBottom: 6 }}>
          {label}
        </label>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button type="button" aria-label="Less" style={stepButton} onClick={() => bump(-1)}>−</button>
          <input
            id="amount-value"
            type="number"
            inputMode="decimal"
            min="0"
            step="any"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onFocus={(e) => e.currentTarget.select()}
            className="num"
            style={{
              boxSizing: "border-box", flex: 1, minWidth: 0, height: 44, textAlign: "center",
              border: "1px solid var(--me-line-strong)", borderRadius: 10, padding: "0 12px",
              font: "inherit", fontSize: 18, fontWeight: 500, outline: "none",
              background: "var(--me-surface)", color: "inherit",
            }}
          />
          <button type="button" aria-label="More" style={stepButton} onClick={() => bump(1)}>+</button>
        </div>
      </div>

      {/* Live preview of what the entry becomes */}
      <div
        className="num"
        style={{
          display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, textAlign: "center",
          padding: "12px 14px", borderRadius: 10, background: "var(--me-bg)",
          opacity: valid ? 1 : 0.4,
        }}
      >
        {[
          { name: "Calories", shown: formatKcal(entry.kcal * factor) },
          { name: "Protein", shown: `${formatGrams(entry.protein_g * factor)} g` },
          { name: "Carbs", shown: `${formatGrams(entry.carb_g * factor)} g` },
          { name: "Fat", shown: `${formatGrams(entry.fat_g * factor)} g` },
        ].map(({ name, shown }) => (
          <div key={name}>
            <div style={{ fontSize: 12, color: "var(--me-muted)", marginBottom: 2 }}>{name}</div>
            <div style={{ fontSize: 16, fontWeight: 600 }}>{shown}</div>
          </div>
        ))}
      </div>

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
          type="submit"
          disabled={busy || !valid}
          style={{
            font: "inherit", fontSize: 14, fontWeight: 500, height: 44, padding: "0 20px",
            borderRadius: 10, border: "none", background: "var(--me-green)", color: "var(--me-on-green)",
            cursor: busy || !valid ? "not-allowed" : "pointer",
            opacity: busy || !valid ? 0.7 : 1, flex: 1,
          }}
        >
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  )
}
