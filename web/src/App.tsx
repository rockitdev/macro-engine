import { useCallback, useEffect, useState } from "react"

import { ApiError, deleteLog, getDay, getFood, getMonth, getWeek, setLogMeal, setWeekStart } from "@/api"
import { DayView } from "@/components/day-view"
import { FitMealDialog } from "@/components/fit-meal-dialog"
import { FoodDialog } from "@/components/food-dialog"
import { MonthView } from "@/components/month-view"
import { TargetsDialog } from "@/components/targets-dialog"
import { WeekView } from "@/components/week-view"
import { formatLong, formatMonth, formatSpan, isoToday, shiftDate, shiftMonth } from "@/lib/format"
import { defaultMeal, type Meal } from "@/lib/meals"
import type { Day, Food, Month, Week } from "@/types"

type Range = "day" | "week" | "month"

// ── Icons ────────────────────────────────────────────────────────────────────
function LeafIcon() {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#f6faf7" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/>
      <path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"/>
    </svg>
  )
}
function ChevLeft() {
  return (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m15 18-6-6 6-6"/>
    </svg>
  )
}
function ChevRight() {
  return (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m9 18 6-6-6-6"/>
    </svg>
  )
}
function TargetIcon() {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>
    </svg>
  )
}
function PlusIcon() {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 5v14M5 12h14"/>
    </svg>
  )
}

export default function App() {
  const today = isoToday()
  const [date, setDate] = useState(today)
  const [mode, setMode] = useState<Range>("day")
  const [day, setDay] = useState<Day | null>(null)
  const [week, setWeek] = useState<Week | null>(null)
  const [month, setMonth] = useState<Month | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [targetsOpen, setTargetsOpen] = useState(false)
  const [foodOpen, setFoodOpen] = useState(false)
  const [foodName, setFoodName] = useState("")
  const [seed, setSeed] = useState<Food | null>(null)
  const [pendingId, setPendingId] = useState<number | null>(null)
  const [meal, setMeal] = useState<Meal>(defaultMeal)
  const [fitMealOpen, setFitMealOpen] = useState(false)
  const consumeSeed = useCallback(() => setSeed(null), [])

  // When a new food is created while in week/month mode, switch to day view to log it
  useEffect(() => {
    if (seed && mode !== "day") setMode("day")
  }, [seed, mode])

  const load = useCallback(async (next: string, range: Range) => {
    try {
      const [payload, weekPayload, monthPayload] = await Promise.all([
        getDay(next),
        getWeek(next),
        range === "month" ? getMonth(next) : Promise.resolve(null),
      ])
      setDay(payload)
      setWeek(weekPayload)
      if (monthPayload) setMonth(monthPayload)
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load that view")
    }
  }, [])

  useEffect(() => {
    let active = true
    Promise.all([
      getDay(date),
      getWeek(date),
      mode === "month" ? getMonth(date) : Promise.resolve(null),
    ])
      .then(([payload, weekPayload, monthPayload]) => {
        if (!active) return
        setDay(payload)
        setWeek(weekPayload)
        if (monthPayload) setMonth(monthPayload)
        setError(null)
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof ApiError ? err.message : "Could not load that view")
      })
    return () => { active = false }
  }, [date, mode])

  const ready =
    mode === "day" ? day?.date === date
    : mode === "week" ? week?.focus === date
    : month?.focus === date

  const rangeLabel =
    mode === "week" && week?.focus === date
      ? formatSpan(week.start, week.end)
      : mode === "month" && month?.focus === date
        ? formatMonth(date)
        : formatLong(date)

  const showingToday =
    mode === "week" && week?.focus === date
      ? today >= week.start && today <= week.end
      : mode === "month" && month?.focus === date
        ? today >= month.start && today <= month.end
        : date === today

  // Weekly pace badge
  const weekPaceBadge = week?.focus === date && week?.saved && day?.date === date
    ? (week.saved.carb_g >= 0 && week.saved.fat_g >= 0
        ? "On pace for the week"
        : week.saved.carb_g < 0 || week.saved.fat_g < 0
          ? "Over this week"
          : null)
    : null

  function step(direction: number) {
    setDate((current) =>
      mode === "month" ? shiftMonth(current, direction)
      : shiftDate(current, mode === "week" ? direction * 7 : direction),
    )
  }

  function openDay(next: string) {
    setMode("day")
    setDate(next)
  }

  async function saveWeekStart(dayName: string) {
    try {
      await setWeekStart(dayName)
      await load(date, mode)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change when the week starts")
      throw err
    }
  }

  async function move(id: number, next: Meal | null) {
    setPendingId(id)
    try {
      await setLogMeal(id, next)
      await load(date, mode)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not move that entry")
    } finally {
      setPendingId(null)
    }
  }

  async function remove(id: number) {
    setPendingId(id)
    try {
      await deleteLog(id)
      await load(date, mode)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not remove that entry")
    } finally {
      setPendingId(null)
    }
  }

  return (
    <div style={{ fontFamily: "var(--font-sans)", color: "#111814", minHeight: "100vh", background: "#f7f8f7" }}>
      {/* ── Header ── */}
      <header style={{ borderBottom: "1px solid #e3e7e4", background: "rgba(255,255,255,.92)", position: "sticky", top: 0, zIndex: 40, backdropFilter: "blur(8px)" }}>
        <div style={{ maxWidth: 1120, margin: "0 auto", padding: "12px 24px", display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
          {/* Logo */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, fontWeight: 600, fontSize: 15 }}>
            <span style={{ width: 28, height: 28, borderRadius: 8, background: "#1e6b45", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
              <LeafIcon />
            </span>
            Macro Engine
          </div>

          {/* Range nav */}
          <nav aria-label="Range" className="pill-nav" style={{ marginLeft: 12 }}>
            {(["day", "week", "month"] as const).map((id) => (
              <button
                key={id}
                type="button"
                className="pill-nav-item"
                aria-selected={mode === id}
                onClick={() => setMode(id)}
              >
                {id === "day" ? "Day" : id === "week" ? "Week" : "Month"}
              </button>
            ))}
          </nav>

          <div style={{ flex: 1 }} />

          {/* Eating out */}
          <button
            type="button"
            onClick={() => setFitMealOpen(true)}
            style={{ font: "inherit", fontSize: 14, fontWeight: 500, height: 36, padding: "0 14px", borderRadius: 9, border: "1px solid #e3e7e4", background: "#fff", color: "#111814", display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}
          >
            Eating out?
          </button>

          {/* Targets */}
          <button
            type="button"
            onClick={() => setTargetsOpen(true)}
            style={{ font: "inherit", fontSize: 14, fontWeight: 500, height: 36, padding: "0 14px", borderRadius: 9, border: "1px solid #e3e7e4", background: "#fff", color: "#111814", display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}
          >
            <TargetIcon />
            Targets
          </button>

          {/* New food */}
          <button
            type="button"
            onClick={() => { setFoodName(""); setFoodOpen(true) }}
            style={{ font: "inherit", fontSize: 14, fontWeight: 500, height: 36, padding: "0 14px", borderRadius: 9, border: "1px solid #1e6b45", background: "#1e6b45", color: "#f6faf7", display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}
          >
            <PlusIcon />
            New food
          </button>
        </div>
      </header>

      {/* ── Main ── */}
      <main style={{ maxWidth: 1120, margin: "0 auto", padding: "28px 24px 64px" }}>

        {/* Date header */}
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <button
              type="button"
              aria-label={`Previous ${mode}`}
              onClick={() => step(-1)}
              style={{ width: 36, height: 36, padding: 0, borderRadius: 9, border: "1px solid transparent", background: "transparent", color: "#111814", display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}
            >
              <ChevLeft />
            </button>
            <div>
              {showingToday && mode === "day" && (
                <div style={{ fontSize: 12, color: "#5f6b65", textTransform: "uppercase", letterSpacing: ".06em" }}>Today</div>
              )}
              <h1 style={{ margin: 0, fontSize: 24, fontWeight: 600, letterSpacing: "-.02em" }}>{rangeLabel}</h1>
            </div>
            <button
              type="button"
              aria-label={`Next ${mode}`}
              onClick={() => step(1)}
              style={{ width: 36, height: 36, padding: 0, borderRadius: 9, border: "1px solid transparent", background: "transparent", color: "#111814", display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}
            >
              <ChevRight />
            </button>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {!showingToday && (
              <button
                type="button"
                onClick={() => setDate(today)}
                style={{ font: "inherit", fontSize: 13, fontWeight: 500, height: 30, padding: "0 12px", borderRadius: 8, border: "1px solid #e3e7e4", background: "#fff", color: "#111814", cursor: "pointer" }}
              >
                Today
              </button>
            )}
            {weekPaceBadge && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, color: "#1e6b45", background: "#e7f2ec", padding: "4px 10px", borderRadius: 999 }}>
                <span style={{ width: 6, height: 6, borderRadius: 99, background: "#1e6b45" }} />
                {weekPaceBadge}
              </span>
            )}
          </div>
        </div>

        {error && (
          <p style={{ marginTop: 24, fontSize: 14, color: "#c2512e" }} role="alert">{error}</p>
        )}

        {/* ── Day view ── */}
        {mode === "day" && day && day.date === date && week && (
          <DayView
            day={day}
            week={week}
            date={date}
            meal={meal}
            seed={seed}
            pendingId={pendingId}
            onMeal={setMeal}
            onSeedConsumed={consumeSeed}
            onLogged={() => void load(date, mode)}
            onAddFood={(name) => { setFoodName(name); setFoodOpen(true) }}
            onRemove={(id) => void remove(id)}
            onMove={(id, next) => void move(id, next)}
            onWeekStart={saveWeekStart}
            onSelectDay={openDay}
          />
        )}

        {/* ── Week view ── */}
        {mode === "week" && week && week.focus === date && (
          <WeekView
            week={week}
            onSelect={openDay}
            onWeekStart={saveWeekStart}
          />
        )}

        {/* ── Month view ── */}
        {mode === "month" && month && month.focus === date && (
          <MonthView
            month={month}
            onSelect={openDay}
            onWeekStart={saveWeekStart}
          />
        )}

        {!ready && !error && (
          <p style={{ marginTop: 32, fontSize: 14, color: "#5f6b65" }}>Loading the {mode}…</p>
        )}
      </main>

      {/* ── Dialogs ── */}
      <FitMealDialog
        open={fitMealOpen}
        onOpenChange={setFitMealOpen}
        day={day && day.date === date ? day : null}
      />
      <TargetsDialog
        open={targetsOpen}
        onOpenChange={setTargetsOpen}
        date={date}
        targets={day && day.date === date ? day.targets : null}
        onSaved={() => void load(date, mode)}
      />
      <FoodDialog
        open={foodOpen}
        onOpenChange={setFoodOpen}
        initialName={foodName}
        onCreated={(foodId) => {
          void getFood(foodId)
            .then((food) => setSeed(food))
            .catch((err: unknown) => {
              setError(err instanceof ApiError ? err.message : "Saved the food, but could not open it")
            })
        }}
      />

      {/* mode switch is handled in a useEffect below */}
    </div>
  )
}
