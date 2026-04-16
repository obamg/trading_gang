import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { Badge } from "../Badge";

describe("<Badge>", () => {
  it("renders children text", () => {
    const { getByText } = render(<Badge variant="bullish">STRONG LONG</Badge>);
    expect(getByText("STRONG LONG")).toBeInTheDocument();
  });

  it("applies distinct variant classes", () => {
    const { container: bull } = render(<Badge variant="bullish">A</Badge>);
    const { container: bear } = render(<Badge variant="bearish">B</Badge>);
    expect(bull.firstChild).not.toBeNull();
    expect(bear.firstChild).not.toBeNull();
    // The two variants should produce different class strings
    const bullCls = (bull.firstChild as HTMLElement).className;
    const bearCls = (bear.firstChild as HTMLElement).className;
    expect(bullCls).not.toBe(bearCls);
  });
});
