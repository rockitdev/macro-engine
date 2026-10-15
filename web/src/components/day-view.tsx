import { Composer } from "@/components/composer"
import { LogList } from "@/components/log-list"
import { MacroRing } from "@/components/macro-ring"
import { WeekSidebar } from "@/components/week-sidebar"
import { formatGrams, formatKcal } from "@/lib/format"
import { type Meal } from "@/lib/meals"
import type { Day, Food, Week } from "@/types"

/**
 * Day view — two-column on desktop, single-column on mobile.
 * Left: kcal ring + macro bars, Log food panel, Eaten list
 * Right: This week sidebar card
 */
export function DayView({
  day,
  week,
  date,
  meal,
  seed,
  pendingId,
  onMeal,
  onSeedConsumed,
  onLogged,
  onAddFood,
  onRemove,
  onMove,
  onWeekStart,
  onSelectDay,
}: {
  day: Day
  week: Week
  date: string
  meal: Meal
  seed: Food | null
  pendingId: number | null
  onMeal: (m: Meal) => void
  onSeedConsumed: () => void
  onLogged: () => void
  onAddFood: (name: string) => void
  onRemove: (id: number) => void
  onMove: (id: number, next: Meal | null) => void
  onWeekStart: (day: string) => Promise<void>
  onSelectDay: (date: string) => void
}) {
  return (
    <div
      style={{
        marginTop: 24,
        display: "grid",
        gridTemplateColumns: "minmax(0, 1.55fr) minmax(0, 1fr)",
        gap: 20,
        alignItems: "start",
      }}
      className="day-view-grid"
    >
      {/* ── Left column ── */}
      <div style={{ display: "flex", flexDirection: "column", gap: 20, minWidth: 0 }}>

        {/* Macro ring + bars */}
        <MacroRing day={day} />

        {/* Log food panel */}
        <section className="me-card" style={{ padding: 20 }}>
          <Composer
            date={date}
            meal={meal}
            onMeal={onMeal}
            seed={seed}
            onSeedConsumed={onSeedConsumed}
            onLogged={onLogged}
            onAddFood={onAddFood}
          />
        </section>

        {/* Eaten list */}
        <LogList
          entries={day.entries}
          pendingId={pendingId}
          onRemove={onRemove}
          onMove={onMove}
        />
      </div>

      {/* ── Right sidebar ── */}
      <aside style={{ display: "flex", flexDirection: "column", gap: 20, minWidth: 0 }}>
        <WeekSidebar
          week={week}
          onSelect={onSelectDay}
          onWeekStart={onWeekStart}
        />
      </aside>
    </div>
  )
}

// Keep these helpers available for the ring
export { formatGrams, formatKcal }
