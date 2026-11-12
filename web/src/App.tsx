import { useCallback, useEffect, useRef, useState } from "react"

import { ApiError, deleteLog, duplicateLog, getDay, getFood, getMonth, getWeek, setLogAmount, setLogMeal, setWeekStart } from "@/api"
import type { AmountChange } from "@/components/amount-sheet"
import { DayView } from "@/components/day-view"
import { FitMealDialog } from "@/components/fit-meal-dialog"
import { FoodDialog } from "@/components/food-dialog"
import { MonthView } from "@/components/month-view"
import { ProgressView } from "@/components/progress-view"
import { TargetsDialog } from "@/components/targets-dialog"
import { WeekView } from "@/components/week-view"
import { formatLong, formatMonth, formatSpan, isoToday, shiftDate, shiftMonth } from "@/lib/format"
import { defaultMeal, type Meal } from "@/lib/meals"
import type { Day, Food, Month, Week } from "@/types"

type Range = "day" | "week" | "month" | "progress"

// ── Icons ────────────────────────────────────────────────────────────────────
function LeafIcon() {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--me-on-green)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
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

function ForkIcon() {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2"/><path d="M7 2v20"/><path d="M21 15V2a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3Zm0 0v7"/>
    </svg>
  )
}

const TABS = [
  { id: "day", label: "Day", icon: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></> },
  { id: "week", label: "Week", icon: <><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></> },
  { id: "month", label: "Month", icon: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></> },
  { id: "progress", label: "Progress", icon: <><path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="m19 9-5 5-4-4-3 3"/></> },
] as const

export default function App() {
  const today = isoToday()
  const [date, setDate] = useState(today)
  const [mode, setMode] = useState<Range>(() =>
    (["day", "week", "month", "progress"] as const).find((r) => `#${r}` === window.location.hash) ?? "day")
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
  // Bumped whenever the app returns to the foreground; views that fetch their own data key off it.
  const [resumeCount, setResumeCount] = useState(0)
  const viewRef = useRef({ date, mode })
  useEffect(() => { viewRef.current = { date, mode } }, [date, mode])

  // Keep the range in the URL hash so views are linkable (#progress).
  useEffect(() => {
    window.history.replaceState(null, "", `#${mode}`)
  }, [mode])

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

  // An installed app has no reload button and iOS keeps it suspended, so refresh
  // whenever it comes back to the foreground (food logged through chat shows up),
  // and follow the calendar when the day has rolled over since it was last open.
  useEffect(() => {
    let lastDay = isoToday()
    let lastRefresh = Date.now()
    function onResume() {
      if (document.visibilityState !== "visible") return
      const nowDay = isoToday()
      const rolled = nowDay !== lastDay
      if (!rolled && Date.now() - lastRefresh < 5000) return
      lastRefresh = Date.now()
      setResumeCount((n) => n + 1)
      if (rolled) {
        const wasOnToday = viewRef.current.date === lastDay
        lastDay = nowDay
        if (wasOnToday) {
          setDate(nowDay) // the [date, mode] effect loads it
          return
        }
      }
      void load(viewRef.current.date, viewRef.current.mode)
    }
    document.addEventListener("visibilitychange", onResume)
    window.addEventListener("pageshow", onResume)
    return () => {
      document.removeEventListener("visibilitychange", onResume)
      window.removeEventListener("pageshow", onResume)
    }
  }, [load])

  const ready =
    mode === "day" ? day?.date === date
    : mode === "week" ? week?.focus === date
    : mode === "progress" ? true
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
      : shiftDate(current, mode === "week" || mode === "progress" ? direction * 7 : direction),
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

  async function duplicate(id: number) {
    setPendingId(id)
    try {
      await duplicateLog(id)
      await load(date, mode)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not duplicate that entry")
    } finally {
      setPendingId(null)
    }
  }

  async function changeAmount(id: number, change: AmountChange) {
    setPendingId(id)
    try {
      await setLogAmount(id, change)
      await load(date, mode)
      return true
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change that amount")
      return false
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
    <div className="app-shell" style={{ fontFamily: "var(--font-sans)", color: "var(--me-ink)", background: "var(--me-bg)" }}>
      {/* ── Header ── */}
      <header className="app-header" style={{ borderBottom: "1px solid var(--me-line)", background: "var(--me-bar)", position: "sticky", top: 0, zIndex: 40 }}>
        <div className="app-header-inner" style={{ maxWidth: 1120, margin: "0 auto", padding: "12px 24px", display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
          {/* Logo */}
          <div className="app-brand" style={{ display: "flex", alignItems: "center", gap: 10, fontWeight: 600, fontSize: 15 }}>
            <span style={{ width: 28, height: 28, borderRadius: 8, background: "var(--me-green)", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
              <LeafIcon />
            </span>
            Macro Engine
          </div>

          {/* Range nav */}
          <nav aria-label="Range" className="pill-nav range-nav" style={{ marginLeft: 12 }}>
            {(["day", "week", "month", "progress"] as const).map((id) => (
              <button
                key={id}
                type="button"
                className="pill-nav-item"
                aria-selected={mode === id}
                onClick={() => setMode(id)}
              >
                {id === "day" ? "Day" : id === "week" ? "Week" : id === "month" ? "Month" : "Progress"}
              </button>
            ))}
          </nav>

          <div style={{ flex: 1 }} />

          {/* Eating out */}
          <button
            type="button"
            onClick={() => setFitMealOpen(true)}
            className="hdr-btn"
            aria-label="Eating out?"
            style={{ font: "inherit", fontSize: 14, fontWeight: 500, height: 36, padding: "0 14px", borderRadius: 9, border: "1px solid var(--me-line)", background: "var(--me-surface)", color: "var(--me-ink)", display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}
          >
            <span className="hdr-icon-only"><ForkIcon /></span>
            <span className="hdr-label">Eating out?</span>
          </button>

          {/* Targets */}
          <button
            type="button"
            onClick={() => setTargetsOpen(true)}
            className="hdr-btn"
            aria-label="Targets"
            style={{ font: "inherit", fontSize: 14, fontWeight: 500, height: 36, padding: "0 14px", borderRadius: 9, border: "1px solid var(--me-line)", background: "var(--me-surface)", color: "var(--me-ink)", display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}
          >
            <TargetIcon />
            <span className="hdr-label">Targets</span>
          </button>

          {/* New food */}
          <button
            type="button"
            onClick={() => { setFoodName(""); setFoodOpen(true) }}
            className="hdr-btn"
            aria-label="New food"
            style={{ font: "inherit", fontSize: 14, fontWeight: 500, height: 36, padding: "0 14px", borderRadius: 9, border: "1px solid var(--me-green)", background: "var(--me-green)", color: "var(--me-on-green)", display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}
          >
            <PlusIcon />
            <span className="hdr-label">New food</span>
          </button>
        </div>
      </header>

      {/* ── Main ── */}
      <main className="app-main" style={{ maxWidth: 1120, margin: "0 auto", padding: "28px 24px 64px" }}>

        {/* Date header */}
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <button
              type="button"
              aria-label={`Previous ${mode}`}
              onClick={() => step(-1)}
              className="tap" style={{ width: 36, height: 36, padding: 0, borderRadius: 9, border: "1px solid transparent", background: "transparent", color: "var(--me-ink)", display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}
            >
              <ChevLeft />
            </button>
            <div>
              {showingToday && mode === "day" && (
                <div style={{ fontSize: 12, color: "var(--me-muted)", textTransform: "uppercase", letterSpacing: ".06em" }}>Today</div>
              )}
              <h1 className="range-title" style={{ margin: 0, fontSize: 24, fontWeight: 600, letterSpacing: "-.02em" }}>{rangeLabel}</h1>
            </div>
            <button
              type="button"
              aria-label={`Next ${mode}`}
              onClick={() => step(1)}
              className="tap" style={{ width: 36, height: 36, padding: 0, borderRadius: 9, border: "1px solid transparent", background: "transparent", color: "var(--me-ink)", display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}
            >
              <ChevRight />
            </button>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {!showingToday && (
              <button
                type="button"
                onClick={() => setDate(today)}
                style={{ font: "inherit", fontSize: 13, fontWeight: 500, height: 30, padding: "0 12px", borderRadius: 8, border: "1px solid var(--me-line)", background: "var(--me-surface)", color: "var(--me-ink)", cursor: "pointer" }}
              >
                Today
              </button>
            )}
            {weekPaceBadge && (mode === "day" || mode === "week") && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--me-green-text)", background: "var(--me-green-soft)", padding: "4px 10px", borderRadius: 999 }}>
                <span style={{ width: 6, height: 6, borderRadius: 99, background: "var(--me-green)" }} />
                {weekPaceBadge}
              </span>
            )}
          </div>
        </div>

        {error && (
          <p style={{ marginTop: 24, fontSize: 14, color: "var(--me-roast)" }} role="alert">{error}</p>
        )}

        <div key={mode} className="view-enter">
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
            onDuplicate={(id) => void duplicate(id)}
            onAmount={changeAmount}
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

        {/* ── Progress (coaching) view ── */}
        {mode === "progress" && (
          <ProgressView date={date} refreshKey={resumeCount} onTargetsChanged={() => void load(date, mode)} />
        )}

        </div>

        {!ready && !error && (
          <p style={{ marginTop: 32, fontSize: 14, color: "var(--me-muted)" }}>Loading the {mode}…</p>
        )}
      </main>

      {/* ── Bottom tab bar (phones) ── */}
      <nav aria-label="Range" className="tabbar">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className="tabbar-item"
            aria-current={mode === tab.id ? "page" : undefined}
            onClick={() => {
              if (mode === tab.id) window.scrollTo({ top: 0, behavior: "smooth" })
              else { setMode(tab.id); window.scrollTo({ top: 0 }) }
            }}
          >
            <svg aria-hidden="true" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={mode === tab.id ? 2.2 : 1.8} strokeLinecap="round" strokeLinejoin="round">
              {tab.icon}
            </svg>
            <span>{tab.label}</span>
          </button>
        ))}
      </nav>

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
