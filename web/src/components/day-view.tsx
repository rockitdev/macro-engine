import type { AmountChange } from "@/components/amount-sheet"
import { Composer } from "@/components/composer"
import { LogList } from "@/components/log-list"
import { MacroRing } from "@/components/macro-ring"
import { WeekSidebar } from "@/components/week-sidebar"
import { formatGrams, formatKcal } from "@/lib/format"
import { type Meal } from "@/lib/meals"
import type { Day, Entry, Food, Recipe, Week } from "@/types"

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
  onDuplicate,
  onAmount,
  onKitchen,
  onBulkMove,
  onBulkDuplicate,
  onBulkRemove,
  onSaveRecipe,
  onCopyDay,
  onSelectStart,
  onEditRecipe,
  listKey,
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
  onDuplicate: (id: number) => void
  onAmount: (id: number, change: AmountChange) => Promise<boolean>
  onKitchen: (entry: Entry, inKitchen: boolean) => void
  onBulkMove: (ids: number[], meal: Meal) => Promise<boolean>
  onBulkDuplicate: (ids: number[]) => Promise<boolean>
  onBulkRemove: (ids: number[]) => Promise<boolean>
  onSaveRecipe: (entries: Entry[]) => void
  onCopyDay: () => void
  onSelectStart: () => void
  onEditRecipe: (recipe: Recipe | number) => void
  /** Bumped to drop the log's selection (after a recipe is saved from it). */
  listKey: number
  onWeekStart: (day: string) => Promise<void>
  onSelectDay: (date: string) => void
}) {
  const hadFoodIds = new Set(
    day.entries.filter((e) => e.meal === meal && e.food_id != null).map((e) => e.food_id as number),
  )
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
            stamp={day}
            hadFoodIds={hadFoodIds}
            onEditRecipe={onEditRecipe}
          />
        </section>

        {/* Eaten list */}
        <LogList
          key={`${date}:${listKey}`}
          entries={day.entries}
          pendingId={pendingId}
          onRemove={onRemove}
          onMove={onMove}
          onDuplicate={onDuplicate}
          onAmount={onAmount}
          onKitchen={onKitchen}
          onBulkMove={onBulkMove}
          onBulkDuplicate={onBulkDuplicate}
          onBulkRemove={onBulkRemove}
          onSaveRecipe={onSaveRecipe}
          onCopyDay={onCopyDay}
          onSelectStart={onSelectStart}
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
