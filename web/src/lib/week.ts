import { formatGrams, formatKcal, weekdayName } from "@/lib/format"
import type { Month, Week, WeekDay } from "@/types"

export const WEEK_STARTS = [
  { id: "sunday", label: "Sunday" },
  { id: "monday", label: "Monday" },
  { id: "tuesday", label: "Tuesday" },
  { id: "wednesday", label: "Wednesday" },
  { id: "thursday", label: "Thursday" },
  { id: "friday", label: "Friday" },
  { id: "saturday", label: "Saturday" },
] as const

function grams(value: number) {
  return `${formatGrams(Math.abs(value))} g`
}

export function weekLines(week: Week): string[] {
  const { carb_g: carbTarget, fat_g: fatTarget, protein_g: proteinTarget } = week.week.target
  if (carbTarget == null && fatTarget == null) {
    return ["Set a target to see what this week has saved."]
  }
  if (week.today > week.end) {
    return [
      finishedLine(week.week.left.carb_g ?? 0, week.week.left.fat_g ?? 0),
      proteinLine(week.week.eaten.protein_g, proteinTarget),
    ].filter((line): line is string => Boolean(line))
  }
  if (week.today < week.start) {
    return ["This week hasn't started."]
  }

  const lines = [
    week.saved
      ? savedLine(week.saved.carb_g, week.saved.fat_g)
      : week.today === week.start
        ? "Nothing saved yet. The week starts today."
        : "Nothing saved from earlier this week.",
  ]
  if (week.saved && week.room_today) {
    lines.push(roomLine(week, week.room_today.carb_g, week.room_today.fat_g))
  }
  const protein = proteinLine(week.week.eaten.protein_g, proteinTarget)
  if (protein) lines.push(protein)
  return lines
}

function proteinLine(eaten: number, target: number | null) {
  if (target == null) return null
  return `Protein ${formatGrams(eaten)} of ${formatGrams(target)} g this week.`
}

function savedLine(carb: number, fat: number) {
  const tail = "from earlier this week."
  if (carb === 0 && fat === 0) return `Nothing saved ${tail}`
  if (carb > 0 && fat > 0) return `${grams(carb)} of carbs and ${grams(fat)} of fat saved ${tail}`
  if (carb < 0 && fat < 0) return `${grams(carb)} of carbs and ${grams(fat)} of fat over ${tail}`
  if (carb > 0 && fat < 0) return `${grams(carb)} of carbs saved and ${grams(fat)} of fat over ${tail}`
  if (carb < 0 && fat > 0) return `${grams(carb)} of carbs over and ${grams(fat)} of fat saved ${tail}`
  if (carb > 0) return `${grams(carb)} of carbs saved ${tail}`
  if (carb < 0) return `${grams(carb)} of carbs over ${tail}`
  if (fat > 0) return `${grams(fat)} of fat saved ${tail}`
  return `${grams(fat)} of fat over ${tail}`
}

function roomLine(week: Week, carb: number, fat: number) {
  const when = week.focus === week.today ? "today" : `on ${weekdayName(week.today)}`
  if (carb > 0 && fat > 0) {
    return `You can have ${grams(carb)} of carbs and ${grams(fat)} of fat ${when} and still finish the week on target.`
  }
  if (carb > 0 && fat === 0) {
    return `You can have ${grams(carb)} of carbs ${when} and no more fat, and still finish the week on target.`
  }
  if (fat > 0 && carb === 0) {
    return `You can have ${grams(fat)} of fat ${when} and no more carbs, and still finish the week on target.`
  }
  if (carb === 0 && fat === 0) {
    return `Nothing left to spend ${when} if the rest of the week stays on target.`
  }
  if (carb < 0 && fat < 0) {
    return `Carbs are already ${grams(carb)} past the week, and fat is ${grams(fat)} past. Later days need to come in under target.`
  }
  if (carb > 0 && fat < 0) {
    return `You can have ${grams(carb)} of carbs ${when} and still finish the week on target. Fat is already ${grams(fat)} past that.`
  }
  if (fat > 0 && carb < 0) {
    return `You can have ${grams(fat)} of fat ${when} and still finish the week on target. Carbs are already ${grams(carb)} past that.`
  }
  if (carb < 0) {
    return `Carbs are already ${grams(carb)} past the week. Later days need to come in under target.`
  }
  return `Fat is already ${grams(fat)} past the week. Later days need to come in under target.`
}

function finishedLine(carb: number, fat: number, period: "week" | "month" = "week") {
  const name = period === "month" ? "This month" : "This week"
  if (carb === 0 && fat === 0) return `${name} landed on its carb and fat targets.`
  if (carb > 0 && fat > 0) {
    return `${name} finished ${grams(carb)} of carbs and ${grams(fat)} of fat under target.`
  }
  if (carb < 0 && fat < 0) {
    return `${name} finished ${grams(carb)} of carbs and ${grams(fat)} of fat over target.`
  }
  if (carb > 0 && fat < 0) {
    return `${name} finished ${grams(carb)} of carbs under target and ${grams(fat)} of fat over.`
  }
  if (carb < 0 && fat > 0) {
    return `${name} finished ${grams(carb)} of carbs over target and ${grams(fat)} of fat under.`
  }
  if (carb > 0) return `${name} finished ${grams(carb)} of carbs under target.`
  if (carb < 0) return `${name} finished ${grams(carb)} of carbs over target.`
  if (fat > 0) return `${name} finished ${grams(fat)} of fat under target.`
  return `${name} finished ${grams(fat)} of fat over target.`
}

export function monthLines(month: Month): string[] {
  const { carb_g: carbTarget, fat_g: fatTarget } = month.month.target
  if (carbTarget == null && fatTarget == null) {
    return ["Set a target to see how this month is going."]
  }
  if (month.today > month.end) {
    return [
      finishedLine(month.month.left.carb_g ?? 0, month.month.left.fat_g ?? 0, "month"),
      proteinSoFar(month.month.eaten.protein_g, month.month.target.protein_g, "this month"),
    ].filter((line): line is string => Boolean(line))
  }
  if (month.today < month.start) return ["This month hasn't started."]
  const lines = [
    month.finished
      ? soFarLine(month.finished.left.carb_g ?? 0, month.finished.left.fat_g ?? 0)
      : "No day has finished in this month yet.",
  ]
  const protein = proteinSoFar(
    month.through_today?.eaten.protein_g ?? 0,
    month.through_today?.target.protein_g ?? null,
    "so far",
  )
  if (protein) lines.push(protein)
  return lines
}

function soFarLine(carb: number, fat: number) {
  if (carb === 0 && fat === 0) return "So far this month, carbs and fat are on target."
  if (carb > 0 && fat > 0) {
    return `So far this month, ${grams(carb)} of carbs and ${grams(fat)} of fat under target.`
  }
  if (carb < 0 && fat < 0) {
    return `So far this month, ${grams(carb)} of carbs and ${grams(fat)} of fat over target.`
  }
  if (carb > 0 && fat < 0) {
    return `So far this month, ${grams(carb)} of carbs under target and ${grams(fat)} of fat over.`
  }
  if (carb < 0 && fat > 0) {
    return `So far this month, ${grams(carb)} of carbs over target and ${grams(fat)} of fat under.`
  }
  if (carb > 0) return `So far this month, ${grams(carb)} of carbs under target.`
  if (carb < 0) return `So far this month, ${grams(carb)} of carbs over target.`
  if (fat > 0) return `So far this month, ${grams(fat)} of fat under target.`
  return `So far this month, ${grams(fat)} of fat over target.`
}

function proteinSoFar(eaten: number, target: number | null, when: string) {
  if (target == null) return null
  return `Protein ${formatGrams(eaten)} of ${formatGrams(target)} g ${when}.`
}

export function dayStats(day: WeekDay, today: string): { text: string; over: boolean }[] {
  if (day.date > today) return [{ text: "Later", over: false }]
  if (!day.targets || day.under == null) {
    return [{ text: `${formatKcal(day.eaten.kcal)} kcal eaten`, over: false }]
  }
  const done = day.date < today
  const parts: { text: string; over: boolean }[] = []
  const kcalUnder = day.under.kcal
  if (kcalUnder != null && kcalUnder < 0) {
    parts.push({ text: `${formatKcal(Math.abs(kcalUnder))} kcal over`, over: true })
  } else if (day.targets.kcal != null) {
    parts.push({
      text: `${formatKcal(day.eaten.kcal)} of ${formatKcal(day.targets.kcal)} kcal`,
      over: false,
    })
  }
  const proteinUnder = day.under.protein_g
  if (proteinUnder != null && day.targets.protein_g != null) {
    parts.push({
      text:
        proteinUnder < 0
          ? `${grams(proteinUnder)} protein over`
          : `${formatGrams(day.eaten.protein_g)} of ${formatGrams(day.targets.protein_g)} g protein`,
      over: proteinUnder < 0,
    })
  }
  for (const [key, label] of [
    ["carb_g", "carbs"],
    ["fat_g", "fat"],
  ] as const) {
    const under = day.under[key]
    if (under == null) continue
    if (under === 0) {
      parts.push({ text: `${label} on target`, over: false })
      continue
    }
    parts.push({
      text: `${grams(under)} ${label} ${under > 0 ? (done ? "under" : "left") : "over"}`,
      over: under < 0,
    })
  }
  return parts
}

export function carbDayLabel(day: WeekDay, today: string) {
  const name = weekdayName(day.date)
  if (day.date > today) {
    return day.eaten.entries > 0
      ? `${name}, later this week, ${formatGrams(day.eaten.carb_g)} g carbs already logged`
      : `${name}, later this week`
  }
  const goal = day.targets?.carb_g
  if (goal == null) return `${name}, no carb target`
  if (day.eaten.carb_g > goal) {
    return `${name}, ${grams(day.eaten.carb_g - goal)} of carbs over`
  }
  return `${name}, ${formatGrams(day.eaten.carb_g)} of ${formatGrams(goal)} g carbs`
}
