export const MEALS = [
  { id: "breakfast", label: "Breakfast" },
  { id: "lunch", label: "Lunch" },
  { id: "dinner", label: "Dinner" },
  { id: "snacks", label: "Snacks" },
] as const

export type Meal = (typeof MEALS)[number]["id"]

export function mealLabel(id: string) {
  return MEALS.find((meal) => meal.id === id)?.label ?? id
}

export function defaultMeal(now = new Date()): Meal {
  const hour = now.getHours()
  if (hour < 11) return "breakfast"
  if (hour < 15) return "lunch"
  if (hour < 21) return "dinner"
  return "snacks"
}
