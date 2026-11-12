/**
 * RecipeSheet — name a set of ingredients and say how many servings it makes.
 *
 * Two starting points:
 *   - new:  the entries selected in the log become the ingredients
 *   - edit: an existing recipe; rename it, change the servings, drop
 *           ingredients, or delete it
 * Either way the recipe then logs like any food, by serving. Days it was
 * already logged on keep the macros they were logged with.
 */

import { useState } from "react"

import { ApiError, createRecipe, deleteRecipe, updateRecipe } from "@/api"
import { formatGrams, formatKcal } from "@/lib/format"
import type { Entry, Recipe } from "@/types"

export type RecipeDraft = { kind: "new"; entries: Entry[] } | { kind: "edit"; recipe: Recipe }

type Ingredient = {
  key: number
  name: string
  amount: string | null
  kcal: number
  protein_g: number
  carb_g: number
  fat_g: number
}

function ingredientsOf(draft: RecipeDraft): Ingredient[] {
  if (draft.kind === "new") {
    return draft.entries.map((e) => ({
      key: e.log_id, name: e.name, amount: e.estimated ? null : e.qty_desc,
      kcal: e.kcal, protein_g: e.protein_g, carb_g: e.carb_g, fat_g: e.fat_g,
    }))
  }
  return draft.recipe.items.map((i) => ({
    key: i.id, name: i.name, amount: i.qty_desc,
    kcal: i.kcal, protein_g: i.protein_g, carb_g: i.carb_g, fat_g: i.fat_g,
  }))
}

const stepButton = {
  all: "unset", boxSizing: "border-box", width: 44, height: 44, borderRadius: 10,
  border: "1px solid var(--me-line-strong)", display: "inline-flex",
  alignItems: "center", justifyContent: "center", fontSize: 22, cursor: "pointer", flexShrink: 0,
} as const

const fieldLabel = { display: "block", fontSize: 13, color: "var(--me-muted)", marginBottom: 6 } as const

const textInput = {
  boxSizing: "border-box", width: "100%", height: 44,
  border: "1px solid var(--me-line-strong)", borderRadius: 10, padding: "0 12px",
  font: "inherit", fontSize: 16, outline: "none",
  background: "var(--me-surface)", color: "inherit",
} as const

export function RecipeSheet({
  draft,
  onSaved,
  onDeleted,
  onCancel,
}: {
  draft: RecipeDraft
  onSaved: (recipe: Recipe, created: boolean) => void
  onDeleted: (recipe: Recipe) => void
  onCancel: () => void
}) {
  const editing = draft.kind === "edit" ? draft.recipe : null
  const all = ingredientsOf(draft)
  const [name, setName] = useState(editing?.name ?? "")
  const [servingsText, setServingsText] = useState(String(editing?.servings ?? 1))
  const [removed, setRemoved] = useState<Set<number>>(new Set())
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const servings = parseFloat(servingsText)
  const servingsOk = Number.isFinite(servings) && servings > 0
  const kept = all.filter((i) => !removed.has(i.key))
  const total = kept.reduce(
    (sum, i) => ({
      kcal: sum.kcal + i.kcal, protein_g: sum.protein_g + i.protein_g,
      carb_g: sum.carb_g + i.carb_g, fat_g: sum.fat_g + i.fat_g,
    }),
    { kcal: 0, protein_g: 0, carb_g: 0, fat_g: 0 },
  )
  const per = servingsOk ? servings : 1
  const canSave = name.trim().length > 0 && servingsOk && kept.length > 0

  function bump(direction: 1 | -1) {
    const from = servingsOk ? servings : 1
    setServingsText(String(Math.max(1, Math.round(from + direction))))
  }

  async function save() {
    if (!canSave) return
    setBusy(true)
    setError(null)
    try {
      const result = editing
        ? await updateRecipe(editing.food_id, {
            name: name.trim(),
            servings,
            ...(removed.size > 0 ? { remove_item_ids: [...removed] } : {}),
          })
        : await createRecipe({ name: name.trim(), servings, log_ids: kept.map((i) => i.key) })
      if (!result.saved) {
        setError(result.problems[0]?.reason ?? "Could not save that recipe")
        return
      }
      onSaved(result, !editing)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save that recipe")
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!editing) return
    setBusy(true)
    setError(null)
    try {
      await deleteRecipe(editing.food_id)
      onDeleted(editing)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete that recipe")
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); void save() }}
      style={{ display: "flex", flexDirection: "column", gap: 18 }}
    >
      <div>
        <label htmlFor="recipe-name" style={fieldLabel}>Name</label>
        <input
          id="recipe-name"
          value={name}
          autoComplete="off"
          placeholder="Weeknight chili"
          onChange={(e) => setName(e.target.value)}
          style={textInput}
        />
      </div>

      <div>
        <label htmlFor="recipe-servings" style={fieldLabel}>Servings the whole recipe makes</label>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button type="button" aria-label="Fewer servings" style={stepButton} onClick={() => bump(-1)}>−</button>
          <input
            id="recipe-servings"
            type="number"
            inputMode="decimal"
            min="0"
            step="any"
            value={servingsText}
            onChange={(e) => setServingsText(e.target.value)}
            onFocus={(e) => e.currentTarget.select()}
            className="num"
            style={{ ...textInput, flex: 1, minWidth: 0, textAlign: "center", fontSize: 18, fontWeight: 500 }}
          />
          <button type="button" aria-label="More servings" style={stepButton} onClick={() => bump(1)}>+</button>
        </div>
      </div>

      {/* What one serving comes to */}
      <div>
        <div style={fieldLabel}>One serving</div>
        <div
          className="num"
          style={{
            display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, textAlign: "center",
            padding: "12px 14px", borderRadius: 10, background: "var(--me-bg)",
            opacity: servingsOk && kept.length > 0 ? 1 : 0.4,
          }}
        >
          {[
            { label: "Calories", shown: formatKcal(total.kcal / per) },
            { label: "Protein", shown: `${formatGrams(total.protein_g / per)} g` },
            { label: "Carbs", shown: `${formatGrams(total.carb_g / per)} g` },
            { label: "Fat", shown: `${formatGrams(total.fat_g / per)} g` },
          ].map(({ label, shown }) => (
            <div key={label}>
              <div style={{ fontSize: 12, color: "var(--me-muted)", marginBottom: 2 }}>{label}</div>
              <div style={{ fontSize: 16, fontWeight: 600 }}>{shown}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Ingredients */}
      <div>
        <div style={{ ...fieldLabel, display: "flex", justifyContent: "space-between" }}>
          <span>{kept.length} {kept.length === 1 ? "ingredient" : "ingredients"}</span>
          <span className="num">{formatKcal(total.kcal)} kcal in all</span>
        </div>
        <ul style={{ listStyle: "none", margin: 0, padding: 0, borderTop: "1px solid var(--me-mist)" }}>
          {all.map((item) => {
            const gone = removed.has(item.key)
            return (
              <li
                key={item.key}
                style={{
                  display: "flex", alignItems: "center", gap: 10, minHeight: 48,
                  borderBottom: "1px solid var(--me-mist)",
                }}
              >
                <div style={{ flex: 1, minWidth: 0, padding: "8px 0", opacity: gone ? 0.45 : 1 }}>
                  <div style={{ fontSize: 14, fontWeight: 500, textDecoration: gone ? "line-through" : "none" }}>{item.name}</div>
                  {item.amount && (
                    <div className="num" style={{ fontSize: 12, color: "var(--me-muted)", marginTop: 1 }}>{item.amount}</div>
                  )}
                </div>
                <span className="num" style={{ fontSize: 14, opacity: gone ? 0.45 : 1 }}>{formatKcal(item.kcal)}</span>
                <button
                  type="button"
                  className="text-btn"
                  style={{ margin: 0, minWidth: 56, textAlign: "right" }}
                  aria-label={gone ? `Keep ${item.name}` : `Leave out ${item.name}`}
                  onClick={() => setRemoved((current) => {
                    const next = new Set(current)
                    if (gone) next.delete(item.key)
                    else next.add(item.key)
                    return next
                  })}
                >
                  {gone ? "Keep" : "Leave out"}
                </button>
              </li>
            )
          })}
        </ul>
        {!editing && (
          <p style={{ margin: "10px 0 0", fontSize: 13, color: "var(--me-muted)", lineHeight: 1.45 }}>
            Amounts are the ones you logged. Your log stays as it is.
          </p>
        )}
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
          type="submit"
          disabled={busy || !canSave}
          style={{
            font: "inherit", fontSize: 14, fontWeight: 500, height: 44, padding: "0 20px",
            borderRadius: 10, border: "none", background: "var(--me-green)", color: "var(--me-on-green)",
            cursor: busy || !canSave ? "not-allowed" : "pointer",
            opacity: busy || !canSave ? 0.6 : 1, flex: 1,
          }}
        >
          {busy ? "Saving…" : editing ? "Save changes" : "Save recipe"}
        </button>
      </div>

      {editing && (
        <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap", minHeight: 32 }}>
          {confirmDelete ? (
            <>
              <span style={{ fontSize: 14 }}>Delete this recipe? Days you logged it on keep their entries.</span>
              <button type="button" className="text-btn" data-tone="danger" style={{ margin: 0 }} disabled={busy} onClick={() => void remove()}>Delete</button>
              <button type="button" className="text-btn" style={{ margin: 0 }} onClick={() => setConfirmDelete(false)}>Keep it</button>
            </>
          ) : (
            <button type="button" className="text-btn" data-tone="danger" style={{ margin: 0 }} onClick={() => setConfirmDelete(true)}>Delete recipe</button>
          )}
        </div>
      )}
    </form>
  )
}
