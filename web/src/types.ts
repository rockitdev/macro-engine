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

// ── Coaching ────────────────────────────────────────────────────────────────

export type Activity = "sedentary" | "light" | "moderate" | "very" | "extra"

export type Profile = {
  sex: "male" | "female" | null
  birth_year: number | null
  birth_date: string | null
  height_cm: number | null
  activity: Activity
  resistance_training: boolean
  daily_activity: "seated" | "on_feet" | "physical" | null
  strength_days_per_week: number | null
  strength_minutes_per_session: number | null
  goal: "cut" | "maintain" | "reverse" | null
  goal_loss_kg: number | null
  carb_share: number
  adjust_with_activity: boolean
  units: "kg" | "lb"
}

export type WeekTrend = {
  start: string
  end: string
  n: number
  avg_kg: number | null
  avg_lb: number | null
  change_kg: number | null
  change_pct: number | null
  waist_cm: number | null
  hips_cm: number | null
  body_fat_pct: number | null
}

export type MacroTargets = {
  kcal: number
  protein_g: number
  carb_g: number
  fat_g: number
  fiber_g: number | null
}

export type Phase = {
  id: number
  kind: "cut" | "maintenance" | "reverse" | "diet_break"
  start_date: string
  week_number: number
  params: Record<string, unknown> & { rate_pct?: number; pace?: string }
}

export type Measured = {
  kcal: number | null
  confidence: "none" | "low" | "medium" | "high"
  reason?: string
  avg_intake_kcal?: number
  rate_kg_per_week?: number
  logging_coverage?: number
}

export type Predicted = { bmr: number; equation: string; kcal: number } | null

export type Adherence = {
  days: number
  logged_days: number
  logging_coverage: number
  kcal_compliance: number | null
  protein_compliance: number | null
  weekend_drift_kcal?: number
}

export type CheckIn = {
  date: string
  status: string
  action: string
  reasons: string[]
  advice: string[]
  proposed_targets: MacroTargets | null
  adjust_pct: number | null
  activity_pct: number | null
  applied: boolean
  this_week?: { avg_kg: number; change_kg: number; change_pct: number }
  goal_loss_kg?: number
  loss_kg?: number
  activity?: { notes: string[]; note: string }
}

export type WorkoutCategory = "strength" | "cardio" | "other"

export type Workout = {
  id: number
  identity_basis: "external" | "derived" | "manual"
  external_id: string | null
  source: string
  type: string
  category: WorkoutCategory
  local_date: string
  start_at: string | null
  end_at: string | null
  duration_seconds: number
  duration_basis: "recorded" | "elapsed" | "manual"
  minutes: number
  note: string | null
  captured_at: string | null
  received_at: string
  updated_at: string
}

export type ActivityDay = {
  date: string
  partial: boolean
  future: boolean
  steps: number | null
  steps_captured_at: string | null
  steps_queried: boolean
  resting_hr_bpm: number | null
  resting_hr_sample_at: string | null
  workouts: number
}

export type ActivityWeek = {
  start: string
  end: string
  closed_days: number
  steps_observed_days: number
  steps_avg: number | null
  steps_queried_days: number
  resting_hr_avg: number | null
  resting_hr_days: number
  workouts: Record<WorkoutCategory, { count: number; minutes: number }>
  workout_days: number
  duration_bases: string[]
  manual_workouts: number
}

export type DateRange = { start: string; end: string; days: number }

export type ActivityFreshness = {
  last_query_captured_at: string | null
  last_query_received_at: string | null
  last_attempt_status: string | null
  last_observation_date: string | null
  last_observation_saved_at: string | null
}

export type ActivitySync = {
  mode: "normal" | "catch_up"
  window_days: number
  today: string
  timezone: string
  catch_up: DateRange[]
  backlog_beyond_limit: DateRange[]
  backlog_note: string | null
  last_request: {
    received_at: string
    captured_at: string
    outcome: "updated" | "no_new_readings" | "partial"
    notice: string
    partial_reasons: string[]
    observations_saved: number
    warnings: string[]
  } | null
}

export type ActivitySummary = {
  date: string
  today: string
  timezone: string
  timezone_note: string
  source: {
    selected: string | null
    selection: "auto" | "user" | null
    policy: string
    label: string
    seen: { source: string; days: number; last_date: string }[]
    history: { source: string; selection: string; at: string; previous: string | null }[]
  }
  today_steps: { date: string; value: number; so_far: true; captured_at: string } | null
  days: ActivityDay[]
  weeks: ActivityWeek[]
  workouts: Workout[]
  plan: { strength_days_per_week: number | null; strength_minutes_per_session: number | null }
  freshness: Record<"steps" | "resting_heart_rate" | "workouts", ActivityFreshness>
  sync: ActivitySync
  capabilities: { steps: string; resting_heart_rate: string; workouts: string; workouts_note: string }
  manual_workout_types: string[]
  notes: string[]
}

export type Progress = {
  activity: ActivitySummary
  apple_health_sync?: { last_sync_at: string; preserved_manual_fields: number } | null
  date: string
  profile: Profile
  body: {
    weight_kg: number | null
    body_fat_pct: number | null
    lbm_kg: number | null
    bmi: number | null
    missing: string[]
  }
  phase: Phase | null
  targets: Targets | null
  weeks: WeekTrend[]
  daily: { date: string; weight_kg: number | null; body_fat_pct: number | null; waist_cm: number | null }[]
  maintenance: { measured: Measured; predicted: Predicted; vs_predicted_pct: number | null; gate?: string }
  adherence_14d: Adherence
  phase_change_kg?: number
  last_checkin: (CheckIn & { payload: { reasons: string[] } }) | null
}

export type Plan = {
  goal: string
  targets: MacroTargets
  maintenance: { kcal: number; source: string }
  deficit?: { rate_pct: number; kg_per_week: number; kcal_per_day: number }
  protein_basis: string
  timeline?: { diet_weeks: number; end_weight_kg: number }
  warnings: string[]
  notes: string[]
}
