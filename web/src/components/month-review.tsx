import { formatSpan } from "@/lib/format"
import { monthLines, weekLines } from "@/lib/week"
import type { Month } from "@/types"

import { DayList } from "@/components/day-list"
import { WeekStartSelect } from "@/components/week-start"

export function MonthReview({
  month,
  onSelect,
  onWeekStart,
}: {
  month: Month
  onSelect: (date: string) => void
  onWeekStart: (day: string) => Promise<void>
}) {
  const lines = monthLines(month)

  return (
    <section className="mt-8" aria-label="This month">
      <WeekStartSelect value={month.week_starts_on} onWeekStart={onWeekStart} />
      <div className="mt-4 grid gap-2">
        {lines.map((line) => (
          <p
            key={line}
            className={line.startsWith("Protein") ? "text-sm text-muted-foreground" : undefined}
          >
            {line}
          </p>
        ))}
      </div>
      {month.weeks.map((week) => (
        <section key={week.start} className="mt-8" aria-label={formatSpan(week.start, week.end)}>
          <h2 className="font-medium">{formatSpan(week.start, week.end)}</h2>
          <div className="mt-2 grid gap-2">
            {weekLines(week).map((line) => (
              <p
                key={line}
                className={line.startsWith("Protein") ? "text-sm text-muted-foreground" : "text-sm"}
              >
                {line}
              </p>
            ))}
          </div>
          <DayList
            days={week.days}
            today={month.today}
            focus={month.focus}
            monthStart={month.start}
            monthEnd={month.end}
            onSelect={onSelect}
          />
        </section>
      ))}
    </section>
  )
}
