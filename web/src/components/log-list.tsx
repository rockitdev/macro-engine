import { Button } from "@/components/ui/button"
import { formatGrams, formatKcal } from "@/lib/format"
import { MEALS, mealLabel, type Meal } from "@/lib/meals"
import type { Entry } from "@/types"

function sections(entries: Entry[]): { id: string; label: string; entries: Entry[] }[] {
  const known = new Set<string>(MEALS.map((meal) => meal.id))
  const grouped: { id: string; label: string; entries: Entry[] }[] = MEALS.map((meal) => ({
    id: meal.id,
    label: meal.label,
    entries: entries.filter((entry) => entry.meal === meal.id),
  })).filter((section) => section.entries.length > 0)
  const loose = entries.filter((entry) => !entry.meal || !known.has(entry.meal))
  if (loose.length > 0) {
    grouped.push({ id: "none", label: "No meal", entries: loose })
  }
  return grouped
}

export function LogList({
  entries,
  pendingId,
  onRemove,
  onMove,
}: {
  entries: Entry[]
  pendingId: number | null
  onRemove: (id: number) => void
  onMove: (id: number, meal: Meal | null) => void
}) {
  const groups = sections(entries)
  return (
    <section className="mt-10">
      <h2 className="text-base font-medium">Eaten</h2>
      {entries.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">Nothing logged yet.</p>
      ) : (
        groups.map((group) => {
          const kcal = group.entries.reduce((sum, entry) => sum + entry.kcal, 0)
          return (
            <div key={group.id} className="mt-5">
              <div className="flex items-baseline justify-between gap-3">
                <h3 className="text-sm font-medium">{group.label}</h3>
                <p className="text-sm text-muted-foreground tabular-nums">{formatKcal(kcal)} kcal</p>
              </div>
              <ul className="mt-1">
                {group.entries.map((entry) => (
                  <li
                    key={entry.log_id}
                    className="flex items-start justify-between gap-4 border-t border-border py-3"
                  >
                    <div className="min-w-0">
                      <p className="font-medium">{entry.name}</p>
                      <p className="text-sm text-muted-foreground">
                        {entry.qty_desc}
                        {entry.estimated ? " Estimate" : ""}
                      </p>
                      <p className="mt-1 text-sm text-muted-foreground tabular-nums">
                        {formatGrams(entry.protein_g)} g protein, {formatGrams(entry.carb_g)} g carbs,{" "}
                        {formatGrams(entry.fat_g)} g fat
                      </p>
                    </div>
                    <div className="shrink-0 text-right">
                      <p className="tabular-nums">{formatKcal(entry.kcal)} kcal</p>
                      <select
                        value={entry.meal ?? ""}
                        disabled={pendingId === entry.log_id}
                        aria-label={`Meal for ${entry.name}`}
                        onChange={(event) => {
                          const next = event.target.value
                          onMove(entry.log_id, next === "" ? null : (next as Meal))
                        }}
                        className="mt-1 h-7 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                      >
                          {entry.meal == null && <option value="">No meal</option>}
                          {MEALS.map((meal) => (
                            <option key={meal.id} value={meal.id}>
                              {mealLabel(meal.id)}
                            </option>
                          ))}
                      </select>
                      <Button
                        type="button"
                        variant="ghost"
                        size="xs"
                        className="mt-1"
                        disabled={pendingId === entry.log_id}
                        onClick={() => onRemove(entry.log_id)}
                      >
                        Remove
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )
        })
      )}
    </section>
  )
}
