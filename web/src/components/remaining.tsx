import type { Day } from "@/types"
import { formatGrams, formatKcal } from "@/lib/format"

const ROWS = [
  { key: "protein_g", label: "Protein", fill: "bg-spinach" },
  { key: "carb_g", label: "Carbs", fill: "bg-grain" },
  { key: "fat_g", label: "Fat", fill: "bg-roast" },
  { key: "fiber_g", label: "Fiber", fill: "bg-foreground" },
] as const

export function Remaining({ day }: { day: Day }) {
  const eaten = day.eaten.kcal
  const target = day.targets?.kcal
  const left = day.remaining?.kcal
  const over = left != null && left < 0

  return (
    <section className="mt-8">
      {target == null || left == null ? (
        <>
          <h1 className="text-[clamp(3.5rem,16vw,4.75rem)] leading-none font-semibold tracking-tight tabular-nums">
            {formatKcal(eaten)}
          </h1>
          <p className="mt-2 text-muted-foreground">kcal eaten</p>
          <p className="mt-4 max-w-sm text-sm">Set a target to see what's left.</p>
        </>
      ) : (
        <>
          <h1
            className={`text-[clamp(3.5rem,16vw,4.75rem)] leading-none font-semibold tracking-tight tabular-nums ${over ? "text-roast" : ""}`}
          >
            {formatKcal(Math.abs(left))}
          </h1>
          <p className="mt-2 text-muted-foreground">{over ? "kcal over" : "kcal left"}</p>
          <p className="mt-1 text-sm text-muted-foreground tabular-nums">
            {formatKcal(eaten)} eaten of {formatKcal(target)}
          </p>
        </>
      )}

      {day.targets && day.remaining && (
        <ul className="mt-8 grid gap-4">
          {ROWS.map((row) => {
            const goal = day.targets?.[row.key]
            const have = day.eaten[row.key]
            const remain = day.remaining?.[row.key]
            if (goal == null) {
              return (
                <li key={row.key} className="flex justify-between gap-4 text-sm">
                  <span>{row.label}</span>
                  <span className="tabular-nums text-muted-foreground">
                    {formatGrams(have)} g eaten
                  </span>
                </li>
              )
            }
            const isOver = remain != null && remain < 0
            const width = goal > 0 ? Math.min(100, (have / goal) * 100) : 0
            return (
              <li key={row.key}>
                <div className="flex justify-between gap-4 text-sm">
                  <span>{row.label}</span>
                  <span className={`tabular-nums ${isOver ? "text-roast" : ""}`}>
                    {remain == null
                      ? `${formatGrams(have)} g`
                      : isOver
                        ? `${formatGrams(Math.abs(remain))} g over`
                        : `${formatGrams(remain)} g left`}
                  </span>
                </div>
                <div className="mt-1.5 h-2 overflow-hidden rounded-sm bg-mist">
                  <div
                    className={`h-full ${isOver ? "bg-roast" : row.fill}`}
                    style={{ width: `${width}%` }}
                  />
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
