import { describe, it, expect } from "vitest";
import { MODULES, MODULE_BY_KEY } from "../modules";

describe("MODULES nav list", () => {
  it("includes both bot dashboards with their routes", () => {
    const bot = MODULES.find((m) => m.key === "bot");
    expect(bot?.label).toBe("WaveBot");
    expect(bot?.path).toBe("/bot");

    const majors = MODULES.find((m) => m.key === "majorsbot");
    expect(majors?.label).toBe("MajorsBot");
    expect(majors?.path).toBe("/majorsbot");
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
