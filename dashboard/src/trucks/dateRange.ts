/** Turns `<input type="date">` values (local `YYYY-MM-DD`) into an end-exclusive UTC range. */
function localMidnight(day: string, plusDays: number): string | undefined {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!m) return undefined;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + plusDays);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

export function localDayRange(
  fromDate: string | "",
  toDate: string | "",
): { from?: string; to?: string } {
  const out: { from?: string; to?: string } = {};
  const from = localMidnight(fromDate, 0);
  const to = localMidnight(toDate, 1);
  if (from) out.from = from;
  if (to) out.to = to;
  return out;
}
