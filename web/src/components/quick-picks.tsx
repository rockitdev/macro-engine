/**
 * QuickPicks — log without searching. Sits under the search field while it is
 * empty, with three sources:
 *   Usuals   what you log most at this meal (from the log itself)
 *   Kitchen  foods you keep on hand
 *   Recipes  dishes you saved, logged by serving
 *
 * The + logs the usual amount in one tap. Tapping the row opens the food in
 * the composer with that amount filled in, for when today's is different.
 */

import { useEffect, useRef, useState } from "react"

import { ApiError, getKitchen, getRecipes, getUsuals, logMeal } from "@/api"
import { formatGrams, formatKcal } from "@/lib/format"
import { mealLabel, type Meal } from "@/lib/meals"
import type { Food, Pick, Recipe, Usual } from "@/types"

type Tab = "usual" | "kitchen" | "recipes"

const TABS: { id: Tab; label: string }[] = [
  { id: "usual", label: "Usuals" },
  { id: "kitchen", label: "Kitchen" },
  { id: "recipes", label: "Recipes" },
]

const COLLAPSED = 4
const TAB_KEY = "me.quickTab"

type Row = {
  food: Food
  usual: Usual
  detail: string
  recipe?: Recipe
}

function savedTab(): Tab {
  try {
    const value = window.localStorage.getItem(TAB_KEY)
    if (value === "usual" || value === "kitchen" || value === "recipes") return value
  } catch { /* storage can be unavailable; the default is fine */ }
  return "usual"
}

function macroLine(usual: Usual) {
  return `P ${formatGrams(usual.protein_g)} · C ${formatGrams(usual.carb_g)} · F ${formatGrams(usual.fat_g)}`
}

function pickRow(pick: Pick): Row {
  return { food: pick, usual: pick.usual, detail: `${pick.usual.desc} · ${macroLine(pick.usual)}` }
}

function recipeRow(recipe: Recipe): Row {
  const usual: Usual = {
    item: { qty: 1, unit: "1 serving" },
    desc: "1 serving",
    grams: recipe.serving_grams ?? 100,
    ...recipe.per_serving,
  }
  // Lead with the serving count: it is what gets cut off on a narrow screen otherwise.
  return { food: recipe.food, usual, recipe, detail: `1 of ${formatGrams(recipe.servings)} servings · ${macroLine(usual)}` }
}

function Empty({ tab, meal }: { tab: Tab; meal: Meal }) {
  const text =
    tab === "usual"
      ? `Nothing logged lately. What you eat at ${mealLabel(meal).toLowerCase()} will show up here for one-tap logging.`
      : tab === "kitchen"
        ? "Your kitchen is empty. Open the menu on anything you've logged and choose Add to kitchen."
        : "No recipes yet. Tap Select in the log below, pick the ingredients, then Save as recipe."
  return <p style={{ margin: 0, padding: "14px 0 4px", fontSize: 14, color: "var(--me-muted)", lineHeight: 1.45 }}>{text}</p>
}

export function QuickPicks({
  date,
  meal,
  stamp,
  hadFoodIds,
  onChoose,
  onLogged,
  onEditRecipe,
}: {
  date: string
  meal: Meal
  /** Changes whenever the day's data reloads, so the lists refetch. */
  stamp: unknown
  /** Foods already logged at this meal today. */
  hadFoodIds: Set<number>
  onChoose: (food: Food, usual: Usual) => void
  onLogged: () => void
  onEditRecipe: (recipe: Recipe) => void
}) {
  const [tab, setTab] = useState<Tab>(savedTab)
  const [rows, setRows] = useState<Row[] | null>(null)
  const [shownFor, setShownFor] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [doneId, setDoneId] = useState<number | null>(null)
  const doneTimer = useRef<number | undefined>(undefined)

  const view = `${tab}|${tab === "usual" ? meal : ""}`

  useEffect(() => {
    const controller = new AbortController()
    const load =
      tab === "usual" ? getUsuals(meal, date, controller.signal).then((r) => r.usual.map(pickRow))
      : tab === "kitchen" ? getKitchen(date, controller.signal).then((r) => r.foods.map(pickRow))
      : getRecipes(controller.signal).then((r) => r.recipes.map(recipeRow))
    load
      .then((next) => {
        setRows(next)
        setShownFor(view)
        setError(null)
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return
        setError(err instanceof ApiError ? err.message : "Could not load this list")
      })
    return () => controller.abort()
  }, [tab, meal, date, stamp, view])

  useEffect(() => () => window.clearTimeout(doneTimer.current), [])

  function selectTab(next: Tab) {
    setTab(next)
    setExpanded(false)
    try { window.localStorage.setItem(TAB_KEY, next) } catch { /* not essential */ }
  }

  async function quickLog(row: Row) {
    setBusyId(row.food.id)
    setError(null)
    try {
      const result = await logMeal({
        date,
        meal,
        raw_text: row.food.name,
        items: [{ food_id: row.food.id, ...row.usual.item }],
      })
      if (result.problems.length > 0) {
        setError(result.problems[0].reason)
        return
      }
      setDoneId(row.food.id)
      window.clearTimeout(doneTimer.current)
      doneTimer.current = window.setTimeout(() => setDoneId(null), 1600)
      onLogged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not log that food")
    } finally {
      setBusyId(null)
    }
  }

  // Keep the previous list on screen while the next one loads, unless it
  // belongs to a different tab or meal.
  const current = shownFor === view ? rows : null
  const visible = current ? (expanded ? current : current.slice(0, COLLAPSED)) : []
  const hidden = current ? current.length - visible.length : 0
  const mealName = mealLabel(meal).toLowerCase()

  return (
    <div>
      <div role="tablist" aria-label="Quick log" className="quick-tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className="quick-tab"
            onClick={() => selectTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error && (
        <p style={{ margin: 0, padding: "12px 0 0", fontSize: 14, color: "var(--me-roast)" }} role="alert">{error}</p>
      )}

      {current && current.length === 0 && !error && <Empty tab={tab} meal={meal} />}

      {current === null && !error && (
        <p style={{ margin: 0, padding: "14px 0 4px", fontSize: 14, color: "var(--me-faint)" }}>Loading…</p>
      )}

      {visible.length > 0 && (
        <ul className="quick-list">
          {visible.map((row) => {
            const done = doneId === row.food.id
            const had = hadFoodIds.has(row.food.id)
            return (
              <li key={row.food.id} className="quick-row">
                <button
                  type="button"
                  className="quick-row-main"
                  aria-label={`${row.food.name}, ${row.usual.desc}. Change the amount before logging`}
                  onClick={() => onChoose(row.food, row.usual)}
                >
                  <span className="quick-row-name">
                    <span>{row.food.name}</span>
                    {had && <span className="had-dot" role="img" aria-label={`Already logged at ${mealName}`} />}
                  </span>
                  <span className="quick-row-sub num">{row.detail}</span>
                </button>

                <span className="num" style={{ fontSize: 14, fontWeight: 500 }}>{formatKcal(row.usual.kcal)}</span>

                {row.recipe && (
                  <button
                    type="button"
                    className="icon-btn"
                    aria-label={`Edit ${row.food.name}`}
                    onClick={() => onEditRecipe(row.recipe!)}
                  >
                    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 20h9"/><path d="M16.4 3.6a2 2 0 0 1 2.9 2.9L7 18.8l-4 1 1-4Z"/>
                    </svg>
                  </button>
                )}

                <button
                  type="button"
                  className="add-btn"
                  data-state={done ? "done" : undefined}
                  disabled={busyId !== null}
                  aria-label={done ? `Logged ${row.food.name}` : `Log ${row.usual.desc} of ${row.food.name} to ${mealName}`}
                  onClick={() => void quickLog(row)}
                >
                  <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    {done ? <path d="m5 12.5 4.5 4.5L19 7.5"/> : <path d="M12 5v14M5 12h14"/>}
                  </svg>
                </button>
              </li>
            )
          })}
        </ul>
      )}

      {current && current.length > COLLAPSED && (
        <div style={{ paddingTop: 10 }}>
          <button type="button" className="text-btn" onClick={() => setExpanded((v) => !v)}>
            {expanded ? "Show fewer" : `Show ${hidden} more`}
          </button>
        </div>
      )}
    </div>
  )
}
