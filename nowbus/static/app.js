/* NowBus 프론트엔드.
 *
 * 상태는 모듈 스코프 변수 몇 개면 충분하다. 화면이 세 개뿐이라 라우터도
 * 상태관리 라이브러리도 얹지 않는다.
 */
'use strict';

let coords = null;       // {lat, lon, accuracy} - 진입 즉시 백그라운드로 채운다
let gpsError = null;
let places = [];
let lastResponse = null;
let lastQuery = null;    // 새로고침·반경확대에 재사용
let freshnessTimer = null;

const $ = (id) => document.getElementById(id);
const TOKEN_KEY = 'nowbus.token';

/* ---------------------------------------------------------------- 토큰 */
function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
}
function askToken(force) {
  let t = getToken();
  if (t && !force) return t;
  t = window.prompt('접근 토큰 (.env 의 NOWBUS_API_TOKEN)', t || '');
  if (t === null) return getToken();
  try { localStorage.setItem(TOKEN_KEY, t.trim()); } catch { /* 사파리 비공개 모드 */ }
  return t.trim();
}

async function api(path, params, body) {
  const url = new URL(path, location.origin);
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== null && v !== undefined) url.searchParams.set(k, v);
  });
  const init = { headers: { 'X-Token': getToken() } };
  if (body !== undefined) {
    init.method = 'POST';
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const res = await fetch(url, init);
  if (res.status === 401) {
    askToken(true);
    throw new Error('토큰이 필요해요');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(detailText(err) || `요청 실패 (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}

// FastAPI 의 422 는 detail 이 배열이다. 그대로 표시하면 [object Object] 가 뜬다.
function detailText(err) {
  const d = err && err.detail;
  if (!d) return '';
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((e) => e.msg || '').filter(Boolean).join(', ');
  return '';
}

/* ---------------------------------------------------------------- GPS */
// 선(先)위치 획득: 사용자가 목적지를 고르는 사이에 좌표를 확보해 둔다.
function startGps() {
  const el = $('gps');
  if (!navigator.geolocation) {
    gpsError = '이 브라우저는 위치를 지원하지 않아요';
    el.textContent = gpsError;
    el.className = 'gps err';
    return;
  }
  if (!window.isSecureContext) {
    // HTTPS 가 아니면 iOS 는 조용히 막는다. 원인을 알려줘야 한다.
    gpsError = 'HTTPS 가 아니라 위치를 쓸 수 없어요';
    el.textContent = gpsError;
    el.className = 'gps err';
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      coords = {
        lat: pos.coords.latitude,
        lon: pos.coords.longitude,
        accuracy: pos.coords.accuracy,
      };
      const acc = Math.round(coords.accuracy);
      el.textContent = acc > 100
        ? `현재 위치 (오차 ${acc}m — 정확도가 낮아요)`
        : '현재 위치 확인됨';
      el.className = acc > 100 ? 'gps' : 'gps ok';
    },
    (err) => {
      gpsError = err.code === err.PERMISSION_DENIED
        ? '위치 권한이 거부됐어요. 출발지를 골라주세요'
        : '위치를 가져오지 못했어요. 출발지를 골라주세요';
      el.textContent = gpsError;
      el.className = 'gps err';
    },
    { enableHighAccuracy: true, timeout: 5000, maximumAge: 30000 }
  );
}

/* ---------------------------------------------------------------- 홈 */
async function loadPlaces() {
  const box = $('places');
  try {
    places = await api('/api/places');
  } catch (e) {
    box.innerHTML = `<p class="empty">${esc(e.message)}</p>`;
    return;
  }
  if (!places.length) {
    box.innerHTML = '<p class="empty">장소를 먼저 추가해주세요</p>';
    return;
  }
  box.innerHTML = '';
  for (const p of places) {
    const b = document.createElement('button');
    b.className = 'btn';
    b.textContent = p.name;
    b.onclick = () => onDestination(p);
    box.appendChild(b);
  }
}

// GPS 가 없으면 출발지를 먼저 물어본다 (설계서 §10.3 폴백).
function onDestination(dest) {
  if (coords) return run({ lat: coords.lat, lon: coords.lon, to: dest.name }, `현재 위치 → ${dest.name}`);
  const others = places.filter((p) => p.name !== dest.name);
  if (!others.length) return alert(gpsError || '출발지를 알 수 없어요');
  const names = others.map((p, i) => `${i + 1}. ${p.name}`).join('\n');
  const pick = window.prompt(`출발지를 골라주세요\n${names}`, '1');
  const from = others[Number(pick) - 1];
  if (!from) return;
  run({ from: from.name, to: dest.name }, `${from.name} → ${dest.name}`);
}

/* ---------------------------------------------------------------- 결과 */
async function run(query, label) {
  lastQuery = { query, label };
  show('result');
  $('trip-label').textContent = label;
  $('banner').hidden = true;
  $('composition').hidden = true;
  $('sprint-box').hidden = true;
  $('freshness').textContent = '조회 중…';
  $('cards').innerHTML = '<div class="skeleton"></div>'.repeat(3);
  try {
    lastResponse = await api('/api/plan', query);
    render(lastResponse);
  } catch (e) {
    $('cards').innerHTML = `<p class="empty">${esc(e.message)}</p>`;
    $('freshness').textContent = '';
  }
}

function render(data) {
  const cards = $('cards');
  const banner = $('banner');

  // 뛰어야 하는 후보를 맨 위에 따로 놓는다. 1~2분 뒤 도착하는 버스라 제일 급하고,
  // 본 목록에 섞으면 '여유 있음'과 구분이 안 된다.
  const sprintBox = $('sprint-box');
  const sprint = $('sprint');
  sprint.innerHTML = '';
  if (data.sprint && data.sprint.length) {
    for (const it of data.sprint) sprint.appendChild(card(it));
    sprintBox.hidden = false;
  } else {
    sprintBox.hidden = true;
  }

  if (!data.items.length) {
    cards.innerHTML = '<p class="empty">지금 걸어서 탈 수 있는 직통 버스가 없어요</p>';
    const wider = document.createElement('button');
    wider.className = 'btn';
    wider.textContent = '반경 넓혀서 다시 찾기';
    wider.onclick = () => {
      const r = Math.min((lastQuery.query.radius || 600) + 400, 2000);
      run({ ...lastQuery.query, radius: r }, lastQuery.label);
    };
    cards.appendChild(wider);
  } else {
    cards.innerHTML = '';
    for (const it of data.items) cards.appendChild(card(it));
  }

  // 설계서 §10.1: 'A정류장 2개 / B정류장 1개' 구조가 한눈에 보여야 한다.
  // 그래야 첫 정류장을 놓쳤을 때 대안이 어디인지 바로 안다.
  const comp = $('composition');
  const counts = new Map();
  for (const it of data.items) {
    counts.set(it.board_stop_name, (counts.get(it.board_stop_name) || 0) + 1);
  }
  if (counts.size > 1) {
    comp.innerHTML = [...counts]
      .map(([name, n]) => `<b>${esc(name)}</b> ${n}`)
      .join(' · ');
    comp.hidden = false;
  } else {
    comp.hidden = true;
  }

  if (data.warning && data.items.length) {
    banner.textContent = data.warning + ' — 조금 뒤에 나가는 게 좋아요';
    banner.hidden = false;
  }
  startFreshness(data.generated_at);
}

const GRADE = {
  SAFE: '✓ 여유',
  TIGHT: '⚠ 서둘러야 함',
  RUN: '🏃 뛰면 잡음',
  MISS: '✕ 놓침',
};

function card(it) {
  const el = document.createElement('article');
  el.className = `card ${it.catch}`;

  const tags = [];
  if (it.is_last) tags.push('막차');
  if (it.congestion) tags.push(`혼잡도 ${it.congestion}`);
  if (it.is_estimated) tags.push('배차간격 추정');

  // RUN 이면 실제로 쓰는 시간은 뛰는 시간이다. 도보 시간도 같이 보여줘야
  // '안 뛰면 몇 분인지'를 알고 판단할 수 있다.
  const move = it.catch === 'RUN' && it.run_to_board_min !== null
    ? `뛰어서 ${it.run_to_board_min}분 <s>도보 ${it.walk_to_board_min}분</s>`
    : `도보 ${it.walk_to_board_min}분`;

  el.innerHTML = `
    <div class="stop-row">
      <button class="stop" type="button">${esc(it.board_stop_name)}</button>
      <span class="walk">${move}</span>
    </div>
    <span class="grade ${it.catch}">${GRADE[it.catch]} ${fmtMargin(it)}</span>
    <p class="route">${esc(it.route_name)}번 <span class="eta">${it.eta_min}분 후</span></p>
    <p class="leg">→ ${esc(it.alight_stop_name)} 하차, 도보 ${it.walk_from_alight_min}분</p>
    <p class="arrive">${it.arrive_at} 도착 · 총 ${it.total_min}분</p>
    ${tags.length ? `<p class="tags">${tags.map((t) => `<span>${esc(t)}</span>`).join('')}</p>` : ''}
  `;
  el.querySelector('.stop').onclick = () => walkTo(it);
  return el;
}

function fmtMargin(it) {
  // 0분은 적지 않는다. '뛰면 잡음 0분' 은 등급만 읽어도 아는 사실을 숫자로
  // 되풀이하면서 여유가 있다는 착각만 준다.
  if (it.catch === 'MISS' || it.margin_min <= 0) return '';
  return `${it.margin_min}분`;
}

// [F-18] 도보 안내는 지도앱에 위임한다. 지도를 직접 그리지 않는다.
function walkTo(it) {
  const { board_lat: la, board_lon: lo } = it;
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  location.href = ios
    ? `maps://?daddr=${la},${lo}&dirflg=w`
    : `https://www.google.com/maps/dir/?api=1&destination=${la},${lo}&travelmode=walking`;
}

/* ---------------------------------------------------------------- 신선도 */
function startFreshness(iso) {
  clearInterval(freshnessTimer);
  const at = new Date(iso);
  const tick = () => {
    const sec = Math.max(0, Math.round((Date.now() - at) / 1000));
    const el = $('freshness');
    el.textContent = sec < 60 ? `${sec}초 전` : `${Math.floor(sec / 60)}분 전`;
    el.classList.toggle('stale', sec > 30);
  };
  tick();
  freshnessTimer = setInterval(tick, 1000);
}

/* ---------------------------------------------------------------- 화면 */
function show(which) {
  for (const id of ['home', 'result', 'search']) $(id).hidden = id !== which;
  if (which !== 'result') clearInterval(freshnessTimer);
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ---------------------------------------------------------------- 시작 */
$('back').onclick = () => show('home');
$('refresh').onclick = () => lastQuery && run(lastQuery.query, lastQuery.label);
$('edit-token').onclick = () => { askToken(true); loadPlaces(); };
$('add-place').onclick = () => openSearch();

/* ------------------------------------------------------------ 장소 검색 */
// 좌표를 사람이 알 리가 없다. 이름으로 찾아 프로그램이 좌표를 가져온다 [F-19].
function openSearch() {
  show('search');
  $('hits').innerHTML = '<p class="empty">장소 이름이나 주소를 입력하세요</p>';
  $('search-q').value = '';
  $('search-q').focus();
}

$('search-back').onclick = () => show('home');

$('search-form').onsubmit = async (e) => {
  e.preventDefault();
  const q = $('search-q').value.trim();
  if (!q) return;
  const box = $('hits');

  // 좌표를 직접 붙여넣는 길은 남겨둔다. 지도앱에서 복사해 온 경우.
  const raw = q.match(/^\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*$/);
  if (raw) {
    return showHits([{ name: q, lat: +raw[1], lon: +raw[2], address: '직접 입력한 좌표', source: 'raw' }]);
  }

  box.innerHTML = '<div class="skeleton"></div>'.repeat(2);
  try {
    const params = { q };
    if (coords) { params.lat = coords.lat; params.lon = coords.lon; }
    showHits(await api('/api/geocode', params));
  } catch (err) {
    box.innerHTML = `<p class="empty">${esc(err.message)}</p>`;
  }
};

function showHits(hits) {
  const box = $('hits');
  box.innerHTML = '';
  if (!hits.length) {
    box.innerHTML = '<p class="empty">못 찾았어요. 다른 이름이나 주소로 찾아보세요</p>';
    return;
  }
  for (const h of hits) {
    const b = document.createElement('button');
    b.className = 'hit';
    b.type = 'button';
    const dist = h.distance_m !== null && h.distance_m !== undefined
      ? `<span class="hit-dist">${fmtDist(h.distance_m)}</span>` : '';
    b.innerHTML = `
      <span class="hit-name">${esc(h.name)}${h.source === 'stop' ? ' <em>정류장</em>' : ''}</span>
      <span class="hit-addr">${esc(h.address || '')}</span>${dist}`;
    b.onclick = () => savePlace(h);
    box.appendChild(b);
  }
}

function fmtDist(m) {
  return m >= 1000 ? `${(m / 1000).toFixed(1)}km` : `${m}m`;
}

async function savePlace(hit) {
  // 정류장 좌표를 출발지로 쓰면 도보 시간이 0 에 가깝게 잡혀 판정이 무의미해진다.
  if (hit.source === 'stop' &&
      !confirm(`'${hit.name}' 는 정류장 위치예요.\n출발지로 쓰면 도보 시간이 0분으로 잡혀요. 그대로 저장할까요?`)) {
    return;
  }
  const name = window.prompt('저장할 이름 (예: 집, 회사)', hit.name.slice(0, 20));
  if (!name || !name.trim()) return;
  try {
    await api('/api/places', null, { name: name.trim().slice(0, 20), lat: hit.lat, lon: hit.lon });
    show('home');
    loadPlaces();
  } catch (e) {
    alert(e.message);
  }
}

// 집처럼 '지금 내가 있는 곳'을 저장할 때가 제일 흔하다. 검색을 건너뛴다.
$('use-here').onclick = () => {
  if (!coords) return alert(gpsError || '아직 위치를 못 잡았어요');
  savePlace({
    name: '현재 위치',
    lat: coords.lat,
    lon: coords.lon,
    address: `오차 ${Math.round(coords.accuracy)}m`,
    source: 'gps',
  });
};

/* ---------------------------------------------------------------- SW */
// ?nosw=1 로 열면 등록을 해제한다. 캐시가 의심스러울 때 쓰는 탈출구.
if ('serviceWorker' in navigator) {
  if (new URLSearchParams(location.search).has('nosw')) {
    navigator.serviceWorker.getRegistrations()
      .then((rs) => Promise.all(rs.map((r) => r.unregister())))
      .then(() => caches.keys().then((ks) => Promise.all(ks.map((k) => caches.delete(k)))))
      .then(() => alert('서비스 워커와 캐시를 지웠어요. 새로고침하세요.'));
  } else {
    // 새 워커가 넘겨받으면(skipWaiting) 한 번만 새로고침한다. 그래야 폰에 떠 있던
    // 화면이 옛 app.js 그대로 남지 않는다. 홈 화면에 추가한 PWA 는 탭을 닫는
    // 일이 거의 없어서 이게 없으면 옛 화면이 며칠씩 살아 있는다.
    let reloadedOnce = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (reloadedOnce) return;
      reloadedOnce = true;
      location.reload();
    });
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch(() => {});
    });
  }
}

// 위치 요청이 먼저다. 즐겨찾기 로딩을 기다리게 하지 않는다.
startGps();
if (!getToken()) askToken(true);
loadPlaces();
