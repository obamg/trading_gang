import { describe, it, expect } from "vitest";
import { MODULES, MODULE_BY_KEY } from "../modules";

describe("MODULES nav list", () => {
  it("includes the MajorsBot dashboard with its route", () => {
    const majors = MODULES.find((m) => m.key === "majorsbot");
    expect(majors?.label).toBe("MajorsBot");
    expect(majors?.path).toBe("/majorsbot");
  });

  it("no longer includes the retired WaveBot dashboard", () => {
    expect(MODULES.find((m) => m.key === "bot")).toBeUndefined();
  });

  it("has unique keys and paths", () => {
    const keys = MODULES.map((m) => m.key);
    const paths = MODULES.map((m) => m.path);
    expect(new Set(keys).size).toBe(keys.length);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it("MODULE_BY_KEY resolves majorsbot", () => {
    expect(MODULE_BY_KEY.majorsbot.path).toBe("/majorsbot");
  });
});
