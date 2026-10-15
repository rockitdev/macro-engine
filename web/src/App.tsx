import { useCallback, useEffect, useState } from "react"
import { ChevronLeft, ChevronRight } from "lucide-react"

import { ApiError, deleteLog, getDay, getFood, getMonth, getWeek, setLogMeal, setWeekStart } from "@/api"
import { Composer } from "@/components/composer"
import { DayList } from "@/components/day-list"
import { FoodDialog } from "@/components/food-dialog"
import { LogList } from "@/components/log-list"
import { MonthReview } from "@/components/month-review"
import { Remaining } from "@/components/remaining"
import { TargetsDialog } from "@/components/targets-dialog"
import { WeekBank } from "@/components/week-bank"
import { Button } from "@/components/ui/button"
import { formatLong, formatMonth, formatSpan, isoToday, shiftDate, shiftMonth } from "@/lib/format"
import { defaultMeal, type Meal } from "@/lib/meals"
import type { Day, Food, Month, Week } from "@/types"

type Range = "day" | "week" | "month"

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
  const consumeSeed = useCallback(() => setSeed(null), [])

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
    return () => {
      active = false
    }
  }, [date, mode])

  const ready =
    mode === "day"
      ? day?.date === date
      : mode === "week"
        ? week?.focus === date
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

  function step(direction: number) {
    setDate((current) =>
      mode === "month" ? shiftMonth(current, direction) : shiftDate(current, mode === "week" ? direction * 7 : direction),
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
    <main className="mx-auto w-full max-w-[40rem] px-5 py-8 sm:py-12">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={`Previous ${mode}`}
            onClick={() => step(-1)}
          >
            <ChevronLeft />
          </Button>
          <p className="min-w-0 text-base font-medium">{rangeLabel}</p>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={`Next ${mode}`}
            onClick={() => step(1)}
          >
            <ChevronRight />
          </Button>
        </div>
        <div className="flex shrink-0 gap-2">
          {!showingToday && (
            <Button type="button" variant="ghost" onClick={() => setDate(today)}>
              Today
            </Button>
          )}
          <Button type="button" variant="outline" onClick={() => setTargetsOpen(true)}>
            Targets
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              setFoodName("")
              setFoodOpen(true)
            }}
          >
            New food
          </Button>
        </div>
      </header>

      <div role="tablist" aria-label="Range" className="mt-4 flex gap-5">
        {(["day", "week", "month"] as const).map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={mode === id}
            onClick={() => setMode(id)}
            className={`text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 ${
              mode === id
                ? "font-medium underline decoration-spinach decoration-2 underline-offset-4"
                : "text-muted-foreground"
            }`}
          >
            {id === "day" ? "Day" : id === "week" ? "Week" : "Month"}
          </button>
        ))}
      </div>

      {error && (
        <p className="mt-8 max-w-sm text-sm" role="alert">
          {error}
        </p>
      )}

      {mode === "day" && day && day.date === date && (
        <>
          <Remaining day={day} />
          {week && week.focus === date && (
            <WeekBank week={week} onSelect={setDate} onWeekStart={saveWeekStart} />
          )}
          <Composer
            date={date}
            meal={meal}
            onMeal={setMeal}
            seed={seed}
            onSeedConsumed={consumeSeed}
            onLogged={() => void load(date, mode)}
            onAddFood={(name) => {
              setFoodName(name)
              setFoodOpen(true)
            }}
          />
          <LogList
            entries={day.entries}
            pendingId={pendingId}
            onRemove={(id) => void remove(id)}
            onMove={(id, next) => void move(id, next)}
          />
        </>
      )}

      {mode === "week" && week && week.focus === date && (
        <>
          <WeekBank week={week} onSelect={openDay} onWeekStart={saveWeekStart} />
          <DayList days={week.days} today={week.today} focus={week.focus} onSelect={openDay} />
        </>
      )}

      {mode === "month" && month && month.focus === date && (
        <MonthReview month={month} onSelect={openDay} onWeekStart={saveWeekStart} />
      )}

      {!ready && !error && (
        <p className="mt-8 text-sm text-muted-foreground">Loading the {mode}.</p>
      )}

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
    </main>
  )
}
