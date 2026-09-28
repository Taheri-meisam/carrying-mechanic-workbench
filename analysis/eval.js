// Reproduction script for the tables in the PIPELINE paper.
//
// Input: pool.json -- the adjudicated pool exported from the workbench
//   (sqlite3 data/workbench.sqlite "select json from records"), one record per game.
//   record.review.raw = the model's draft for that entry.
//   the record itself  = the human coder's ratings.
//
//   PROVENANCE. In the nine-game pilot the coder rated each game from the source entry WITHOUT
//   seeing the model's draft; those ratings were then entered into the record. The key name
//   `review.raw` and the column label "draft -> coder" are therefore about which values sit where,
//   not about anyone reviewing or correcting anything. This script compares two independent
//   readings of the same entry. It cannot, and does not, show that a draft was edited.
//
// The field accessors and the two statistics below must stay identical to backend/evaluate.py
// and the frontend's renderEval() -- design rule 6. All three gates are scored.
//
//   node eval.js [pool.json]

const fs = require('fs');
const path = process.argv[2] || 'pool.json';
const pool = JSON.parse(fs.readFileSync(path, 'utf8'));

const FIELDS={
  route:r=>r.route.primary,
  MC:r=>String(r.dimensions.MC.level),
  LOM:r=>String(r.dimensions.LOM.level),
  AGE:r=>String(r.dimensions.AGE.level),
  CTa:r=>String(r.dimensions.CTa.level),
  S:r=>r.dig.S.op, M:r=>r.dig.M.op, L:r=>r.dig.L.op,
  D:r=>r.distinctiveness.level,
  G1:r=>String(r.gates.G1.pass), G2:r=>String(r.gates.G2.pass), G3:r=>String(r.gates.G3.pass),
};
const ORDER={Kept:3,Transformed:2,Substituted:1,Removed:0};
// Instrument v0.2. Kept and Transformed survive; Removed does not; Substituted is conditional on
// the substitution test. Must stay identical to backend/evaluate.py survives() and the frontend.
const survives=d=>(ORDER[(d||{}).op]||0)>=2?true:((d||{}).op!=='Substituted'?false:!!(((d||{}).substitutionTest||{}).pass));

const SECTIONS = ['mechanic','gates','route','dimensions','dig','ledger','distinct'];
const reviewed = r => SECTIONS.filter(s => r.review?.sections?.[s]).length;

function kappa(a, b) {
  const n = a.length; if (!n) return null;
  const cats = [...new Set([...a, ...b])];
  let po = 0; for (let i = 0; i < n; i++) if (a[i] === b[i]) po++;
  po /= n;
  let pe = 0; for (const c of cats) pe += (a.filter(x => x === c).length / n) * (b.filter(x => x === c).length / n);
  // pe >= 1: both coders used a single identical category, so there was no distinction to agree
  // about and the statistic divides by zero. Undefined, not 1.
  return pe >= 1 ? null : (po - pe) / (1 - pe);
}

const pad = (s, n) => String(s).padEnd(n);
const done = pool.filter(r => r.review?.raw && reviewed(r) === SECTIONS.length);
console.log(`${path}: ${pool.length} records, ${done.length} fully reviewed with a stored draft\n`);

console.log("Table: agreement -- model ratings against the coder's independent ratings");
console.log(pad('field', 7), pad('agree', 7), pad('acc', 6), 'disagreements (model -> coder)');
let tot = 0, totN = 0;
for (const f of Object.keys(FIELDS)) {
  let ok = 0; const dis = [];
  for (const r of done) {
    const m = FIELDS[f](r.review.raw), h = FIELDS[f](r);
    if (m === h) ok++; else dis.push(`${r.name.split(' ')[0]}: ${m} -> ${h}`);
  }
  if (f !== 'D') { tot += ok; totN += done.length; }   // D excluded: see the table caption
  console.log(pad(f, 7), pad(`${ok}/${done.length}`, 7), pad((ok / done.length).toFixed(2), 6), dis.join('; '));
}
console.log(pad('TOTAL', 7), pad(`${tot}/${totN}`, 7), pad((tot / totN).toFixed(2), 6), '(D level excluded)');

const two = pool.filter(r => r.review2?.coder && reviewed(r) === SECTIONS.length);
console.log('\nTable: rubric reliability -- coder 1 against coder 2');
if (!two.length) {
  console.log('  Not measured. No record in this pool carries a second coding, so there is no');
  console.log('  kappa to compute. The paper reports this table empty for the same reason.');
} else {
  console.log(pad('field', 7), pad('agree', 7), pad('kappa', 7));
  for (const f of ['route','MC','LOM','AGE','CTa','S','D']) {
    const rows = two.filter(r => r.review2[f] !== null && r.review2[f] !== '');
    const a = rows.map(r => String(FIELDS[f](r))), b = rows.map(r => String(r.review2[f]));
    const k = kappa(a, b);
    console.log(pad(f, 7), pad(`${a.filter((v,i)=>v===b[i]).length}/${a.length}`, 7),
                pad(k === null ? '--' : k.toFixed(2), 7));
  }
}

// Algorithm 1: eligibility and the hybrid flag, computed on the adjudicated record.
const eligible = r => {
  const f = [], g = r.gates;
  if (!(g.G1.pass && g.G2.pass && g.G3.pass)) f.push('gate');
  if (+r.dimensions.MC.level < 1) f.push('MC');
  // LOM is conditional on a stated educational objective and is not part of core eligibility
  if (!survives(r.dig.S)) f.push('DIG(S)');
  if (+r.dimensions.CTa.level < 1) f.push('CT-a');
  const hybrid = !survives(r.dig.S) && survives(r.dig.M);   // v0.3: L dropped (could not discriminate)
  return { ok: !f.length && reviewed(r) === SECTIONS.length, fails: f, hybrid };
};

console.log('\nTable: corpus decisions');
console.log(pad('game', 30), pad('route', 6), pad('verdict', 10), 'failed on');
for (const r of pool.filter(r => reviewed(r) === SECTIONS.length)) {
  const e = eligible(r);
  console.log(pad(r.name.slice(0, 29), 30), pad(r.route.primary, 6),
              pad(e.ok ? 'eligible' : (e.hybrid ? 'hybrid' : 'rejected'), 10), e.fails.join(', '));
}
