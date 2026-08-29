const FORMULA_PREFIX = /^[\s\p{Cc}]*[=+\-@＝＋－＠]/u;

export function csvCell(value) {
  let cell = String(value ?? '');
  if (FORMULA_PREFIX.test(cell)) cell = `'${cell}`;
  return /[",\r\n]/.test(cell) || /^\s|\s$/u.test(cell) ? `"${cell.replaceAll('"', '""')}"` : cell;
}

export function toCsv(rows) {
  return rows.map((row) => row.map(csvCell).join(',')).join('\r\n');
}
