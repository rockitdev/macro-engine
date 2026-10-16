import type { BarcodeFood, Day, FitMacroBudget, FitMealResult, Food, MealResult, Month, NewFood, Targets, Week } from "./types"

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err
    throw new ApiError(
      "The tracker isn't running. Start it with python -m macro_engine.http_api",
    )
  }
  const text = await response.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      throw new ApiError(`Unexpected response (${response.status})`)
    }
  }
  if (!response.ok) {
    const message =
      data && typeof data === "object" && "error" in data && typeof data.error === "string"
        ? data.error
        : `Request failed (${response.status})`
    throw new ApiError(message)
  }
  return data as T
}

export function getDay(date: string) {
  return request<Day>(`/api/day?date=${encodeURIComponent(date)}`)
}

export function getWeek(date: string) {
  return request<Week>(`/api/week?date=${encodeURIComponent(date)}`)
}

export function getMonth(date: string) {
  return request<Month>(`/api/month?date=${encodeURIComponent(date)}`)
}

export function getSettings() {
  return request<{ week_starts_on: string; meals_per_day: number }>("/api/settings")
}

export function setWeekStart(day: string) {
  return request<{ week_starts_on: string }>("/api/settings", {
    method: "POST",
    body: JSON.stringify({ week_starts_on: day }),
  })
}

export function setMealsPerDay(n: number) {
  return request<{ meals_per_day: number }>("/api/settings", {
    method: "POST",
    body: JSON.stringify({ meals_per_day: n }),
  })
}

export function searchFoods(query: string, signal?: AbortSignal) {
  const params = new URLSearchParams({ q: query, limit: "8" })
  return request<Food[]>(`/api/foods?${params}`, { signal })
}

export function getFood(id: number) {
  return request<Food>(`/api/foods/${id}`)
}

export function logMeal(body: {
  date: string
  meal?: string | null
  raw_text?: string
  items: Record<string, unknown>[]
}) {
  return request<MealResult>("/api/meals", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export function deleteLog(id: number) {
  return request<{ deleted: boolean }>(`/api/log/${id}`, { method: "DELETE" })
}

export function setLogMeal(id: number, meal: string | null) {
  return request<{ log_id: number; meal: string | null }>(`/api/log/${id}`, {
    method: "POST",
    body: JSON.stringify({ meal }),
  })
}

export function setTargets(body: {
  kcal: number
  protein_g: number
  carb_g: number
  fat_g: number
  fiber_g: number | null
  effective_date: string
}) {
  return request<Targets>("/api/targets", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export function addFood(body: NewFood) {
  return request<{ food_id: number }>("/api/foods", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export function addAlias(body: {
  phrase: string
  food_id: number
  default_grams?: number | null
}) {
  return request<{ phrase: string }>("/api/aliases", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export function getChains() {
  return request<{ chains: string[] }>("/api/chains")
}

export function lookupBarcode(code: string) {
  return request<BarcodeFood>(`/api/barcode/${encodeURIComponent(code)}`)
}

export function fitMeal(body: {
  restaurant: string
  remaining?: FitMacroBudget | null
  date?: string
  meals_per_day?: number | null
  meals_left?: number | null
  include_treats?: boolean
}) {
  return request<FitMealResult>("/api/fit-meal", {
    method: "POST",
    body: JSON.stringify(body),
  })
}
