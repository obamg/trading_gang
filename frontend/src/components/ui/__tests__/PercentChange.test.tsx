import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { PercentChange } from "../PercentChange";

describe("<PercentChange>", () => {
  it("renders a positive percent with + sign and green class", () => {
    const { container } = render(<PercentChange value={2.5} />);
    const span = container.querySelector("span")!;
    expect(span.className).toContain("text-profit");
    expect(span.textContent).toBe("+2.50%");
  });

  it("renders a negative percent with minus and red class", () => {
    const { container } = render(<PercentChange value={-3.14} />);
    const span = container.querySelector("span")!;
    expect(span.className).toContain("text-loss");
    expect(span.textContent).toBe("\u22123.14%");
  });

  it("handles missing values with em-dash", () => {
    const { container } = render(<PercentChange value={null} />);
    expect(container.textContent).toBe("—");
  });
});
