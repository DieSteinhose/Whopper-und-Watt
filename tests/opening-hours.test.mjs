import test from 'node:test';
import assert from 'node:assert/strict';
import { evaluateOpeningHours } from '../web/opening-hours.js';

// Fester Bezugstag, damit die Wochentage in den Tests eindeutig sind.
// 2026-06-01 ist ein Montag.
const MONDAY = new Date(2026, 5, 1);

function at(dayOffset, hour, minute = 0) {
  const moment = new Date(MONDAY);
  moment.setDate(MONDAY.getDate() + dayOffset);
  moment.setHours(hour, minute, 0, 0);
  return moment;
}

const MO = 0, TU = 1, WE = 2, TH = 3, FR = 4, SA = 5, SU = 6;

test('Bezugstag ist wirklich ein Montag', () => {
  assert.equal(MONDAY.getDay(), 1);
});

test('24/7 ist immer offen', () => {
  const state = evaluateOpeningHours('24/7', at(SU, 3, 30));
  assert.equal(state.state, 'open');
  assert.equal(state.until, null);
});

test('Fenster ueber Mitternacht reicht in den Folgetag', () => {
  const spec = 'Mo-Th 09:00-01:00, Fr,Sa 09:00-03:00, Su 10:00-01:00';

  const wednesday = evaluateOpeningHours(spec, at(WE, 23));
  assert.equal(wednesday.state, 'open');
  assert.equal(wednesday.until.getHours(), 1);
  assert.equal(wednesday.until.getDate(), at(TH, 1).getDate());

  const saturdayNight = evaluateOpeningHours(spec, at(SA, 2));
  assert.equal(saturdayNight.state, 'open');
  assert.equal(saturdayNight.until.getHours(), 3);

  const thursdayEarly = evaluateOpeningHours(spec, at(TH, 2));
  assert.equal(thursdayEarly.state, 'closed');
  assert.equal(thursdayEarly.nextOpen.getHours(), 9);
});

test('Zwei Fenster am Tag werden ueber Mitternacht zusammengefasst', () => {
  const spec = 'Mo-Fr 00:00-01:00,10:00-24:00; Sa,Su 00:00-03:00,10:00-24:00';
  assert.equal(evaluateOpeningHours(spec, at(MO, 0, 30)).state, 'open');

  const gap = evaluateOpeningHours(spec, at(MO, 9));
  assert.equal(gap.state, 'closed');
  assert.equal(gap.nextOpen.getHours(), 10);

  const evening = evaluateOpeningHours(spec, at(MO, 23));
  assert.equal(evening.state, 'open');
  assert.equal(evening.until.getHours(), 1);
});

test('Wochentagsbereich ueber den Sonntag hinweg', () => {
  const spec = 'Su-Th 11:00-01:00, Fr,Sa 11:00-02:00';
  assert.equal(evaluateOpeningHours(spec, at(SU, 12)).state, 'open');
  assert.equal(evaluateOpeningHours(spec, at(TU, 12)).state, 'open');
  assert.equal(evaluateOpeningHours(spec, at(SA, 23)).until.getHours(), 2);
});

test('Regel ohne Wochentag gilt jeden Tag', () => {
  assert.equal(evaluateOpeningHours('05:30-22:30', at(SU, 6)).state, 'open');
  assert.equal(evaluateOpeningHours('05:30-22:30', at(WE, 23)).state, 'closed');
});

test('Feiertagsregeln werden ignoriert und markiert', () => {
  const state = evaluateOpeningHours('Mo-Su,PH 11:00-22:00', at(SU, 12));
  assert.equal(state.state, 'open');
  assert.equal(state.holidaysIgnored, true);
  assert.equal(evaluateOpeningHours('Mo-Su 11:00-22:00', at(SU, 12)).holidaysIgnored, false);
});

test('Eine reine PH-Regel gilt nicht an Wochentagen', () => {
  // Der Fehler, den der Android-Test gefunden hatte: PH 10:00-23:00 galt taeglich.
  const spec = 'Mo-Th 10:00-22:00; Fr 10:00-23:00, Sa 10:00-23:00, Su 10:00-23:00, PH 10:00-23:00';
  assert.equal(evaluateOpeningHours(spec, at(MO, 22, 30)).state, 'closed');
  assert.equal(evaluateOpeningHours(spec, at(FR, 22, 30)).state, 'open');
});

test('off-Regel schliesst einen einzelnen Tag', () => {
  const spec = 'Mo-Su 10:00-20:00; We off';
  assert.equal(evaluateOpeningHours(spec, at(TU, 12)).state, 'open');
  assert.equal(evaluateOpeningHours(spec, at(WE, 12)).state, 'closed');
});

test('Nicht sicher Lesbares wird als unbekannt gemeldet', () => {
  for (const spec of [null, '', 'Mar-Oct 10:00-20:00', 'sunrise-sunset', 'Mo[1] 10:00-20:00']) {
    assert.equal(evaluateOpeningHours(spec, at(MO, 12)).state, 'unknown', String(spec));
  }
});

// Alle unterschiedlichen Angaben, die in der Stichprobe tatsaechlich in OSM stehen.
const REAL = [
  "00:00-01:00,10:00-24:00",
  "05:30-22:30",
  "08:00-22:00",
  "10:30-00:30; Fr,Sa 09:30-02:30",
  "Mo 00:00-01:00,08:00-01:00; Tu-Th 08:00-01:00; Fr 08:00-24:00; Sa,Su 00:00-24:00",
  "Mo 10:00-01:00, Tu 10:00-01:00, We 10:00-01:00, Th 10:00-01:00, Fr 10:00-02:00, Sa 10:00-02:00, Su 11:00-01:00",
  "Mo,Th-Su 06:00-22:00; Tu-We 08:00-20:00",
  "Mo-Fr 00:00-01:00,10:00-24:00; Sa,Su 00:00-03:00,10:00-24:00",
  "Mo-Fr 06:30-22:00; Sa 06:30-21:00; Su 06:30-22:00; PH 06:30-22:00",
  "Mo-Fr 10:00-22:00, Sa 10:00-22:00, Su 10:00-22:00, PH 10:00-22:00",
  "Mo-Fr 10:30-24:00, Sa,Su 11:00-24:00",
  "Mo-Sa 08:00-24:00",
  "Mo-Sa 10:00-24:00, Su 11:00-24:00",
  "Mo-Sa 12:00-20:00; Su 12:00-22:00",
  "Mo-Su 09:00-24:00",
  "Mo-Su 10:00-24:00",
  "Mo-Su 11:30-00:00",
  "Mo-Su,PH 10:00-16:30",
  "Mo-Su,PH 11:00-22:00",
  "Mo-Th 08:00-01:00, Fr 08:00-03:30, Sa 08:00-04:00, Su 10:00-01:00",
  "Mo-Th 08:00-01:00, Fr 08:00-05:00, Sa 08:00-05:00, Su 09:00-24:00, PH 09:00-24:00",
  "Mo-Th 08:00-01:00, Fr,Sa 08:00-03:00, Su 10:00-01:00",
  "Mo-Th 09:00-01:00, Fr 09:00-02:00, Sa 09:00-02:00, Su 10:00-01:00, PH 10:00-01:00",
  "Mo-Th 09:00-01:00, Fr,Sa 09:00-03:00, Su 10:00-01:00",
  "Mo-Th 09:00-24:00; Fr 00:00-01:00,09:00-24:00; Sa,Su 00:00-03:00,10:00-24:00",
  "Mo-Th 09:00-24:00; Fr 09:00-03:00; Sa 09:00-03:00; Su 10:00-24:00",
  "Mo-Th 09:00-24:00; Tu-Fr 00:00-01:00; Fr,Sa 09:00-24:00; Sa,Su 00:00-03:00; Su 10:00-24:00; Mo 00:00-01:00",
  "Mo-Th 09:30-02:00, Fr,Sa 10:00-04:00, Su 11:00-24:00",
  "Mo-Th 10:00-00:00; Fr,Sa 10:00-02:00; Su 10:00-24:00",
  "Mo-Th 10:00-01:00, Fr 10:00-03:00, Sa 10:00-02:00, Su 10:00-01:00, PH 10:00-01:00",
  "Mo-Th 10:00-01:00, Fr 10:00-03:00, Sa 10:00-03:00, Su 10:00-01:00, PH 10:00-01:00",
  "Mo-Th 10:00-01:00, Fr 10:00-03:00, Sa 10:00-03:00, Su 10:00-24:00, PH 10:00-24:00",
  "Mo-Th 10:00-22:00; Fr 10:00-23:00, Sa 10:00-23:00, Su 10:00-23:00, PH 10:00-23:00",
  "Mo-Th 10:00-22:00; Fr,Sa 11:00-24:00; Su 12:00-22:00",
  "Mo-Th 10:00-23:00; Fr-Sa 10:00-24:00; Su 10:30-23:00",
  "Mo-Th 10:00-24:00, Fr 10:00-01:00, Sa 10:00-01:00, Su 10:00-24:00, PH 10:00-24:00",
  "Mo-Th 10:00-24:00, Fr 10:00-02:00, Sa 10:00-02:00, Su 10:00-24:00, PH 10:00-24:00",
  "Mo-Th 10:00-24:00, Fr 10:00-03:00, Sa 10:00-03:00, Su 10:00-24:00, PH 10:00-24:00",
  "Mo-Th 10:00-24:00, Su 10:30-24:00, PH 10:00-24:00, Fr 10:00-02:00, Sa 10:30-02:00",
  "Mo-Th 11:00-01:00, Fr-Su 11:00-02:00",
  "Mo-Th 11:00-22:00; Fr 11:00-03:00; Sa 11:00-05:00; Su 00:00-24:00",
  "Mo-Th 11:00-23:00, Fr 11:00-24:00, Sa 11:00-24:00, Su 11:00-23:00, PH 11:00-23:00",
  "Mo-Th 11:00-23:00, Fr,Sa 11:00-24:00, Su 11:00-23:00",
  "Mo-Th 11:30-21:30; Fr-Su 11:30-22:00",
  "Mo-Th 12:00-20:00; Fr,Sa 11:00-21:00; Su 11:00-20:00",
  "Mo-Th 12:00-21:00, Fr 12:00-22:00, Sa 12:00-22:00, Su 12:00-21:00, PH 12:00-21:00",
  "PH,Mo-Th 10:00-24:00; Fr,Sa 10:00-03:00",
  "Su 11:00-01:00, Mo-Th 10:00-01:00, Fr,Sa 10:00-02:00",
  "Su-Th 09:00-24:00, Fr,Sa 09:00-01:00",
  "Su-Th 10:00-01:00, Fr,Sa 10:00-03:00",
  "Su-Th 10:30-01:00, Fr 10:30-03:00, Sa 11:00-03:00",
  "Su-Th 11:00-01:00, Fr,Sa 11:00-02:00",
  "Su-Th 11:00-24:00, Fr,Sa 11:00-02:00",
  "Su-We 10:00-01:00, Th 10:00-02:00, Fr,Sa 10:00-04:00",
];

test('Jede echte Angabe ist auswertbar', () => {
  for (const spec of REAL) {
    assert.notEqual(evaluateOpeningHours(spec, at(WE, 12)).state, 'unknown', spec);
  }
});

test('Jede echte Angabe hat offene und geschlossene Zeiten in der Woche', () => {
  for (const spec of REAL) {
    const states = new Set();
    for (let half = 0; half < 7 * 24 * 2; half += 1) {
      const moment = new Date(MONDAY.getTime() + half * 30 * 60_000);
      states.add(evaluateOpeningHours(spec, moment).state);
    }
    assert.ok(states.has('open'), `nie offen: ${spec}`);
    assert.ok(states.has('closed'), `nie geschlossen: ${spec}`);
  }
});
