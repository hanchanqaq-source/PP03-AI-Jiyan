import { describe, expect, it } from "vitest";

import {
  PYTHON_CASEFOLD_DATA,
  PYTHON_CASEFOLD_ENTRY_COUNT,
  PYTHON_CASEFOLD_PYTHON_VERSION,
  PYTHON_CASEFOLD_SHA256,
  PYTHON_CASEFOLD_UNICODE_VERSION,
  pythonCasefold,
} from "@/lib/pythonCasefold.generated";


function generatedExpectations(): Map<number, string> {
  return new Map(PYTHON_CASEFOLD_DATA.split("\n").map((row) => {
    const [source, folded] = row.split(";");
    return [
      Number.parseInt(source, 16),
      folded.split(",").map((codePoint) => String.fromCodePoint(Number.parseInt(codePoint, 16))).join(""),
    ];
  }));
}

describe("generated Python casefold table", () => {
  it("pins the exact backend runtime provenance", () => {
    expect(PYTHON_CASEFOLD_PYTHON_VERSION).toBe("3.12.13");
    expect(PYTHON_CASEFOLD_UNICODE_VERSION).toBe("15.0.0");
    expect(PYTHON_CASEFOLD_ENTRY_COUNT).toBe(1530);
    expect(PYTHON_CASEFOLD_SHA256).toBe(
      "c2df54e2831740ed088aeecd238638042acb91e26a3ff9bd8835de831331cfce",
    );
  });

  it("matches the pinned SHA-256 of the canonical generated data", async () => {
    const digest = await globalThis.crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(PYTHON_CASEFOLD_DATA),
    );
    const hex = [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");

    expect(hex).toBe(PYTHON_CASEFOLD_SHA256);
  });

  it("matches every mapped and unmapped Unicode scalar in the pinned table", () => {
    const expected = generatedExpectations();
    expect(expected.size).toBe(PYTHON_CASEFOLD_ENTRY_COUNT);
    for (let codePoint = 0; codePoint <= 0x10ffff; codePoint += 1) {
      if (codePoint >= 0xd800 && codePoint <= 0xdfff) continue;
      const source = String.fromCodePoint(codePoint);
      const folded = expected.get(codePoint) ?? source;
      if (pythonCasefold(source) !== folded) {
        throw new Error(`casefold mismatch at U+${codePoint.toString(16).toUpperCase()}`);
      }
    }
  });

  it("covers the reviewed underfold and non-NFKC boundaries explicitly", () => {
    expect(pythonCasefold("ᲀ")).toBe("в");
    expect(pythonCasefold("ͅ")).toBe("ι");
    expect(pythonCasefold("Ａ")).toBe("ａ");
    expect(pythonCasefold("Ａ")).not.toBe(pythonCasefold("A"));
    expect(pythonCasefold("¹")).not.toBe(pythonCasefold("1"));
  });
});
