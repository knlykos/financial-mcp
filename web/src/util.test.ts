import { describe, expect, test } from "bun:test";
import { isMonth, monthRange, safeNext } from "./util";

describe("monthRange", () => {
  test("regular month", () => expect(monthRange("2026-10")).toEqual(["2026-10-01", "2026-11-01"]));
  test("december rolls over the year", () => expect(monthRange("2026-12")).toEqual(["2026-12-01", "2027-01-01"]));
});

describe("isMonth", () => {
  test.each(["2026-01", "2026-12"])("accepts %s", (m) => expect(isMonth(m)).toBe(true));
  test.each(["2026-13", "2026-1", "26-10", "", "2026-10-01", "'; DROP"])("rejects %p", (m) => expect(isMonth(m)).toBe(false));
  test("rejects undefined", () => expect(isMonth(undefined)).toBe(false));
});

describe("safeNext", () => {
  test("keeps a local path with its query", () => expect(safeNext("/movimientos?mes=2026-10")).toBe("/movimientos?mes=2026-10"));
  test.each(["//evil.com", "/\\evil.com", "https://evil.com", "evil.com", "", undefined])(
    "falls back to / for %p",
    (n) => expect(safeNext(n)).toBe("/"),
  );
});
