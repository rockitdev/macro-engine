import { useEffect, useState } from "react"

import { addAlias, ApiError, getFood, logMeal, searchFoods } from "@/api"
import { Checkbox } from "@/components/ui/checkbox"
import { formatGrams, formatKcal } from "@/lib/format"
import { MEALS, type Meal } from "@/lib/meals"
import type { Food, Problem } from "@/types"

const OUNCE = 28.3495

type Choice =
  | { kind: "portion"; label: string; grams: number }
  | { kind: "usual"; grams: number }
  | { kind: "grams" }
  | { kind: "ounce" }

type UnitOption = { id: string; label: string; choice: Choice }

function unitsFor(food: Food): UnitOption[] {
  const options: UnitOption[] = []
  if (food.matched_alias && food.alias_default_grams) {
    options.push({
      id: "usual",
      label: `Usual (${formatGrams(food.alias_default_grams)} g)`,
      choice: { kind: "usual", grams: food.alias_default_grams },
    })
  }
  for (const portion of food.portions ?? []) {
    options.push({
      id: `portion:${portion.label}`,
      label: `${portion.label} (${formatGrams(portion.grams)} g)`,
      choice: { kind: "portion", label: portion.label, grams: portion.grams },
    })
  }
  options.push({ id: "grams", label: "Grams", choice: { kind: "grams" } })
  options.push({ id: "ounce", label: "Ounces", choice: { kind: "ounce" } })
  return options
}

function gramsFor(choice: Choice, qty: number) {
  if (choice.kind === "portion" || choice.kind === "usual") return qty * choice.grams
  if (choice.kind === "ounce") return qty * OUNCE
  return qty
}

function canRemember(food: Food, phrase: string) {
  const trimmed = phrase.trim().toLowerCase()
  return Boolean(trimmed) && trimmed !== food.name.trim().toLowerCase() && !food.matched_alias
}

function itemFor(food: Food, choice: Choice, qty: number) {
  if (choice.kind === "portion") return { food_id: food.id, qty, unit: choice.label }
  if (choice.kind === "usual") return { food_id: food.id, grams: choice.grams * qty }
  if (choice.kind === "ounce") return { food_id: food.id, qty, unit: "ounce" }
  return { food_id: food.id, qty, unit: "g" }
}

function sourceLabel(food: Food): string {
  const parts: string[] = []
  if (food.store) parts.push(food.store)
  else if (food.brand) parts.push(food.brand)
  else parts.push(food.source === "usda" ? "USDA" : food.source)
  if (food.kcal != null) parts.push(`${formatKcal(food.kcal)} kcal / 100 g`)
  return parts.join(" · ")
}

// ── Styles ────────────────────────────────────────────────────────────────────
const btnBase: React.CSSProperties = {
  font: "inherit",
  fontSize: 14,
  fontWeight: 500,
  height: 36,
  padding: "0 14px",
  borderRadius: 9,
  border: "1px solid #e3e7e4",
  background: "#fff",
  color: "#111814",
  display: "inline-flex",
  alignItems: "center",
  gap: 8,
  cursor: "pointer",
}

export function Composer({
  date,
  meal,
  onMeal,
  seed,
  onSeedConsumed,
  onLogged,
  onAddFood,
}: {
  date: string
  meal: Meal
  onMeal: (meal: Meal) => void
  seed: Food | null
  onSeedConsumed: () => void
  onLogged: () => void
  onAddFood: (name: string) => void
}) {
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<Food[]>([])
  const [searching, setSearching] = useState(false)
  const [active, setActive] = useState(0)
  const [selected, setSelected] = useState<Food | null>(null)
  const [unitId, setUnitId] = useState("grams")
  const [qty, setQty] = useState("1")
  const [remember, setRemember] = useState(false)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<Problem | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  function choose(food: Food) {
    const options = unitsFor(food)
    setSelected(food)
    setUnitId(options[0]?.id ?? "grams")
    setQty(options[0]?.choice.kind === "grams" ? "100" : "1")
    setRemember(canRemember(food, query))
    setProblem(null)
    setNotice(null)
  }

  useEffect(() => {
    if (!seed) return
    const options = unitsFor(seed)
    setQuery(seed.name)
    setSelected(seed)
    setUnitId(options[0]?.id ?? "grams")
    setQty(options[0]?.choice.kind === "grams" ? "100" : "1")
    setRemember(false)
    setProblem(null)
    setNotice(null)
    setResults([])
    onSeedConsumed()
  }, [seed, onSeedConsumed])

  useEffect(() => {
    const term = query.trim()
    if (selected || term.length < 2) {
      setResults([])
      setSearching(false)
      return
    }
    const controller = new AbortController()
    let cancelled = false
    const timer = window.setTimeout(() => {
      setSearching(true)
      searchFoods(term, controller.signal)
        .then((hits) => {
          if (cancelled) return
          setResults(hits)
          setActive(0)
        })
        .catch((err: unknown) => {
          if (cancelled || (err instanceof DOMException && err.name === "AbortError")) return
          setNotice(err instanceof ApiError ? err.message : "Search failed")
        })
        .finally(() => { if (!cancelled) setSearching(false) })
    }, 200)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [query, selected])

  const options = selected ? unitsFor(selected) : []
  const choice = options.find((o) => o.id === unitId)?.choice ?? options[0]?.choice
  const amount = Number(qty)
  const grams = choice && Number.isFinite(amount) && amount > 0 ? gramsFor(choice, amount) : null

  // Macro preview
  const previewMacros = selected && grams != null && selected.kcal != null
    ? {
        kcal: Math.round(selected.kcal * grams / 100),
        protein: selected.protein_g != null ? Math.round(selected.protein_g * grams / 100) : null,
        carbs: selected.carb_g != null ? Math.round(selected.carb_g * grams / 100) : null,
        fat: selected.fat_g != null ? Math.round(selected.fat_g * grams / 100) : null,
      }
    : null
  const previewText = previewMacros
    ? [
        `${formatKcal(previewMacros.kcal)} kcal`,
        previewMacros.protein != null ? `${formatGrams(previewMacros.protein)} g protein` : null,
        previewMacros.carbs != null ? `${formatGrams(previewMacros.carbs)} g carbs` : null,
        previewMacros.fat != null ? `${formatGrams(previewMacros.fat)} g fat` : null,
      ].filter(Boolean).join(" · ")
    : null

  function selectUnit(nextId: string) {
    const next = options.find((o) => o.id === nextId)
    if (!next || !choice) { setUnitId(nextId); return }
    const switchingToMass = next.choice.kind === "grams" || next.choice.kind === "ounce"
    const leavingMass = choice.kind === "grams" || choice.kind === "ounce"
    if (switchingToMass && !leavingMass && grams != null) {
      setQty(next.choice.kind === "ounce"
        ? String(Math.round((grams / OUNCE) * 10) / 10)
        : String(Math.round(grams)))
    } else if (!switchingToMass && leavingMass) {
      setQty("1")
    }
    setUnitId(nextId)
  }

  async function submit() {
    if (!selected || !choice) return
    if (!Number.isFinite(amount) || amount <= 0) { setNotice("Enter an amount greater than 0."); return }
    setBusy(true)
    setNotice(null)
    setProblem(null)
    try {
      const result = await logMeal({
        date,
        meal,
        raw_text: query.trim() || selected.name,
        items: [itemFor(selected, choice, amount)],
      })
      if (result.problems.length > 0) { setProblem(result.problems[0]); return }
      if (remember && query.trim()) {
        try {
          await addAlias({ phrase: query.trim(), food_id: selected.id, default_grams: grams })
        } catch (err) {
          onLogged()
          setNotice(err instanceof ApiError ? `Logged, but the name was not saved. ${err.message}` : "Logged, but the name was not saved.")
          return
        }
      }
      setQuery("")
      setSelected(null)
      setResults([])
      setRemember(false)
      onLogged()
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Could not log that food")
    } finally {
      setBusy(false)
    }
  }

  async function pickCandidate(id: number) {
    setBusy(true)
    setNotice(null)
    try { choose(await getFood(id)) }
    catch (err) { setNotice(err instanceof ApiError ? err.message : "Could not load that food") }
    finally { setBusy(false) }
  }

  const activeMealLabel = MEALS.find((m) => m.id === meal)?.label ?? "Dinner"

  return (
    <>
      {/* Title + meal tabs */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Log food</h2>
        <div role="group" aria-label="Meal" className="pill-nav" style={{ padding: 3 }}>
          {MEALS.map((m) => (
            <button
              key={m.id}
              type="button"
              className="pill-nav-item"
              aria-selected={meal === m.id}
              style={{ height: 28, fontSize: 13 }}
              onClick={() => onMeal(m.id)}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      {/* Search field */}
      <form
        style={{ marginTop: 14 }}
        onSubmit={(e) => {
          e.preventDefault()
          if (selected) void submit()
          else if (results[active]) choose(results[active])
        }}
      >
        <label
          htmlFor="food-search"
          style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}
        >
          Search foods
        </label>
        <div style={{ position: "relative" }}>
          <svg
            aria-hidden="true"
            style={{ position: "absolute", left: 12, top: 12, pointerEvents: "none" }}
            width="16" height="16" viewBox="0 0 24 24" fill="none"
            stroke="#5f6b65" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          >
            <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
          </svg>
          <input
            id="food-search"
            value={query}
            autoComplete="off"
            placeholder="Search foods"
            style={{
              boxSizing: "border-box", width: "100%", height: 40,
              border: selected ? "1px solid #1e6b45" : "1px solid #d5dbd7",
              boxShadow: selected ? "0 0 0 3px rgba(30,107,69,.15)" : "none",
              borderRadius: 10, padding: "0 12px 0 36px",
              font: "inherit", fontSize: 14, outline: "none",
            }}
            onChange={(e) => {
              setQuery(e.target.value)
              setSelected(null)
              setProblem(null)
              setNotice(null)
            }}
            onKeyDown={(e) => {
              if (selected || results.length === 0) return
              if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(results.length - 1, i + 1)) }
              else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(0, i - 1)) }
            }}
          />
        </div>

        {/* Search results listbox */}
        {!selected && results.length > 0 && (
          <ul
            role="listbox"
            style={{ listStyle: "none", margin: "8px 0 0", padding: 4, border: "1px solid #e3e7e4", borderRadius: 10 }}
          >
            {results.map((food, index) => (
              <li key={food.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={index === active}
                  style={{
                    all: "unset", boxSizing: "border-box", width: "100%",
                    display: "flex", justifyContent: "space-between", gap: 12,
                    padding: "8px 10px", borderRadius: 7,
                    background: index === active ? "#eef1ef" : "transparent",
                    cursor: "pointer",
                  }}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => choose(food)}
                >
                  <span>
                    <span style={{ display: "block", fontSize: 14, fontWeight: 500 }}>{food.name}</span>
                    <span style={{ display: "block", fontSize: 12, color: "#5f6b65" }}>{sourceLabel(food)}</span>
                  </span>
                  {food.matched_alias && (
                    <span style={{
                      alignSelf: "center", fontSize: 11, fontWeight: 500,
                      color: "#1e6b45", background: "#e7f2ec",
                      padding: "2px 8px", borderRadius: 99,
                    }}>
                      Saved name
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}

        {!selected && !searching && query.trim().length >= 2 && results.length === 0 && (
          <p style={{ marginTop: 12, fontSize: 14, color: "#5f6b65" }}>
            No food matches.{" "}
            <button
              type="button"
              style={{ background: "none", border: "none", padding: 0, font: "inherit", fontSize: 14, color: "#1e6b45", cursor: "pointer", textDecoration: "underline" }}
              onClick={() => onAddFood(query.trim())}
            >
              Add it
            </button>
          </p>
        )}

        {/* Amount / unit / log controls */}
        {selected && choice && (
          <div style={{
            marginTop: 14, padding: 14, borderRadius: 10, background: "#f7f8f7",
            display: "grid",
            gridTemplateColumns: "110px minmax(0, 1fr) auto",
            gap: 10, alignItems: "end",
          }}>
            {/* Amount */}
            <div>
              <label style={{ display: "block", fontSize: 13, color: "#5f6b65", marginBottom: 6 }} htmlFor="food-qty">Amount</label>
              <input
                id="food-qty"
                inputMode="decimal"
                value={qty}
                onChange={(e) => setQty(e.target.value)}
                style={{
                  boxSizing: "border-box", width: "100%", height: 36,
                  border: "1px solid #d5dbd7", borderRadius: 9, padding: "0 10px",
                  font: "inherit", fontSize: 14, background: "#fff", outline: "none",
                }}
              />
            </div>

            {/* Unit */}
            <div>
              <label style={{ display: "block", fontSize: 13, color: "#5f6b65", marginBottom: 6 }} htmlFor="food-unit">Unit</label>
              <div style={{ position: "relative" }}>
                <select
                  id="food-unit"
                  value={unitId}
                  onChange={(e) => selectUnit(e.target.value)}
                  style={{
                    appearance: "none", boxSizing: "border-box", width: "100%", height: 36,
                    border: "1px solid #d5dbd7", borderRadius: 9, padding: "0 28px 0 10px",
                    font: "inherit", fontSize: 14, background: "#fff",
                    color: "#111814", outline: "none", cursor: "pointer",
                  }}
                >
                  {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                </select>
                <svg
                  aria-hidden="true"
                  style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }}
                  width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                >
                  <path d="m6 9 6 6 6-6"/>
                </svg>
              </div>
            </div>

            {/* Log button */}
            <button
              type="submit"
              disabled={busy}
              style={{
                ...btnBase,
                background: "#1e6b45", borderColor: "#1e6b45", color: "#f6faf7",
                opacity: busy ? 0.7 : 1, minWidth: 0,
              }}
            >
              {busy ? "Logging…" : `Log ${activeMealLabel.toLowerCase()}`}
            </button>

            {/* Preview row */}
            {previewText && (
              <div className="num" style={{ gridColumn: "1 / -1", fontSize: 13, color: "#5f6b65" }}>
                {previewText}
              </div>
            )}
          </div>
        )}

        {/* Remember alias */}
        {selected && canRemember(selected, query) && (
          <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 8 }}>
            <Checkbox id="remember-food" checked={remember} onCheckedChange={setRemember} />
            <label htmlFor="remember-food" style={{ fontSize: 14, cursor: "pointer" }}>
              Remember "{query.trim()}"
            </label>
          </div>
        )}
      </form>

      {/* Problems / notices */}
      {problem && (
        <div style={{ marginTop: 12, fontSize: 14 }} role="alert">
          <p style={{ color: "#c2512e" }}>{problem.reason}</p>
          {problem.available_portions && problem.available_portions.length > 0 && (
            <p style={{ marginTop: 4, color: "#5f6b65" }}>Portions: {problem.available_portions.join(", ")}</p>
          )}
          {problem.candidates && problem.candidates.length > 0 && (
            <ul style={{ marginTop: 8, padding: 0, listStyle: "none", display: "grid", gap: 4 }}>
              {problem.candidates.map((c) => (
                <li key={c.food_id}>
                  <button
                    type="button"
                    style={{ background: "none", border: "none", padding: 0, font: "inherit", fontSize: 14, color: "#1e6b45", cursor: "pointer", textDecoration: "underline" }}
                    onClick={() => void pickCandidate(c.food_id)}
                  >
                    {c.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {notice && (
        <p style={{ marginTop: 12, fontSize: 14, color: "#c2512e" }} role="alert">{notice}</p>
      )}
    </>
  )
}
