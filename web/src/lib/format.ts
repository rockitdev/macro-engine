export function isoDate(date: Date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, "0")
  const day = String(date.getDate()).padStart(2, "0")
  return `${year}-${month}-${day}`
}

export function isoToday() {
  return isoDate(new Date())
}

export function shiftDate(iso: string, days: number) {
  const [year, month, day] = iso.split("-").map(Number)
  const date = new Date(year, month - 1, day)
  date.setDate(date.getDate() + days)
  return isoDate(date)
}

const WEEKDAY_SHORT = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

export function weekdayShort(iso: string) {
  const [year, month, day] = iso.split("-").map(Number)
  const date = new Date(year, month - 1, day)
  return WEEKDAY_SHORT[(date.getDay() + 6) % 7]
}

export function weekdayName(iso: string) {
  const [year, month, day] = iso.split("-").map(Number)
  return new Intl.DateTimeFormat(undefined, { weekday: "long" }).format(
    new Date(year, month - 1, day),
  )
}

function parts(iso: string) {
  const [year, month, day] = iso.split("-").map(Number)
  return { year, month, day, date: new Date(year, month - 1, day) }
}

export function shiftMonth(iso: string, months: number) {
  const { year, month, day } = parts(iso)
  const next = new Date(year, month - 1 + months, 1)
  const last = new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate()
  next.setDate(Math.min(day, last))
  return isoDate(next)
}

export function formatSpan(start: string, end: string) {
  const first = parts(start)
  const last = parts(end)
  const month = new Intl.DateTimeFormat(undefined, { month: "long" })
  const sameYear = first.year === last.year
  const showYear = first.year !== new Date().getFullYear() || last.year !== new Date().getFullYear()
  if (sameYear && first.month === last.month) {
    return `${month.format(first.date)} ${first.day} to ${last.day}${showYear ? `, ${first.year}` : ""}`
  }
  if (sameYear) {
    return `${month.format(first.date)} ${first.day} to ${month.format(last.date)} ${last.day}${showYear ? `, ${first.year}` : ""}`
  }
  return `${month.format(first.date)} ${first.day}, ${first.year} to ${month.format(last.date)} ${last.day}, ${last.year}`
}

export function formatMonth(iso: string) {
  const { date } = parts(iso)
  return new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(date)
}

export function formatDayLabel(iso: string, withMonth = false) {
  const { day, date } = parts(iso)
  const weekday = weekdayShort(iso)
  if (!withMonth) return `${weekday} ${day}`
  const month = new Intl.DateTimeFormat(undefined, { month: "short" }).format(date)
  return `${weekday} ${month} ${day}`
}

export function formatLong(iso: string) {
  const [year, month, day] = iso.split("-").map(Number)
  const options: Intl.DateTimeFormatOptions = {
    weekday: "long",
    month: "long",
    day: "numeric",
  }
  if (year !== new Date().getFullYear()) options.year = "numeric"
  return new Intl.DateTimeFormat(undefined, options).format(new Date(year, month - 1, day))
}

export function formatKcal(value: number) {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(
    Math.round(value),
  )
}

export function formatGrams(value: number) {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(
    Math.round(value * 10) / 10,
  )
}
