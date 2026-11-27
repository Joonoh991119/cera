// node --test tests/mathsha1.test.mjs -- the same file name even over http (LAN) where crypto.subtle is unavailable
import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { sha1js } from "../src/math.js";

test("JS SHA-1 이 표준 SHA-1 과 같다(빈 글 · 짧은 글 · 블록 경계 · 한글 · 긴 SVG)", () => {
  const cases = ["", "abc", "a".repeat(55), "a".repeat(56), "a".repeat(64), "수식 x^2 가나다", "<svg>" + "M0 0L1 1".repeat(5000) + "</svg>"];
  for (const s of cases) {
    const want = createHash("sha1").update(s, "utf8").digest("hex");
    assert.equal(sha1js(new TextEncoder().encode(s)), want, `길이 ${s.length}`);
  }
});
