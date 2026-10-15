import { useEffect, useState } from "react"

import { addAlias, ApiError, getFood, logMeal, searchFoods } from "@/api"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { formatGrams, formatKcal } from "@/lib/format"
import { MEALS, mealLabel, type Meal } from "@/lib/meals"
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
  if (choice.kind === "portion") {
    return { food_id: food.id, qty, unit: choice.label }
  }
  if (choice.kind === "usual") {
    return { food_id: food.id, grams: choice.grams * qty }
  }
  if (choice.kind === "ounce") {
    return { food_id: food.id, qty, unit: "ounce" }
  }
  return { food_id: food.id, qty, unit: "g" }
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
        .finally(() => {
          if (!cancelled) setSearching(false)
        })
    }, 200)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [query, selected])

  const options = selected ? unitsFor(selected) : []
  const choice = options.find((option) => option.id === unitId)?.choice ?? options[0]?.choice
  const amount = Number(qty)
  const grams = choice && Number.isFinite(amount) && amount > 0 ? gramsFor(choice, amount) : null
  const preview =
    selected && grams != null && selected.kcal != null && selected.protein_g != null
      ? `${formatKcal(selected.kcal * grams / 100)} kcal and ${formatGrams(selected.protein_g * grams / 100)} g protein`
      : null

  function selectUnit(nextId: string) {
    const next = options.find((option) => option.id === nextId)
    if (!next || !choice) {
      setUnitId(nextId)
      return
    }
    const switchingToMass = next.choice.kind === "grams" || next.choice.kind === "ounce"
    const leavingMass = choice.kind === "grams" || choice.kind === "ounce"
    if (switchingToMass && !leavingMass && grams != null) {
      setQty(
        next.choice.kind === "ounce"
          ? String(Math.round((grams / OUNCE) * 10) / 10)
          : String(Math.round(grams)),
      )
    } else if (!switchingToMass && leavingMass) {
      setQty("1")
    }
    setUnitId(nextId)
  }

  async function submit() {
    if (!selected || !choice) return
    if (!Number.isFinite(amount) || amount <= 0) {
      setNotice("Enter an amount greater than 0.")
      return
    }
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
      if (result.problems.length > 0) {
        setProblem(result.problems[0])
        return
      }
      if (remember && query.trim()) {
        try {
          await addAlias({
            phrase: query.trim(),
            food_id: selected.id,
            default_grams: grams,
          })
        } catch (err) {
          onLogged()
          setNotice(
            err instanceof ApiError
              ? `Logged, but the name was not saved. ${err.message}`
              : "Logged, but the name was not saved.",
          )
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
    try {
      choose(await getFood(id))
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Could not load that food")
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="mt-10">
      <h2 className="sr-only">Log food</h2>
      <div role="group" aria-label="Meal" className="mb-3 grid grid-cols-4 gap-2">
        {MEALS.map((choice) => (
          <Button
            key={choice.id}
            type="button"
            size="sm"
            variant={meal === choice.id ? "default" : "outline"}
            aria-pressed={meal === choice.id}
            className="w-full px-1"
            onClick={() => onMeal(choice.id)}
          >
            {choice.label}
          </Button>
        ))}
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (selected) void submit()
          else if (results[active]) choose(results[active])
        }}
      >
        <label className="sr-only" htmlFor="food-search">
          Search foods
        </label>
        <Input
          id="food-search"
          value={query}
          autoComplete="off"
          placeholder="Search foods"
          className="h-10"
          onChange={(event) => {
            setQuery(event.target.value)
            setSelected(null)
            setProblem(null)
            setNotice(null)
          }}
          onKeyDown={(event) => {
            if (selected || results.length === 0) return
            if (event.key === "ArrowDown") {
              event.preventDefault()
              setActive((index) => Math.min(results.length - 1, index + 1))
            } else if (event.key === "ArrowUp") {
              event.preventDefault()
              setActive((index) => Math.max(0, index - 1))
            }
          }}
        />

        {!selected && results.length > 0 && (
          <ul className="mt-2 divide-y divide-border" role="listbox">
            {results.map((food, index) => (
              <li key={food.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={index === active}
                  className={`w-full px-1 py-2 text-left ${index === active ? "bg-mist" : ""}`}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => choose(food)}
                >
                  <span className="block">{food.name}</span>
                  <span className="block text-sm text-muted-foreground">
                    {food.store ? `${food.store}. ` : ""}
                    {food.brand ? `${food.brand}. ` : ""}
                    {food.kcal == null ? "No nutrition data" : `${formatKcal(food.kcal)} kcal per 100 g`}
                    {food.matched_alias ? ". Saved name" : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {!selected && !searching && query.trim().length >= 2 && results.length === 0 && (
          <p className="mt-3 text-sm">
            No food matches.{" "}
            <button
              type="button"
              className="underline underline-offset-4"
              onClick={() => onAddFood(query.trim())}
            >
              Add it
            </button>
          </p>
        )}

        {selected && choice && (
          <div className="mt-4 grid gap-3">
            <div className="flex items-baseline justify-between gap-3">
              <p className="font-medium">{selected.name}</p>
              <button
                type="button"
                className="text-sm text-muted-foreground underline underline-offset-4"
                onClick={() => setSelected(null)}
              >
                Change food
              </button>
            </div>
            <div className="grid grid-cols-[6.5rem_1fr] gap-2">
              <div className="grid gap-1.5">
                <label className="text-sm font-medium" htmlFor="food-qty">
                  Amount
                </label>
                <Input
                  id="food-qty"
                  inputMode="decimal"
                  value={qty}
                  onChange={(event) => setQty(event.target.value)}
                />
              </div>
              <div className="grid gap-1.5">
                <label className="text-sm font-medium" htmlFor="food-unit">
                  Unit
                </label>
                <select
                  id="food-unit"
                  value={unitId}
                  onChange={(event) => selectUnit(event.target.value)}
                  className="h-8 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  {options.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            {preview && <p className="text-sm text-muted-foreground">{preview}</p>}
            {canRemember(selected, query) && (
              <div className="flex items-center gap-2">
                <Checkbox id="remember-food" checked={remember} onCheckedChange={setRemember} />
                <label htmlFor="remember-food" className="text-sm">
                  Remember "{query.trim()}"
                </label>
              </div>
            )}
            <div>
              <Button type="submit" disabled={busy}>
                {busy ? "Logging" : `Log ${mealLabel(meal).toLowerCase()}`}
              </Button>
            </div>
          </div>
        )}
      </form>

      {problem && (
        <div className="mt-4 text-sm" role="alert">
          <p>{problem.reason}</p>
          {problem.available_portions && problem.available_portions.length > 0 && (
            <p className="mt-1 text-muted-foreground">
              Portions: {problem.available_portions.join(", ")}
            </p>
          )}
          {problem.candidates && problem.candidates.length > 0 && (
            <ul className="mt-2 grid gap-1">
              {problem.candidates.map((candidate) => (
                <li key={candidate.food_id}>
                  <button
                    type="button"
                    className="underline underline-offset-4"
                    onClick={() => void pickCandidate(candidate.food_id)}
                  >
                    {candidate.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {notice && (
        <p className="mt-3 text-sm" role="alert">
          {notice}
        </p>
      )}
    </section>
  )
}
