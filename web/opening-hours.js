// Auswertung des OSM-Tags opening_hours fuer einen konkreten Zeitpunkt.
//
// Portierung des Auswerters aus der Android-App, gleiche Regeln und gleiche
// Grenzen: was nicht sicher lesbar ist, kommt als "unknown" zurueck statt als
// geratenes Ergebnis. Feiertage werden ignoriert und das Ergebnis markiert.

const MINUTES_PER_DAY = 24 * 60;

const DAY_NAMES = {
  mo: 1, tu: 2, we: 3, th: 4, fr: 5, sa: 6, su: 0,
};

const TIME_SPAN = /(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})/;
const TIME_SPAN_ALL = /(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})/g;
const STARTS_WITH_DAY = /^(mo|tu|we|th|fr|sa|su|ph)\b/i;
const UNSUPPORTED = /\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|week|sunrise|sunset|dawn|dusk|easter)\b|\[|\|\|/i;
const CLOSED_KEYWORD = /\b(off|closed)\b/i;

export function evaluateOpeningHours(spec, at) {
  const text = (spec || '').trim();
  if (!text) return { state: 'unknown', reason: 'keine Öffnungszeiten in OSM' };
  if (text === '24/7') return { state: 'open', until: null, holidaysIgnored: false };
  if (UNSUPPORTED.test(text)) {
    return { state: 'unknown', reason: `Zeitangabe zu komplex: ${text}` };
  }

  const holidaysIgnored = /\bPH\b/.test(text);
  const rules = splitRules(text).map(parseRule).filter(Boolean);
  if (rules.length === 0) {
    return { state: 'unknown', reason: `Zeitangabe nicht lesbar: ${text}` };
  }

  const open = merge(expand(rules.filter((rule) => !rule.closed), at));
  const closed = merge(expand(rules.filter((rule) => rule.closed), at));
  const moment = at.getTime();

  const current = open.find((span) => moment >= span.start && moment < span.end);
  const blocked = closed.find((span) => moment >= span.start && moment < span.end);
  if (current && !blocked) {
    // Eine spaetere Ausnahme kann das laufende Fenster vorzeitig beenden.
    const cut = closed
      .filter((span) => span.start > moment && span.start < current.end)
      .sort((a, b) => a.start - b.start)[0];
    return {
      state: 'open',
      until: new Date(cut ? cut.start : current.end),
      holidaysIgnored,
    };
  }

  const next = open.filter((span) => span.start > moment).sort((a, b) => a.start - b.start)[0];
  return {
    state: 'closed',
    nextOpen: next ? new Date(next.start) : null,
    holidaysIgnored,
  };
}

// In echten Daten trennt sowohl ";" als auch "," Regeln, und "," trennt zugleich
// Wochentags- und Zeitlisten innerhalb einer Regel. Eine neue Regel beginnt
// deshalb erst, wenn ein Wochentag auftaucht und die bisherige schon eine Zeit hat.
function splitRules(text) {
  const rules = [];
  for (const chunk of text.split(';')) {
    let current = [];
    for (const raw of chunk.split(',')) {
      const part = raw.trim();
      if (!part) continue;
      const startsNew = STARTS_WITH_DAY.test(part) &&
        current.some((item) => TIME_SPAN.test(item) || CLOSED_KEYWORD.test(item));
      if (startsNew && current.length) {
        rules.push(current.join(','));
        current = [];
      }
      current.push(part);
    }
    if (current.length) rules.push(current.join(','));
  }
  return rules;
}

function parseRule(rule) {
  const text = rule.trim();
  if (!text) return null;
  const closed = CLOSED_KEYWORD.test(text);

  const spans = [];
  TIME_SPAN_ALL.lastIndex = 0;
  let match;
  while ((match = TIME_SPAN_ALL.exec(text)) !== null) {
    const start = Number(match[1]) * 60 + Number(match[2]);
    let end = Number(match[3]) * 60 + Number(match[4]);
    // 10:00-01:00 meint den Folgetag, 00:00-24:00 den ganzen Tag.
    if (end <= start) end += MINUTES_PER_DAY;
    spans.push([start, end]);
  }
  if (spans.length === 0 && !closed) return null;

  const firstTime = text.search(TIME_SPAN);
  const dayText = (firstTime >= 0 ? text.slice(0, firstTime) : text).replace(CLOSED_KEYWORD, '');
  let days;
  if (!dayText.trim()) {
    days = [0, 1, 2, 3, 4, 5, 6];
  } else {
    days = parseDays(dayText);
    // Bleibt nichts uebrig, war es eine reine Feiertagsregel. Die wird verworfen,
    // sonst gaelte sie faelschlich an jedem Wochentag.
    if (days.length === 0) return null;
  }
  return { days, spans: spans.length ? spans : [[0, MINUTES_PER_DAY]], closed };
}

function parseDays(text) {
  const days = new Set();
  for (const token of text.split(',').map((item) => item.trim().toLowerCase()).filter(Boolean)) {
    if (token === 'ph') continue;
    const range = token.split('-');
    if (range.length === 2) {
      const from = DAY_NAMES[range[0].slice(0, 2)];
      const to = DAY_NAMES[range[1].slice(0, 2)];
      if (from === undefined || to === undefined) continue;
      let day = from;
      for (let guard = 0; guard < 8; guard += 1) {
        days.add(day);
        if (day === to) break;
        day = (day + 1) % 7;
      }
    } else {
      const day = DAY_NAMES[token.slice(0, 2)];
      if (day !== undefined) days.add(day);
    }
  }
  return [...days];
}

// Rechnet die Regeln in konkrete Zeitfenster um, von gestern bis in acht Tage.
function expand(rules, at) {
  const spans = [];
  for (let offset = -1; offset <= 8; offset += 1) {
    const day = new Date(at.getFullYear(), at.getMonth(), at.getDate() + offset);
    for (const rule of rules) {
      if (!rule.days.includes(day.getDay())) continue;
      for (const [start, end] of rule.spans) {
        spans.push({
          start: day.getTime() + start * 60_000,
          end: day.getTime() + end * 60_000,
        });
      }
    }
  }
  return spans.sort((a, b) => a.start - b.start);
}

// Fasst aneinandergrenzende Fenster zusammen, damit "offen bis" ueber Mitternacht stimmt.
function merge(spans) {
  const merged = [];
  for (const span of spans) {
    const last = merged[merged.length - 1];
    if (last && span.start <= last.end) {
      if (span.end > last.end) last.end = span.end;
    } else {
      merged.push({ ...span });
    }
  }
  return merged;
}
