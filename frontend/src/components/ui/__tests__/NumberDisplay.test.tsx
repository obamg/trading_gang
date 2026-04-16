import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { NumberDisplay } from "../NumberDisplay";

describe("<NumberDisplay>", () => {
  it("renders positive values with the profit class when coloured", () => {
    const { container } = render(<NumberDisplay value={42.5} colored />);
    const span = container.querySelector("span")!;
    expect(span.className).toContain("text-profit");
    expect(span.textContent).toBe("42.50");
  });

  it("renders negative values with the loss class and a real minus sign", () => {
    const { container } = render(<NumberDisplay value={-12.345} decimals={2} colored />);
    const span = container.querySelector("span")!;
    expect(span.className).toContain("text-loss");
    // Uses unicode \u2212, not ASCII -
    expect(span.textContent).toBe("\u221212.35");
  });

  it("renders em-dash for nullish values", () => {
    render(<NumberDisplay value={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("applies prefix, suffix and forced sign", () => {
    const { container } = render(
      <NumberDisplay value={3.2} prefix="$" suffix="M" sign />,
    );
    expect(container.textContent).toBe("+$3.20M");
  });
});
