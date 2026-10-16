export type Macros = {
  kcal: number
  protein_g: number
  carb_g: number
  fat_g: number
  fiber_g: number
}

export type Portion = {
  label: string
  grams: number
}

export type Food = {
  id: number
  name: string
  brand: string | null
  store: string | null
  source: string
  verified: number
  kcal: number | null
  protein_g: number | null
  carb_g: number | null
  fat_g: number | null
  fiber_g: number | null
  matched_alias?: boolean
  alias_default_grams?: number | null
  portions?: Portion[]
}

export type Targets = {
  kcal: number
  protein_g: number
  carb_g: number
  fat_g: number
  fiber_g: number | null
  effective_date: string
}

export type Entry = {
  log_id: number
  ts: string
  food_id: number | null
  qty_desc: string | null
  grams: number | null
  kcal: number
  protein_g: number
  carb_g: number
  fat_g: number
  fiber_g: number
  estimated: boolean
  meal: string | null
  name: string
}

export type Day = {
  date: string
  entries: Entry[]
  eaten: Macros & { entries: number }
  targets: Targets | null
  remaining: {
    kcal: number | null
    protein_g: number | null
    carb_g: number | null
    fat_g: number | null
    fiber_g: number | null
  } | null
  note?: string
}

export type Candidate = {
  food_id: number
  name: string
  source: string
}

export type Problem = {
  reason: string
  candidates?: Candidate[]
  available_portions?: string[]
}

export type MealResult = {
  date: string
  logged: { log_id: number; name: string }[]
  problems: Problem[]
}

export type Bank = {
  kcal: number
  carb_g: number
  fat_g: number
}

export type WeekDay = {
  date: string
  eaten: Macros & { entries: number }
  targets: Targets | null
  under: {
    kcal: number | null
    protein_g: number | null
    carb_g: number | null
    fat_g: number | null
    fiber_g: number | null
  } | null
}

export type Week = {
  start: string
  end: string
  week_starts_on: string
  focus: string
  today: string
  days: WeekDay[]
  saved: Bank | null
  room_today: Bank | null
  week: {
    target: {
      kcal: number | null
      protein_g: number | null
      carb_g: number | null
      fat_g: number | null
      fiber_g: number | null
    }
    eaten: Macros
    left: {
      kcal: number | null
      protein_g: number | null
      carb_g: number | null
      fat_g: number | null
      fiber_g: number | null
    }
  }
}

export type PeriodTotals = Week["week"]

export type Month = {
  start: string
  end: string
  focus: string
  today: string
  week_starts_on: string
  weeks: Week[]
  month: PeriodTotals
  finished: PeriodTotals | null
  through_today: PeriodTotals | null
}

// ── fit_meal ────────────────────────────────────────────────────────────────

export type FitMacroBudget = {
  kcal: number | null
  protein_g: number | null
  carb_g: number | null
  fat_g: number | null
}

export type FitMacros = {
  kcal: number
  protein_g: number
  carb_g: number
  fat_g: number
}

export type FitItemRole = "main" | "side" | "drink" | "other"

export type FitItem = {
  name: string
  portion: string
  grams: number
  macros: FitMacros
  role: FitItemRole
}

export type FitOrder = {
  rank: number
  items: FitItem[]
  total: FitMacros
  fit_score: number
}

export type FitOverBudgetItem = {
  name: string
  portion: string
  grams: number
  macros: FitMacros
  blows: string[]
}

export type FitMealResult = {
  restaurant: string
  budget: FitMacroBudget
  per_meal_target: FitMacroBudget
  meals_left: number
  orders: FitOrder[]
  over_budget: FitOverBudgetItem[]
  treats_excluded: boolean
  note?: string
}

export type BarcodeFood = {
  name: string
  brand: string | null
  kcal: number | null
  protein_g: number | null
  carb_g: number | null
  fat_g: number | null
  fiber_g: number | null
  portion_label: string | null
  portion_grams: number | null
  barcode: string
  off_found: true
}

export type NewFood = {
  name: string
  kcal: number
  protein_g: number
  carb_g: number
  fat_g: number
  fiber_g?: number | null
  brand?: string | null
  store?: string | null
  portion_label?: string | null
  portion_grams?: number | null
  macros_are_per_portion?: boolean
  alias?: string | null
}
