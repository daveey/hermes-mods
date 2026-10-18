/* Fork-tailed bush katydid — scenes. Loaded by the animated-explainer shell. */
const EXPLAINER = (() => {
const C = {body:'#8fd05c', dark:'#3f7a22', leg:'#62a236'};

/* katydid, side view, facing right; local box 0..300 x 0..140 (feet at y=140) */
function kd(o = {}) {
  const n = o.nymph, sex = o.sex || 'm';
  const A1 = 'M246 78 C 220 20, 140 -10, 30 18', A2 = 'M244 80 C 232 34, 180 8, 70 2';
  const ant = d => n
    ? `<path d="${d}" stroke="#f3f3e6" stroke-width="2.8" fill="none"/><path d="${d}" stroke="#1f2a14" stroke-width="2.8" fill="none" stroke-dasharray="7 7"/>`
    : `<path d="${d}" stroke="${C.dark}" stroke-width="1.8" fill="none" stroke-linecap="round"/>`;
  const far = `<g stroke="${C.dark}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round" opacity=".7"><path d="M176 108 L187 128 L180 140"/><path d="M207 108 L222 126 L230 140"/><path d="M150 104 L118 66 L104 138"/></g>`;
  const hw = n ? '' : `<path d="M44 90 C 60 82, 100 80, 120 86 L 118 94 C 96 96, 64 96, 44 90 Z" fill="#c2ea98" stroke="${C.dark}" stroke-width="1"/>`;
  let abd = `<ellipse cx="150" cy="104" rx="62" ry="14" fill="${C.body}" stroke="${C.dark}" stroke-width="1.2"/>`;
  if (n) abd += [108,124,140,156,172].map(x => `<path d="M${x} 92 Q ${x-4} 104 ${x} 117" stroke="${C.dark}" stroke-width="1" fill="none" opacity=".45"/>`).join('');
  const veins = [80,100,120,140,160,180,198].map(x => { const y = 95 - (x-62)*.047; return `<path d="M${x} ${y.toFixed(1)} l 11 -9 M${x} ${y.toFixed(1)} l 11 8" stroke="${C.dark}" stroke-width=".8" opacity=".4"/>`; }).join('');
  const wing = n
    ? (o.pads ? `<path d="M172 92 C 184 82, 198 84, 202 90 C 194 101, 180 102, 172 92Z" fill="#79bd4b" stroke="${C.dark}"/>` : '')
    : `<g class="wing ${o.wingCls || ''}"><path d="M58 96 C 80 70, 170 64, 214 88 C 182 106, 100 110, 58 96 Z" fill="url(#gWing)" stroke="${C.dark}" stroke-width="1.3"/><path d="M62 95 C 110 87, 170 80, 211 88" stroke="${C.dark}" stroke-width="1.1" fill="none" opacity=".7"/>${veins}</g>`;
  const tail = n ? '' : sex === 'm'
    ? `<path d="M100 110 L 79 102 M100 110 L 81 120" stroke="${C.dark}" stroke-width="3.4" stroke-linecap="round"/>`
    : `<path d="M102 104 C 88 120, 68 116, 60 96 C 72 107, 88 110, 102 112 Z" fill="#7bbb4c" stroke="${C.dark}" stroke-width="1.2"/>`;
  const hind = `<path d="M158 106 C 130 86, 106 64, 90 49 C 88 45, 97 45, 99 49 C 120 70, 148 92, 166 101 Z" fill="${C.leg}" stroke="${C.dark}" stroke-width="1.1"/><path d="M93 50 L 70 136 L 55 140" stroke="${C.dark}" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>`;
  const hr = n ? 1.15 : 1;
  const head = `<path d="M196 82 C 206 76, 222 76, 228 84 L 226 106 C 214 110, 202 108, 196 104 Z" fill="#84c852" stroke="${C.dark}" stroke-width="1.2"/>
    <g transform="translate(237 97) scale(${hr}) translate(-237 -97)"><ellipse cx="237" cy="97" rx="14.5" ry="18" fill="${C.body}" stroke="${C.dark}" stroke-width="1.2"/>
    <circle cx="241" cy="89" r="7.2" fill="#2b3a18"/><circle cx="243.4" cy="86.6" r="2.6" fill="#fff"/><circle cx="239.4" cy="91.6" r="1.1" fill="#fff" opacity=".7"/>
    <ellipse cx="236" cy="103" rx="3.6" ry="2.2" fill="#f2a0a0" opacity=".55"/>
    <path d="M241 107 q 3.5 3.5 7 0" stroke="${C.dark}" stroke-width="1.5" fill="none" stroke-linecap="round"/>
    <path d="M245 112 l3 6 M240 113 l1 6" stroke="${C.dark}" stroke-width="1.4" stroke-linecap="round"/></g>`;
  const near = `<g stroke="${C.dark}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M183 110 L195 128 L189 140"/><path d="M214 108 L232 122 L244 140"/></g><ellipse cx="235.5" cy="127" rx="2.8" ry="1.6" fill="#1b2610" transform="rotate(55 235.5 127)"/>`;
  return `<g class="kd"><g class="ant a2">${ant(A2)}</g>${far}<g class="ant a1">${ant(A1)}</g>${hw}${abd}${wing}${tail}${hind}${head}${near}</g>`;
}
function leaf(x, y, len, ang, fill = '#6fae45', vein = '#3f7a22') {
  const w = len * .24;
  return `<g transform="translate(${x} ${y}) rotate(${ang})"><path d="M0 0 C ${len*.25} ${-w}, ${len*.7} ${-w}, ${len} 0 C ${len*.7} ${w}, ${len*.25} ${w}, 0 0Z" fill="${fill}"/><path d="M0 0 L${len*.94} 0" stroke="${vein}" stroke-width="1.2" opacity=".5"/></g>`;
}
let seed = 11; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
const greens = ['#6fae45','#7dbb52','#5d9c3a','#8cc65f','#4f8a32','#96cf6a'];
const place = (x, y, s, inner) => `<g transform="translate(${x} ${y}) scale(${s})">${inner}</g>`;
let anaSex = 'm';

const HS = [
  {x:348,y:67, t:'Feelers', d:'Feelers (antennae) longer than its whole body! It uses them to touch and smell.'},
  {x:471,y:190, t:'Big eyes', d:'Big eyes that can see you coming.'},
  {x:460,y:256, t:'Leg ears!', d:'Its ears are on its front legs, just below the knees. Funny, right?'},
  {x:300,y:182, t:'Leaf wings', d:'Green wings with lines, just like a leaf.'},
  {x:146,y:191, t:'Flying wings', d:'These wings are for flying. Their tips peek out the back.'},
  {x:216,y:228, t:'Fork tail', d:'Boy katydids have a tail tip shaped like a fork. That’s how it got its name!'}
];

return {
  title: 'Fork-tailed bush katydid',
  coverKick: 'A story about a green little bug',
  coverTitle: 'Meet the fork‑tailed bush katydid',
  playLabel: 'Слушать', playHint: 'Narrated in Russian · tap to start', langLabel: 'RU',
  narrationLang: 'ru', accent: '#3f8a24', coverViewBox: '170 40 380 280',
  meta: '%n chapters · %m min · for kids',
  sources: [{label: 'UC IPM', url: 'https://ipm.ucanr.edu/agriculture/citrus/katydids/'}],
  defs: `<linearGradient id="gWing" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#b8ea88"/><stop offset="1" stop-color="#6db342"/></linearGradient>
    <linearGradient id="gDay" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f4f9ea"/><stop offset="1" stop-color="#d3e8c1"/></linearGradient>
    <linearGradient id="gNight" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0b1430"/><stop offset="1" stop-color="#1e3048"/></linearGradient>
    <linearGradient id="gWinter" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#dfe9f3"/><stop offset="1" stop-color="#c3d4e2"/></linearGradient>
    <radialGradient id="gFruit" cx=".35" cy=".3" r=".75"><stop offset="0" stop-color="#b4e47a"/><stop offset="1" stop-color="#4b9529"/></radialGradient>
    <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0H0V20" fill="none" stroke="#c9d8bb" stroke-width=".6"/></pattern>`,
  scenes: [
{ kick: 'Hello!', title: 'Meet the katydid!',
  body: 'Look who’s sitting on the branch! This green bug is a <b>katydid</b>. Its big science name is <i>Scudderia furcata</i>. It lives on bushes and trees in North America. It can hop, and it can fly too!',
  chips: ['Green like a leaf', 'Super-long feelers', 'Hops and flies'],
  svg: () => `<rect width="640" height="360" fill="url(#gDay)"/><circle cx="560" cy="72" r="40" fill="#fff4c4" opacity=".85"/>
    ${leaf(470,214,120,-32)}${leaf(86,252,110,160,'#7dbb52')}${leaf(560,232,100,25,'#5d9c3a')}${leaf(20,262,80,200,'#8cc65f')}
    <path d="M-10 262 C 150 240, 330 250, 650 228" stroke="#7a5a3a" stroke-width="10" fill="none" stroke-linecap="round"/>
    ${leaf(300,248,90,62,'#6aa843')}${leaf(420,240,95,112,'#79b54e')}${leaf(560,232,80,70,'#6fae45')}
    <g transform="translate(150 70) scale(1.25)"><g class="hop">${kd({sex:'m'})}</g></g>
    <text x="24" y="52" class="svgT" font-size="24" fill="#3f6a28">Scudderia furcata</text>
    <text x="24" y="74" class="sm" fill="#5b7a48">fork-tailed bush katydid</text>`
},
{ kick: 'Body parts', title: 'Let’s look closer', body: 'Each number is a body part. Tap one!',
  chips: ['Tap a number!', 'Try boy / girl'],
  svg: () => `<rect width="640" height="360" fill="#f3f7ec"/><rect width="640" height="360" fill="url(#grid)" opacity=".7"/>
    <path d="M30 278 H610" stroke="#a9c592" stroke-width="2" stroke-dasharray="2 6"/>
    <g id="anaK" transform="translate(60 40) scale(1.7)">${kd({sex: anaSex})}</g><g id="hsL"></g><g id="hs"></g>`,
  enter(ctx) {
    const {$} = ctx;
    ctx.overlay.innerHTML = '<button data-s="m">♂ boy</button><button data-s="f">♀ girl</button>';
    const cues = ['Номер один','Номер два','Номер три','Номер четыре','Номер пять','номер шесть'].map(ctx.cue);
    const girlAt = ctx.cue('А у девочки');
    let cur = -1, auto = true;
    const setSex = s => { anaSex = s; $('anaK').innerHTML = kd({sex: s}); [...ctx.overlay.children].forEach(x => x.setAttribute('aria-pressed', x.dataset.s === s)); };
    const draw = () => {
      const pts = HS.map((h,i) => i === 5 && anaSex === 'f' ? {...h, x:186, y:217, t:'Egg sword', d:'Girl katydids have a curved little sword for tucking eggs into leaves.'} : h);
      $('hs').innerHTML = pts.map((h,i) => `<g class="hs" data-i="${i}"><circle class="pulse" cx="${h.x}" cy="${h.y}" r="11" fill="#ffb347"/><circle cx="${h.x}" cy="${h.y}" r="${i===cur?15:12}" fill="${i===cur?'#e8762c':'#fff'}" stroke="#e8762c" stroke-width="2.5"/><text x="${h.x}" y="${h.y+5}" text-anchor="middle" class="lbl" font-size="14" fill="${i===cur?'#fff':'#c25a16'}">${i+1}</text></g>`).join('');
      if (cur < 0) { $('hsL').innerHTML = ''; ctx.set({title: 'Let’s look closer', body: 'Each number is a body part. Tap one!'}); return; }
      const h = pts[cur], left = h.x > 420, w = h.t.length * 9 + 26, bx = left ? h.x - 22 - w : h.x + 22;
      $('hsL').innerHTML = `<g class="pop"><rect x="${bx}" y="${h.y-34}" width="${w}" height="30" rx="15" fill="#2f4a1f"/><text x="${bx+w/2}" y="${h.y-14}" text-anchor="middle" class="lbl" font-size="16" fill="#fff">${h.t}</text></g>`;
      ctx.set({title: h.t, body: h.d});
    };
    setSex('m'); draw();
    $('hs').onclick = e => { const g = e.target.closest('.hs'); if (!g) return; cur = +g.dataset.i; auto = false; draw(); };
    ctx.overlay.onclick = e => { const b = e.target.closest('button'); if (!b) return; auto = false; setSex(b.dataset.s); cur = 5; draw(); };
    ctx.every(() => { if (!auto) return; const t = ctx.clock(); let c = -1; cues.forEach((q,i) => { if (t >= q) c = i; });
      const s = t >= girlAt ? 'f' : 'm'; if (c !== cur || s !== anaSex) { cur = c; if (s !== anaSex) setSex(s); draw(); } }, 150);
  }
},
{ kick: 'Growing up', title: 'How a katydid grows',
  body: 'Mom hides tiny gray eggs <b>inside</b> the edge of a leaf. The eggs sleep all winter. In spring, babies hatch! Babies have no wings yet, and their feelers are stripy. They grow and wiggle out of their old skin 6, 7 or even 8 times. Then: wings!',
  chips: ['Eggs hide in leaves', 'Stripy babies', 'New skin 6–8 times'],
  svg: () => `<rect width="640" height="360" fill="url(#gDay)"/><rect id="winter" width="640" height="360" fill="url(#gWinter)" opacity="0" style="transition:opacity .6s"/>
    <path d="M520 0 C 500 40, 480 60, 470 72" stroke="#7a5a3a" stroke-width="7" fill="none"/>
    <path d="M30 72 C 150 18, 360 18, 476 72 C 380 128, 140 128, 30 72Z" fill="#79b84d" stroke="#3f7a22" stroke-width="1.5"/>
    <path d="M34 72 L470 72" stroke="#3f7a22" stroke-width="1.6" opacity=".6"/>
    ${[90,140,190,240,290,340,390].map(x=>`<path d="M${x} 72 l 28 -26 M${x} 72 l 28 26" stroke="#3f7a22" stroke-width="1" opacity=".35"/>`).join('')}
    ${[[150,113],[195,117],[240,118],[285,117],[330,114]].map(([x,y],i)=>`<ellipse id="egg${i}" cx="${x}" cy="${y}" rx="11" ry="4.2" fill="#9aa08f" stroke="#5f6458" stroke-width="1" opacity="0" transform="rotate(${(i-2)*-4} ${x} ${y})"/>`).join('')}
    <g id="zzz" opacity="0" style="transition:opacity .6s"><text x="352" y="104" class="lbl" font-size="18" fill="#5a6f86">z</text><text x="366" y="90" class="lbl" font-size="22" fill="#5a6f86">z</text><text x="384" y="72" class="lbl" font-size="26" fill="#5a6f86">Z</text></g>
    <g class="snow" id="snow" opacity="0" style="transition:opacity .6s">${Array.from({length:26},(_,i)=>`<circle cx="${(i*53)%640}" cy="${(i*37)%120}" r="${2+(i%3)}" fill="#fff" style="animation-delay:${-(i*.37)%3.5}s"/>`).join('')}</g>
    <rect y="298" width="640" height="62" fill="#b5d593"/><path d="M0 298 H640" stroke="#8fb86a" stroke-width="2"/>
    <g id="grow" class="grow"></g>
    <text id="stageTxt" x="616" y="160" text-anchor="end" class="lbl" font-size="18" fill="#3f6a28"></text>
    <text id="stageSub" x="616" y="181" text-anchor="end" class="sm" fill="#5b7a48"></text>
    <g id="tl" transform="translate(0 326)"></g>`,
  enter(ctx) {
    const {$} = ctx, TL = ['Eggs', 'Winter', 'Hatch!', 'Growing', 'Grown-up'], X = [70, 190, 320, 450, 570];
    const tA = ctx.cue('Летом'), tW = ctx.cue('Всю зиму'), tH = ctx.cue('А весной'), tG = ctx.cue('Малыш ест'), tAd = ctx.cue('И вот');
    const grow = $('grow'); let last = '';
    const tl = i => $('tl').innerHTML = `<path d="M${X[0]} 0 H${X[4]}" stroke="#7fa65d" stroke-width="2"/>` + TL.map((t,k) => `<circle cx="${X[k]}" cy="0" r="${k===i?8:5}" fill="${k===i?'#e8762c':'#7fa65d'}"/><text x="${X[k]}" y="22" text-anchor="middle" class="${k===i?'lbl':'sm'}" fill="${k===i?'#2f4a1f':'#4f6b3c'}">${t}</text>`).join('');
    const setK = (s, inner) => { grow.innerHTML = inner; grow.style.transform = `translate(${320 - 150*s}px, ${298 - 140*s}px) scale(${s})`; };
    const st = (a, b) => { $('stageTxt').textContent = a; $('stageSub').textContent = b; };
    const winter = on => ['winter','snow','zzz'].forEach(id => $(id).setAttribute('opacity', on ? 1 : 0));
    const eggs = n => { for (let i = 0; i < 5; i++) $('egg'+i).setAttribute('opacity', i < n ? 1 : 0); };
    const render = () => {
      const t = ctx.clock(); let key, fn;
      if (t < tA) { key = 'pre'; fn = () => { eggs(0); grow.innerHTML = ''; winter(0); tl(-1); st('', ''); }; }
      else if (t < tW) { const n = Math.min(5, 1 + Math.floor((t - tA) / ((tW - tA) / 5))); key = 'e' + n; fn = () => { eggs(n); grow.innerHTML = ''; winter(0); tl(0); st('Eggs', 'hidden inside the leaf'); }; }
      else if (t < tH) { key = 'w'; fn = () => { eggs(5); grow.innerHTML = ''; winter(1); tl(1); st('Shh… sleeping', 'all winter long'); }; }
      else if (t < tG) { key = 'h'; fn = () => { eggs(5); winter(0); $('egg2').setAttribute('opacity', .35); tl(2); st('Hatch!', 'hello, baby'); setK(.2, kd({nymph:true})); }; }
      else if (t < tAd) { const k = Math.min(7, 1 + Math.floor((t - tG) / ((tAd - tG) / 7))); key = 'g' + k; fn = () => { winter(0); tl(3); st(`New skin #${k}`, 'grow, grow, grow'); setK(.22 + k*.075, kd({nymph:true, pads: k >= 4})); }; }
      else { key = 'a'; fn = () => { winter(0); tl(4); st('Grown-up!', 'now it has wings'); setK(.86, kd({sex:'f'})); }; }
      if (key !== last) { last = key; fn(); }
    };
    render(); ctx.every(render, 150);
  }
},
{ kick: 'Night songs', title: 'Singing in the dark',
  body: 'When it gets dark, the boy rubs his wings together to sing: <i>tsip… tsip…</i> The girl hears him with the ears on her legs and answers: <i>tick!</i> They keep calling until they find each other.',
  chips: ['Sings with its wings', 'Hears with its legs'],
  svg: () => `<rect width="640" height="360" fill="url(#gNight)"/>
    <circle cx="540" cy="70" r="30" fill="#f5f0d8"/><circle cx="552" cy="64" r="26" fill="#0f1a36"/>
    ${Array.from({length:30},(_,i)=>`<circle class="twinkle" cx="${(i*97)%640}" cy="${(i*41)%170}" r="${i%4?1:1.6}" fill="#fff" style="animation-delay:${-(i*.3)%2.4}s"/>`).join('')}
    <path d="M-10 252 C 80 246, 200 250, 300 238" stroke="#3b2f25" stroke-width="9" fill="none" stroke-linecap="round"/>
    <path d="M650 260 C 560 256, 440 262, 340 250" stroke="#3b2f25" stroke-width="9" fill="none" stroke-linecap="round"/>
    ${leaf(260,240,80,-40,'#2c4a2a','#1b2e1a')}${leaf(20,250,70,200,'#2f5230','#1b2e1a')}${leaf(380,252,80,220,'#2c4a2a','#1b2e1a')}${leaf(610,258,70,-20,'#2f5230','#1b2e1a')}
    ${[0,.35,.7].map(d=>`<circle class="ring wm" cx="152" cy="192" r="9" fill="none" stroke="#ffe7a0" stroke-width="2" vector-effect="non-scaling-stroke" style="animation-delay:${d}s"/>`).join('')}
    ${[0,.25].map(d=>`<circle class="ring wf" cx="488" cy="200" r="9" fill="none" stroke="#a8e2ff" stroke-width="2" vector-effect="non-scaling-stroke" style="animation-delay:${d}s"/>`).join('')}
    <g filter="brightness(.8)">${place(30,122,.9,kd({sex:'m',wingCls:'buzz'}))}</g>
    <g transform="translate(610 128) scale(-.9 .9)"><g class="approach">${kd({sex:'f'})}</g></g>
    <text class="say svgT" x="190" y="128" font-size="28" fill="#ffe7a0">tsip… tsip…</text>
    <text class="say f svgT" x="408" y="138" font-size="26" fill="#a8e2ff">tick!</text>
    <text x="24" y="338" class="sm" fill="#8ea3b8">♂ boy sings</text><text x="616" y="338" text-anchor="end" class="sm" fill="#8ea3b8">♀ girl answers</text>`
},
{ kick: 'Game time', title: 'Hide and seek!',
  body: 'Can you find the katydid? It’s so green it’s almost invisible! And if you walk up close, it hides behind a leaf.',
  chips: ['Leaf-green wings', 'Sharp eyes'],
  svg: () => { seed = 11;
    const back = Array.from({length:56},()=>leaf(rnd()*680-40, rnd()*400-20, 60+rnd()*70, rnd()*360, greens[Math.floor(rnd()*greens.length)])).join('');
    const front = [leaf(250,230,95,-20,'#5d9c3a'),leaf(470,130,90,150,'#7dbb52'),leaf(300,120,70,-70,'#6fae45'),leaf(420,250,85,30,'#4f8a32'),leaf(268,214,80,-35,'#7dbb52'),leaf(330,128,90,20,'#8cc65f'),leaf(415,214,70,-80,'#5d9c3a'),leaf(205,150,80,10,'#6fae45')].join('');
    return `<rect width="640" height="360" fill="#bcd8a4"/>${back}${place(250,120,.7,kd({sex:'f'}))}${front}
      <circle class="reveal" cx="358" cy="172" r="112" fill="none" stroke="#fff" stroke-width="4" stroke-dasharray="8 7"/>
      <g class="lens"><circle r="34" fill="#ffffff22" stroke="#fff" stroke-width="4"/><path d="M24 24 L46 46" stroke="#fff" stroke-width="7" stroke-linecap="round"/></g>
      <g class="hunt"><rect x="16" y="26" width="180" height="38" rx="19" fill="#ffffffe0"/><text x="106" y="50" text-anchor="middle" class="lbl" font-size="16" fill="#2f5a1c">Can you find it?</text></g>
      <g class="reveal"><rect x="16" y="26" width="140" height="38" rx="19" fill="#ffffffe0"/><text x="86" y="50" text-anchor="middle" class="lbl" font-size="16" fill="#2f5a1c">There it is!</text></g>`; },
  enter(ctx) { const d = Math.max(8, ctx.cue('Вот он') / .5);
    document.querySelectorAll('#scene .reveal,#scene .hunt,#scene .lens').forEach(el => el.style.animationDuration = d + 's'); }
},
{ kick: 'Snack time', title: 'Nibble, hop, nibble!',
  body: 'Baby katydids love orange trees. They take <b>one</b> bite of a tiny green orange (nom!), then hop to the next one. The bites leave little spots on the peel, but the inside stays yummy.',
  chips: ['One bite each', 'Little spots on the peel'],
  svg: () => { const F = [[95,206],[210,228],[325,202],[440,224],[555,208]];
    return `<rect width="640" height="360" fill="url(#gDay)"/>
    ${leaf(150,100,110,-150,'#5d9c3a')}${leaf(380,118,120,-30,'#6fae45')}${leaf(520,104,100,-150,'#7dbb52')}
    <path d="M-10 112 C 200 92, 420 140, 650 104" stroke="#7a5a3a" stroke-width="9" fill="none" stroke-linecap="round"/>
    ${F.map(([x,y])=>`<path d="M${x} ${y-26} C ${x+3} ${y-60}, ${x-4} ${y-80}, ${x} 112" stroke="#5c7a32" stroke-width="2.5" fill="none"/>`).join('')}
    ${F.map(([x,y],i)=>`<circle cx="${x}" cy="${y}" r="26" fill="url(#gFruit)" stroke="#3f7a22" stroke-width="1"/><g id="scar${i}"></g>`).join('')}
    ${leaf(60,106,90,40,'#7dbb52')}${leaf(270,110,90,60,'#6aa843')}${leaf(470,116,90,50,'#5d9c3a')}
    ${[120,260,400,520].map((x,i)=>`<ellipse class="petal" cx="${x}" cy="90" rx="6" ry="3.5" fill="#fffdf4" stroke="#e8e0c8" style="animation-delay:${-i*1.5}s"/>`).join('')}
    <g id="nym" class="nymph"><g id="nymJ">${kd({nymph:true,pads:true})}</g></g>
    <text id="bites" x="24" y="338" class="lbl" font-size="17" fill="#3f6a28">Nom nom: 0</text>`; },
  enter(ctx) {
    const {$} = ctx, F = [[95,206],[210,228],[325,202],[440,224],[555,208]], nym = $('nym'), s = .42; let i = 0, n = 0;
    const go = k => { const [x,y] = F[k]; nym.style.transform = `translate(${x - 150*s}px, ${y - 25 - 140*s}px) scale(${s})`;
      const j = $('nymJ'); j.classList.remove('jump'); void j.getBBox(); j.classList.add('jump'); };
    nym.style.transition = 'none'; go(0); void nym.getBBox(); nym.style.transition = '';
    const step = () => {
      if (i === F.length) { F.forEach((_,k) => $('scar'+k).innerHTML = ''); n = 0; i = 0; $('bites').textContent = 'Nom nom: 0'; go(0); return; }
      go(i); const k = i;
      ctx.later(() => { const [x,y] = F[k]; $('scar'+k).innerHTML = `<ellipse class="pop" cx="${x-9}" cy="${y+8}" rx="9" ry="6.5" fill="#c8b07a" stroke="#8a7445" stroke-width="1.5"/><text class="nom lbl" x="${x+14}" y="${y-30}" font-size="16" fill="#c25a16">nom!</text>`; n++; $('bites').textContent = `Nom nom: ${n}`; }, 750);
      i++;
    };
    ctx.later(() => { step(); ctx.every(step, 1700); }, Math.max(300, (ctx.cue('Малыш откусит') - .6) * 1000));
  }
}
  ]
};
})();
