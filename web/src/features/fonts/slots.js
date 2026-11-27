// Table and registration of the added font slots: pure logic (tests: web/tests/fonts.test.mjs). See the header comment of registry.js for details.
export const EXTRA = { "TB Dokdo": 21, "TB Poor Story": 22, "TB Do Hyeon": 23, "TB Gothic Bold": 24 };
export const RESERVED = [20, 39];

export function registerFontFamilies(table, extra = EXTRA) {
  const used = new Map(Object.entries(table).map(([k, v]) => [v, k]));
  for (const [name, id] of Object.entries(extra)) {
    if (id < RESERVED[0] || id > RESERVED[1]) throw new Error(`글꼴 id ${id} 는 예약 범위(20–39) 밖`);
    const owner = used.get(id);
    if (owner !== undefined && owner !== name) throw new Error(`글꼴 id ${id} 가 겹침: ${owner} / ${name}`);
    table[name] = id;
  }
  return table;
}
