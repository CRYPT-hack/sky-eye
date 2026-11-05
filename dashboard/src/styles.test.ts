import { describe, expect, test } from "vitest";
import html from "../index.html?raw";
import css from "./styles.css?raw";

// Phones are at least 375 px wide: no element may force a wider box.
const PHONE_WIDTH_PX = 375;
const PX_PER_REM = 16;

interface MediaBlock {
  prelude: string;
  body: string;
  start: number;
  end: number;
}

function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Top-level `@media ... { ... }` blocks, brace-matched. */
function mediaBlocks(source: string): MediaBlock[] {
  const blocks: MediaBlock[] = [];
  let from = 0;
  for (;;) {
    const start = source.indexOf("@media", from);
    if (start === -1) return blocks;
    const open = source.indexOf("{", start);
    if (open === -1) throw new Error("unterminated @media rule");
    let depth = 1;
    let i = open + 1;
    while (i < source.length && depth > 0) {
      if (source[i] === "{") depth += 1;
      else if (source[i] === "}") depth -= 1;
      i += 1;
    }
    if (depth !== 0) throw new Error("unbalanced braces in @media rule");
    blocks.push({ prelude: source.slice(start, open).trim(), body: source.slice(open + 1, i - 1), start, end: i });
    from = i;
  }
}

function outsideMedia(source: string): string {
  let out = "";
  let from = 0;
  for (const block of mediaBlocks(source)) {
    out += source.slice(from, block.start);
    from = block.end;
  }
  return out + source.slice(from);
}

/** `width` / `min-width` / `minmax(` / `flex-basis` lengths over the phone width, outside media queries. */
function oversizedWidths(source: string): string[] {
  const code = outsideMedia(stripComments(source));
  const pattern =
    /(?<![\w-])(?:min-width|width|flex-basis)\s*:\s*(\d+(?:\.\d+)?)(px|rem|em)\b|minmax\(\s*(\d+(?:\.\d+)?)(px|rem|em)\b/g;
  const found: string[] = [];
  for (const match of code.matchAll(pattern)) {
    const value = Number(match[1] ?? match[3]);
    const unit = match[2] ?? match[4];
    const px = unit === "px" ? value : value * PX_PER_REM;
    if (px > PHONE_WIDTH_PX) found.push(match[0]);
  }
  return found;
}

function rootBlock(source: string): string {
  const match = /(?:^|[}\s]):root\s*\{([^}]*)\}/.exec(source);
  return match ? match[1] : "";
}

test("layout has viewport meta and no fixed widths over 375px", () => {
  expect(html).toContain('<meta name="viewport" content="width=device-width, initial-scale=1">');
  expect(css).toContain(":root");
  expect(oversizedWidths(css)).toEqual([]);
});

describe("width scanner", () => {
  test("flags oversized widths outside media queries", () => {
    const sample = `
      .a { width: 400px; }
      .b { min-width: 30rem; }
      .c { grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); }
      .d { flex-basis: 500px; }
    `;
    expect(oversizedWidths(sample)).toEqual([
      "width: 400px",
      "min-width: 30rem",
      "minmax(380px",
      "flex-basis: 500px",
    ]);
  });

  test("ignores media queries, max-width, comments and small widths", () => {
    const sample = `
      /* width: 900px in a comment */
      .page { max-width: 1200px; width: 100%; min-width: 0; }
      .tile { width: 375px; }
      @media (min-width: 768px) { .wide { width: 900px; } .x { min-width: 40rem; } }
    `;
    expect(oversizedWidths(sample)).toEqual([]);
  });
});

describe("index.html", () => {
  test("has the page shell the engine serves", () => {
    expect(html).toMatch(/<title>sky-eye<\/title>/);
    expect(html).toMatch(/<link rel="icon" type="image\/svg\+xml" href="data:image\/svg\+xml,/);
    expect(html).toContain('<div id="root"></div>');
    expect(html).toMatch(/<html lang="en">/);
  });

  test("loads nothing from other hosts", () => {
    expect(html).not.toMatch(/(?:src|href)\s*=\s*["']\s*(?:https?:)?\/\//i);
  });
});

describe("styles.css", () => {
  test("colours, spacing and radius are custom properties on :root", () => {
    const root = rootBlock(outsideMedia(stripComments(css)));
    for (const name of ["--bg", "--surface", "--text", "--muted", "--line", "--accent", "--space-4", "--radius-m"]) {
      expect(root).toContain(`${name}:`);
    }
  });

  test("dark theme overrides the colours under prefers-color-scheme", () => {
    const dark = mediaBlocks(stripComments(css)).filter((b) => /prefers-color-scheme:\s*dark/.test(b.prelude));
    expect(dark).toHaveLength(1);
    const root = rootBlock(dark[0].body);
    for (const name of ["--bg", "--surface", "--text", "--muted", "--line", "--accent"]) {
      expect(root).toContain(`${name}:`);
    }
  });

  test("body has an explicit background and a system font stack", () => {
    const code = outsideMedia(stripComments(css));
    const body = /(?:^|[}\s,])body\s*\{([^}]*)\}/.exec(code)?.[1] ?? "";
    expect(body).toMatch(/background(?:-color)?\s*:/);
    expect(code).toMatch(/--font:\s*system-ui/);
  });

  test("uses no web fonts and loads nothing from other hosts", () => {
    const code = stripComments(css);
    expect(code).not.toMatch(/@font-face|@import/);
    expect(code).not.toMatch(/url\(\s*["']?\s*(?:https?:)?\/\//i);
  });
});
