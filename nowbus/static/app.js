/* NowBus 프론트엔드.
 *
 * 상태는 모듈 스코프 변수 몇 개면 충분하다. 화면이 두 개뿐이라 라우터도
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

async function api(path, params) {
  const url = new URL(path, location.origin);
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== null && v !== undefined) url.searchParams.set(k, v);
  });
  const res = await fetch(url, { headers: { 'X-Token': getToken() } });
  if (res.status === 401) {
    askToken(true);
    throw new Error('토큰이 필요해요');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `요청 실패 (${res.status})`);
  }
  return res.json();
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

const GRADE = { SAFE: '✓ 여유', TIGHT: '⚠ 뛰어야 함', MISS: '✕ 놓침' };

function card(it) {
  const el = document.createElement('article');
  el.className = `card ${it.catch}`;

  const tags = [];
  if (it.is_last) tags.push('막차');
  if (it.congestion) tags.push(`혼잡도 ${it.congestion}`);
  if (it.is_estimated) tags.push('배차간격 추정');

  el.innerHTML = `
    <div class="stop-row">
      <button class="stop" type="button">${esc(it.board_stop_name)}</button>
      <span class="walk">도보 ${it.walk_to_board_min}분</span>
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
  if (it.catch === 'MISS') return '';
  return it.margin_min >= 0 ? `${it.margin_min}분` : '';
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
  $('home').hidden = which !== 'home';
  $('result').hidden = which !== 'result';
  if (which === 'home') clearInterval(freshnessTimer);
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ---------------------------------------------------------------- 시작 */
$('back').onclick = () => show('home');
$('refresh').onclick = () => lastQuery && run(lastQuery.query, lastQuery.label);
$('edit-token').onclick = () => { askToken(true); loadPlaces(); };
$('add-place').onclick = async () => {
  const name = window.prompt('장소 이름 (예: 집)');
  if (!name) return;
  const here = coords ? `${coords.lat.toFixed(6)},${coords.lon.toFixed(6)}` : '';
  const raw = window.prompt('좌표 "위도,경도"', here);
  if (!raw) return;
  const [lat, lon] = raw.split(',').map((v) => parseFloat(v.trim()));
  if (Number.isNaN(lat) || Number.isNaN(lon)) return alert('좌표 형식이 잘못됐어요');
  try {
    await fetch('/api/places', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Token': getToken() },
      body: JSON.stringify({ name: name.trim(), lat, lon }),
    });
    loadPlaces();
  } catch (e) {
    alert(e.message);
  }
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
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch(() => {});
    });
  }
}

// 위치 요청이 먼저다. 즐겨찾기 로딩을 기다리게 하지 않는다.
startGps();
if (!getToken()) askToken(true);
loadPlaces();
