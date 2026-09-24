import { describe, it, expect } from "vitest";
import { MODULES, MODULE_BY_KEY } from "../modules";

describe("MODULES nav list", () => {

  it("no longer includes the retired WaveBot dashboard", () => {
    expect(MODULES.find((m) => m.key === "bot")).toBeUndefined();
  });

  it("has unique keys and paths", () => {
    const keys = MODULES.map((m) => m.key);
    const paths = MODULES.map((m) => m.path);
    expect(new Set(keys).size).toBe(keys.length);
    expect(new Set(paths).size).toBe(paths.length);
  });

});
