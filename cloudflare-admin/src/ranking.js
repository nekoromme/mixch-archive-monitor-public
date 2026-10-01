/** 日別の到達順位から、月・年・全期間の日数ランキングを作ります。 */
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const validDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value) &&
  Number.isFinite(Date.parse(value + 'T00:00:00Z')) &&
  new Date(value + 'T00:00:00Z').toISOString().slice(0, 10) === value;

export function validateHistory(history) {
  if (!isObject(history) || history.version !== 1 || history.time_zone !== 'Asia/Tokyo' ||
      !isObject(history.profiles) || !isObject(history.days)) throw new Error('日別順位記録の形式が不正です。');
  for (const field of ['first_observed_at', 'last_observed_at']) {
    if (history[field] != null && (typeof history[field] !== 'string' || !Number.isFinite(Date.parse(history[field])))) {
      throw new Error('日別順位記録の日時が不正です。');
    }
  }
  for (const [id, profile] of Object.entries(history.profiles)) {
    if (!/^\d+$/.test(id) || !isObject(profile) || typeof profile.name !== 'string') {
      throw new Error('日別順位記録の配信者情報が不正です。');
    }
  }
  for (const [day, record] of Object.entries(history.days)) {
    if (!validDate(day) || !isObject(record) || !isObject(record.users) ||
        !Number.isInteger(record.observations) || record.observations < 1) {
      throw new Error('日別順位記録の日付や観測情報が不正です。');
    }
    for (const [id, mask] of Object.entries(record.users)) {
      if (!Object.hasOwn(history.profiles, id) || !Number.isInteger(mask) || mask < 1 || mask > 7) {
        throw new Error('日別順位記録の順位が不正です。');
      }
    }
    if (record.momentum_complete !== undefined && typeof record.momentum_complete !== 'boolean') {
      throw new Error('勢い記録の観測情報が不正です。');
    }
    const momentum = record.momentum ?? {};
    if (!isObject(momentum) || record.momentum === null) throw new Error('日別順位記録の勢いが不正です。');
    for (const [id, byRank] of Object.entries(momentum)) {
      if (!Object.hasOwn(record.users, id) || !isObject(byRank)) throw new Error('日別順位記録の勢いが不正です。');
      for (const [rank, value] of Object.entries(byRank)) {
        if (!/^[123]$/.test(rank) || !Number.isSafeInteger(value) || value < 0 ||
            !(record.users[id] & (1 << (Number(rank) - 1)))) throw new Error('日別順位記録の順位別勢いが不正です。');
      }
    }
  }
}

export function emptyHistory() {
  return { version: 1, time_zone: 'Asia/Tokyo', first_observed_at: null,
    last_observed_at: null, profiles: {}, days: {} };
}

export function parseFilters(params, now = new Date()) {
  const today = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit'}).format(now);
  const mode = params.get('mode') || 'month';
  const year = params.get('year') || today.slice(0, 4);
  const month = params.get('month') || today.slice(5, 7);
  if (!['month', 'year', 'all'].includes(mode) || !/^\d{4}$/.test(year) ||
      Number(year) < 1900 || !/^(0?[1-9]|1[0-2])$/.test(month)) {
    throw new Error('集計期間が不正です。');
  }
  const selected = params.has('ranks') ? params.get('ranks') : '1,2,3';
  const ranks = selected === '' ? [] : [...new Set(selected.split(',').map(value => {
    if (!/^[123]$/.test(value)) throw new Error('集計する順位が不正です。');
    return Number(value);
  }))].sort();
  const minimum = params.get('minMomentum') ?? '0';
  if (!/^\d+$/.test(minimum) || !Number.isSafeInteger(Number(minimum))) {
    throw new Error('勢いの下限は0以上の整数で指定してください。');
  }
  return { mode, year, month: month.padStart(2, '0'), ranks, minMomentum:Number(minimum) };
}

export function summarize(history, filters) {
  validateHistory(history);
  const { mode, year, month, ranks, minMomentum = 0 } = filters;
  const selectedMask = ranks.reduce((mask, rank) => mask | (1 << (rank - 1)), 0);
  const days = Object.entries(history.days).sort(([left], [right]) => left.localeCompare(right));
  const matchingDays = days.filter(([day]) => mode === 'all' ||
    (mode === 'year' ? day.startsWith(year + '-') : day.startsWith(year + '-' + month + '-')));
  const totals = new Map();
  let momentumIncompleteDays = 0;
  for (const [day, record] of matchingDays) {
    let incomplete = record.momentum_complete === false && ranks.length > 0;
    for (const [id, mask] of Object.entries(record.users)) {
      if (!(mask & selectedMask)) continue;
      const reachedRanks = ranks.filter(rank => mask & (1 << (rank - 1)));
      const momentum = record.momentum?.[id] || {};
      if (reachedRanks.some(rank => !Object.hasOwn(momentum, rank))) incomplete = true;
      // 勢いは、その順位にいた時の値で判定します。別順位の最大値は使いません。
      // 0は制限なし。勢いを保存する前の記録も、従来どおり数えます。
      const qualifyingRanks = reachedRanks.filter(rank => minMomentum === 0 || momentum[rank] >= minMomentum);
      if (!qualifyingRanks.length) continue;
      const row = totals.get(id) || { id, name: history.profiles[id].name,
        profileUrl: 'https://mixch.tv/u/' + id, days: 0, rankDays: {1: 0, 2: 0, 3: 0}, lastSeenDay: day };
      // その日に選択した順位のどれかへ入ったら1日。順位別の日数の和にはしません。
      row.days++;
      for (const rank of qualifyingRanks) row.rankDays[rank]++;
      row.lastSeenDay = day;
      totals.set(id, row);
    }
    if (incomplete) momentumIncompleteDays++;
  }
  const rows = [...totals.values()].sort((left, right) => right.days - left.days ||
    left.name.localeCompare(right.name, 'ja') || left.id.localeCompare(right.id, 'en', {numeric: true}));
  let placement = 0;
  rows.forEach((row, index) => {
    if (index === 0 || rows[index - 1].days !== row.days) placement = index + 1;
    row.placement = placement;
  });
  return { ...filters, minMomentum, rows, meta: { firstObservedAt: history.first_observed_at,
    lastObservedAt: history.last_observed_at, totalObservedDays: days.length,
    periodObservedDays: matchingDays.length,
    momentumIncompleteDays,
    years: [...new Set([year, ...days.map(([day]) => day.slice(0, 4))])].sort().reverse(),
    firstDay: days[0]?.[0] || null, lastDay: days.at(-1)?.[0] || null } };
}
