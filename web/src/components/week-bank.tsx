import { formatLong, weekdayShort } from "@/lib/format"
import { carbDayLabel, weekLines } from "@/lib/week"
import type { Week } from "@/types"

import { WeekStartSelect } from "@/components/week-start"

export function WeekBank({
  week,
  onSelect,
  onWeekStart,
}: {
  week: Week
  onSelect: (date: string) => void
  onWeekStart: (day: string) => Promise<void>
}) {
  const lines = weekLines(week)

  return (
    <section className="mt-8" aria-label="This week">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
        <p className="font-medium">
          {formatLong(week.start)} to {formatLong(week.end)}
        </p>
        <WeekStartSelect value={week.week_starts_on} onWeekStart={onWeekStart} />
      </div>
      <div className="mt-4 grid max-w-[40rem] gap-2">
        {lines.map((line) => (
          <p
            key={line}
            className={line.startsWith("Protein") ? "text-sm text-muted-foreground" : undefined}
          >
            {line}
          </p>
        ))}
      </div>
      <ol className="mt-6 grid list-none grid-cols-7 gap-1 p-0" aria-label="Carbs eaten each day">
        {week.days.map((day) => {
          const future = day.date > week.today
          const goal = day.targets?.carb_g
          const showTrack = !future && goal != null
          const over = goal != null && day.eaten.carb_g > goal
          const ratio = goal != null && goal > 0 ? Math.min(1, day.eaten.carb_g / goal) : over ? 1 : 0
          const selected = day.date === week.focus
          const isToday = day.date === week.today
          return (
            <li key={day.date}>
              <button
                type="button"
                aria-pressed={selected}
                aria-current={isToday ? "date" : undefined}
                aria-label={carbDayLabel(day, week.today)}
                onClick={() => onSelect(day.date)}
                className="flex w-full flex-col items-center gap-2 rounded-sm py-1 outline-none hover:bg-mist focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <span
                  className={`text-xs ${
                    selected
                      ? "font-medium underline decoration-spinach decoration-2 underline-offset-4"
                      : isToday
                        ? "text-spinach"
                        : "text-muted-foreground"
                  }`}
                >
                  {weekdayShort(day.date)}
                </span>
                <span className="relative h-10 w-1.5">
                  {showTrack && <span className="absolute inset-0 rounded-sm bg-mist" />}
                  {showTrack && ratio > 0 && (
                    <span
                      className={`absolute inset-x-0 bottom-0 rounded-sm ${over ? "bg-roast" : "bg-grain"}`}
                      style={{ height: `${ratio * 100}%` }}
                    />
                  )}
                </span>
              </button>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
