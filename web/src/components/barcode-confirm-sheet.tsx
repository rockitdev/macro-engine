/**
 * BarcodeConfirmSheet
 *
 * Shown after a barcode scan resolves to an Open Food Facts product.
 * Displays the product name, brand, and per-serving macros, lets the user
 * adjust the quantity (in servings), then logs it via the normal log path.
 *
 * On OFF miss → calls onFallback(barcode) so the user can search manually.
 * On confirmed log → calls onLogged().
 */

import { useState } from "react"

import { addAlias, addFood, ApiError, logMeal, searchFoods } from "@/api"
import { formatKcal } from "@/lib/format"
import { type Meal } from "@/lib/meals"
import type { BarcodeFood } from "@/types"

interface Props {
  food: BarcodeFood
  date: string
  meal: Meal
  onLogged: () => void
  onFallback: (barcode: string) => void
  onClose: () => void
}

function scale(value: number | null, factor: number): number | null {
  return value == null ? null : Math.round(value * factor * 10) / 10
}

export function BarcodeConfirmSheet({ food, date, meal, onLogged, onFallback, onClose }: Props) {
  const hasServing = food.portion_grams != null && food.portion_grams > 0
  // Default quantity: 1 serving if we have serving info, else 100 g
  const defaultQty = hasServing ? "1" : "100"
  const [qty, setQty] = useState(defaultQty)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const qtyNum = parseFloat(qty)
  const valid = Number.isFinite(qtyNum) && qtyNum > 0

  // Compute display macros
  // If we have serving data: macros per 100 g × (servings × grams_per_serving / 100)
  // If no serving data: macros are per 100 g, qty is grams directly
  function macroFactor(): number {
    if (!valid) return 0
    if (hasServing) {
      return (qtyNum * food.portion_grams!) / 100
    }
    return qtyNum / 100
  }

  const factor = macroFactor()
  const displayKcal = scale(food.kcal, factor)
  const displayProtein = scale(food.protein_g, factor)
  const displayCarbs = scale(food.carb_g, factor)
  const displayFat = scale(food.fat_g, factor)

  const unitLabel = hasServing
    ? `serving${qtyNum !== 1 ? "s" : ""} (${food.portion_label ?? `${food.portion_grams} g`})`
    : "g"

  const macroSummary = [
    food.kcal != null && displayKcal != null ? `${formatKcal(displayKcal)} kcal` : null,
    food.protein_g != null && displayProtein != null ? `${displayProtein} g protein` : null,
    food.carb_g != null && displayCarbs != null ? `${displayCarbs} g carbs` : null,
    food.fat_g != null && displayFat != null ? `${displayFat} g fat` : null,
  ].filter(Boolean).join(" · ")

  async function confirmLog() {
    if (!valid) { setError("Enter a valid quantity greater than 0."); return }
    setBusy(true)
    setError(null)
    try {
      // 1. Check if barcode is already aliased to avoid creating duplicate food rows.
      //    searchFoods resolves exact aliases first; a matched_alias hit means this
      //    barcode is already registered — reuse the existing food_id.
      let foodId: number | null = null
      const existing = await searchFoods(food.barcode)
      if (existing.length > 0 && existing[0].matched_alias) {
        foodId = existing[0].id
      }

      if (foodId == null) {
        // No alias yet — create the food row and register the barcode as alias.
        const created = await addFood({
          name: food.name,
          kcal: food.kcal ?? 0,
          protein_g: food.protein_g ?? 0,
          carb_g: food.carb_g ?? 0,
          fat_g: food.fat_g ?? 0,
          fiber_g: food.fiber_g,
          brand: food.brand,
          portion_label: food.portion_label,
          portion_grams: food.portion_grams,
          macros_are_per_portion: false, // macros are always per 100 g from OFF
          alias: food.barcode,           // barcode → instant re-scan resolution
        })
        foodId = created.food_id

        // Persist the alias so re-scans skip addFood entirely next time.
        try {
          await addAlias({ phrase: food.barcode, food_id: foodId })
        } catch {
          /* non-fatal: race-condition duplicate is fine */
        }
      }

      // 2. Log the entry (foodId is always set by this point — either from the
      //    alias hit or from addFood above; addFood throws on failure so we can't
      //    reach here with a null)
      const grams = hasServing ? qtyNum * food.portion_grams! : qtyNum
      const result = await logMeal({
        date,
        meal,
        raw_text: `${food.name} (barcode ${food.barcode})`,
        items: [{ food_id: foodId!, grams }],
      })

      if (result.problems.length > 0) {
        setError(result.problems[0].reason)
        return
      }

      onLogged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not log that food")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Product header */}
      <div>
        <p style={{ margin: 0, fontSize: 18, fontWeight: 600, lineHeight: 1.3 }}>{food.name}</p>
        {food.brand && (
          <p style={{ margin: "4px 0 0", fontSize: 14, color: "#5f6b65" }}>{food.brand}</p>
        )}
      </div>

      {/* Macros per 100 g */}
      <div style={{
        display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8,
        padding: "12px 14px", borderRadius: 10, background: "#f7f8f7",
      }}>
        {[
          { label: "Calories", value: food.kcal, unit: "kcal" },
          { label: "Protein", value: food.protein_g, unit: "g" },
          { label: "Carbs", value: food.carb_g, unit: "g" },
          { label: "Fat", value: food.fat_g, unit: "g" },
        ].map(({ label, value, unit }) => (
          <div key={label} style={{ textAlign: "center" }}>
            <div style={{ fontSize: 12, color: "#5f6b65", marginBottom: 2 }}>{label}</div>
            <div style={{ fontSize: 16, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
              {value != null ? (unit === "kcal" ? formatKcal(value) : value) : "—"}
            </div>
            <div style={{ fontSize: 11, color: "#8fa89a" }}>per 100 g</div>
          </div>
        ))}
      </div>

      {/* Quantity input */}
      <div>
        <label
          htmlFor="barcode-qty"
          style={{ display: "block", fontSize: 13, color: "#5f6b65", marginBottom: 6 }}
        >
          {hasServing ? `Quantity (servings — ${food.portion_label ?? `${food.portion_grams} g each`})` : "Amount (grams)"}
        </label>
        <input
          id="barcode-qty"
          type="number"
          inputMode="decimal"
          min="0.1"
          step="0.1"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          style={{
            boxSizing: "border-box", width: "100%", height: 40,
            border: "1px solid #d5dbd7", borderRadius: 9, padding: "0 12px",
            font: "inherit", fontSize: 16, outline: "none",
          }}
        />
      </div>

      {/* Live macro preview */}
      {valid && macroSummary && (
        <p style={{ margin: 0, fontSize: 13, color: "#5f6b65" }}>
          {qtyNum} {unitLabel} → {macroSummary}
        </p>
      )}

      {error && (
        <p style={{ margin: 0, fontSize: 14, color: "#c2512e" }} role="alert">{error}</p>
      )}

      {/* Actions */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button
          type="button"
          disabled={busy || !valid}
          onClick={() => void confirmLog()}
          style={{
            font: "inherit", fontSize: 14, fontWeight: 500,
            height: 44, padding: "0 20px", borderRadius: 10,
            border: "none", background: "#1e6b45", color: "#f6faf7",
            cursor: busy || !valid ? "not-allowed" : "pointer",
            opacity: busy || !valid ? 0.7 : 1,
            flex: 1,
          }}
        >
          {busy ? "Logging…" : "Log this"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onFallback(food.barcode)}
          style={{
            font: "inherit", fontSize: 14, fontWeight: 500,
            height: 44, padding: "0 16px", borderRadius: 10,
            border: "1px solid #e3e7e4", background: "#fff", color: "#111814",
            cursor: "pointer",
          }}
          title="Wrong product? Search manually"
        >
          Wrong match
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onClose}
          style={{
            font: "inherit", fontSize: 14, fontWeight: 500,
            height: 44, padding: "0 16px", borderRadius: 10,
            border: "1px solid #e3e7e4", background: "#fff", color: "#111814",
            cursor: "pointer",
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  )
}
