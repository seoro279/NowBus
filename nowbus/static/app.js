/* NowBus 프론트엔드.
 *
 * 상태는 모듈 스코프 변수 몇 개면 충분하다. 화면이 세 개뿐이라 라우터도
 * 상태관리 라이브러리도 얹지 않는다.
 */
'use strict';

let coords = null;       // {lat, lon, accuracy} - 진입 즉시 백그라운드로 채운다
let gpsError = null;
let places = [];
let editing = false;   // 홈의 편집 모드. 이때만 삭제 버튼이 존재한다
let currentDest = null;  // 결과 화면의 목적지. {name, lat, lon, saved, source}
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

async function api(path, params, body, method) {
  const url = new URL(path, location.origin);
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== null && v !== undefined) url.searchParams.set(k, v);
  });
  const init = { method: method || 'GET', headers: { 'X-Token': getToken() } };
  if (body !== undefined) {
    init.method = method || 'POST';
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, init);
  } catch {
    // fetch 는 HTTP 오류가 아니라 '서버에 닿지 못했을 때'만 여기로 온다. 사파리는
    // 이걸 'Load failed' 라고만 알려줘서 원인을 짐작할 수 없었다. 화면은 서비스 워커
    // 캐시로 멀쩡히 뜨므로, 서버가 죽었다는 걸 여기서 분명히 말해야 한다.
    throw new Error('서버에 연결할 수 없어요. 맥이 잠자기 중이거나 서버가 꺼져 있을 수 있어요');
  }
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
  editing = false;
  $('edit-places').textContent = '편집';
  try {
    places = await api('/api/places');
  } catch (e) {
    box.innerHTML = `<p class="empty">${esc(e.message)}</p>`;
    $('edit-places').hidden = true;
    return;
  }
  if (!places.length) {
    box.innerHTML = '<p class="empty small">아직 없어요. 검색해서 간 곳을 ☆ 로 저장할 수 있어요</p>';
    $('edit-places').hidden = true;
    return;
  }
  box.innerHTML = '';
  for (const p of places) {
    box.appendChild(placeRow(p));
  }
  $('edit-places').hidden = false;
}

// 한 줄 = 목적지 버튼 (+ 편집 모드일 때 삭제 버튼).
function placeRow(p) {
  const row = document.createElement('div');
  row.className = editing ? 'place-row editing' : 'place-row';

  const b = document.createElement('button');
  b.className = 'btn';
  b.textContent = p.name;
  b.onclick = () => onDestination({ ...p, saved: true });
  row.appendChild(b);

  if (editing) {
    const del = document.createElement('button');
    del.className = 'del';
    del.type = 'button';
    del.textContent = '✕';
    del.setAttribute('aria-label', `${p.name} 삭제`);
    del.onclick = () => removePlace(p);
    row.appendChild(del);
  }
  return row;
}

// 평소엔 삭제 버튼을 만들지 않는다. 아침에 목적지를 탭하다 잘못 누르면 안 된다.
function setEditing(on) {
  editing = on;
  $('edit-places').textContent = on ? '완료' : '편집';
  const box = $('places');
  box.innerHTML = '';
  for (const p of places) box.appendChild(placeRow(p));
}

// 즐겨찾기 하나를 지운다. 지웠으면 true.
// 홈의 ✕ 와 결과 화면의 ★ 가 같이 쓴다 - 어디서 지우든 같은 문구로 묻는다.
async function deleteFavorite(name) {
  if (!confirm(`'${name}' 을 목록에서 지울까요?`)) return false;
  try {
    await api('/api/places', { name }, undefined, 'DELETE');
  } catch (e) {
    alert(e.message);
    return false;
  }
  places = places.filter((x) => x.name !== name);
  return true;
}

async function removePlace(p) {
  if (!(await deleteFavorite(p.name))) return;
  if (!places.length) {
    setEditing(false);
    return loadPlaces();
  }
  setEditing(editing);
}

// 목적지 하나로 경로를 조회한다. 즐겨찾기든 검색 결과든 여기로 온다.
// 즐겨찾기는 이름으로(서버가 좌표를 찾는다), 검색 결과는 좌표로 넘긴다 -
// 검색 결과를 즐겨찾기에 먼저 넣게 하지 않는 게 이 함수의 요점이다.
function destParams(dest) {
  return dest.saved
    ? { to: dest.name }
    : { to_lat: dest.lat, to_lon: dest.lon, to_name: dest.name };
}

// GPS 가 없으면 출발지를 먼저 물어본다 (설계서 §10.3 폴백).
function onDestination(dest) {
  currentDest = dest;
  const to = destParams(dest);
  if (coords) return run({ lat: coords.lat, lon: coords.lon, ...to }, `현재 위치 → ${dest.name}`);
  const others = places.filter((p) => p.name !== dest.name);
  if (!others.length) return alert(gpsError || '출발지를 알 수 없어요');
  const names = others.map((p, i) => `${i + 1}. ${p.name}`).join('\n');
  const pick = window.prompt(`출발지를 골라주세요\n${names}`, '1');
  const from = others[Number(pick) - 1];
  if (!from) return;
  run({ from: from.name, ...to }, `${from.name} → ${dest.name}`);
}

/* ---------------------------------------------------------------- 즐겨찾기 별 */
// 이미 저장된 곳인지는 좌표로 본다. 이름은 사용자가 바꿔 저장하므로 기준이 못 된다.
// 약 11m 격자 - 같은 건물을 두 번 저장하는 걸 막을 정도면 된다.
function findSaved(dest) {
  const key = (x) => `${x.lat.toFixed(4)},${x.lon.toFixed(4)}`;
  return places.find((p) => key(p) === key(dest)) || null;
}

// 지금 목적지가 즐겨찾기에 있으면 그 항목, 없으면 null.
function savedEntry() {
  if (!currentDest) return null;
  return currentDest.saved ? currentDest : findSaved(currentDest);
}

function updateStar() {
  const star = $('star');
  if (!currentDest) { star.hidden = true; return; }
  const saved = savedEntry();
  star.hidden = false;
  star.textContent = saved ? '★' : '☆';
  star.setAttribute('aria-label', saved ? `즐겨찾기에서 '${saved.name}' 빼기` : '즐겨찾기에 추가');
}

// ☆ 는 저장, ★ 는 삭제. 홈의 편집 모드까지 가지 않고 그 자리에서 되돌릴 수 있다.
$('star').onclick = async () => {
  if (!currentDest) return;
  const saved = savedEntry();

  if (saved) {
    if (!(await deleteFavorite(saved.name))) return;
    currentDest = { ...currentDest, saved: false };
    // 즐겨찾기 이름으로 조회하던 중이었다면 좌표로 바꿔 둔다. 그대로 두면 방금
    // 지운 이름을 찾느라 '새로고침' 이 404 가 난다.
    if (lastQuery && lastQuery.query.to === saved.name) {
      const { to, ...rest } = lastQuery.query;
      lastQuery.query = { ...rest, ...destParams(currentDest) };
    }
    updateStar();
    loadPlaces();  // 홈 목록을 뒤에서 갱신해 둔다
    return;
  }

  const name = await saveFavorite(currentDest);
  if (!name) return;
  currentDest = { ...currentDest, name, saved: true };
  updateStar();
};

/* ---------------------------------------------------------------- 결과 */
async function run(query, label) {
  lastQuery = { query, label };
  show('result');
  $('trip-label').textContent = label;
  $('banner').hidden = true;
  $('composition').hidden = true;
  $('sprint-box').hidden = true;
  updateStar();
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
  // 이름이 아니라 정류장 단위로 센다. 길 양쪽의 같은 이름 정류장을 이름으로 묶으면
  // '7단지영업소 3' 처럼 보여서 대안이 없는 것처럼 읽힌다. 이름이 겹칠 때만 방면을 붙인다.
  const comp = $('composition');
  const groups = new Map();
  for (const it of data.items) {
    const key = it.board_stop_id || it.board_stop_name;
    const g = groups.get(key) || { it, n: 0 };
    g.n += 1;
    groups.set(key, g);
  }
  if (groups.size > 1) {
    const nameCount = new Map();
    for (const { it } of groups.values()) {
      nameCount.set(it.board_stop_name, (nameCount.get(it.board_stop_name) || 0) + 1);
    }
    comp.innerHTML = [...groups.values()]
      .map(({ it, n }) => {
        const dup = nameCount.get(it.board_stop_name) > 1 && it.board_next_stop;
        const label = dup ? `${it.board_stop_name}(${it.board_next_stop} 방면)` : it.board_stop_name;
        return `<b>${esc(label)}</b> ${n}`;
      })
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

  // 길 양쪽에 이름이 같은 정류장이 있으면 이름만으로는 어느 쪽인지 모른다.
  // 표지판처럼 '다음 정류장 방면' 을 붙이고, 노선번호 옆에 행선판을 적는다.
  const heading = it.board_next_stop
    ? `<p class="heading">${esc(it.board_next_stop)} 방면</p>` : '';
  const bound = it.bound_for ? ` <span class="bound">${esc(it.bound_for)}행</span>` : '';

  el.innerHTML = `
    <div class="stop-row">
      <button class="stop" type="button">${esc(it.board_stop_name)}</button>
      <span class="walk">${move}</span>
    </div>
    ${heading}
    <span class="grade ${it.catch}">${GRADE[it.catch]} ${fmtMargin(it)}</span>
    <p class="route">${esc(it.route_name)}번${bound} <span class="eta">${it.eta_min}분 후</span></p>
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
$('edit-places').onclick = () => setEditing(!editing);
$('open-search').onclick = () => {
  setEditing(false);   // 검색하고 돌아왔을 때 편집 모드가 켜져 있으면 헷갈린다
  openSearch();
};

/* ------------------------------------------------------------ 장소 검색 */
// 좌표를 사람이 알 리가 없다. 이름으로 찾아 프로그램이 좌표를 가져온다 [F-19].
function openSearch() {
  show('search');
  $('hits').innerHTML = '<p class="empty">장소 이름이나 주소를 입력하세요.<br>누르면 바로 경로를 찾아요</p>';
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
    b.onclick = () => onDestination({ ...h, saved: false });
    box.appendChild(b);
  }
}

function fmtDist(m) {
  return m >= 1000 ? `${(m / 1000).toFixed(1)}km` : `${m}m`;
}

// 즐겨찾기에 저장한다. 저장한 이름을 돌려주고, 취소하면 null.
// 화면 이동은 하지 않는다 - 결과 화면의 ☆ 에서 부르면 그 자리에 있어야 한다.
async function saveFavorite(hit) {
  // 정류장 좌표를 출발지로 쓰면 도보 시간이 0 에 가깝게 잡혀 판정이 무의미해진다.
  if (hit.source === 'stop' &&
      !confirm(`'${hit.name}' 는 정류장 위치예요.\n출발지로 쓰면 도보 시간이 0분으로 잡혀요. 그대로 저장할까요?`)) {
    return null;
  }
  const name = window.prompt('즐겨찾기 이름 (예: 집, 회사)', hit.name.slice(0, 20));
  if (!name || !name.trim()) return null;
  const clean = name.trim().slice(0, 20);
  try {
    await api('/api/places', null, { name: clean, lat: hit.lat, lon: hit.lon });
  } catch (e) {
    alert(e.message);
    return null;
  }
  loadPlaces();  // 홈 목록을 뒤에서 갱신해 둔다
  return clean;
}

// 집처럼 '지금 내가 있는 곳'을 저장할 때가 제일 흔하다. 검색을 건너뛴다.
$('use-here').onclick = async () => {
  if (!coords) return alert(gpsError || '아직 위치를 못 잡았어요');
  const name = await saveFavorite({
    name: '현재 위치',
    lat: coords.lat,
    lon: coords.lon,
    source: 'gps',
  });
  if (name) show('home');
};

/* ---------------------------------------------------------------- 테마 */
// 자동(기기 설정) → 밝게 → 어둡게 순서로 돈다. 첫 페인트 전 적용은 index.html 의
// 인라인 스크립트가 하고, 여기서는 바꿀 때만 다시 칠한다.
const THEME_KEY = 'nowbus.theme';
const THEMES = ['auto', 'light', 'dark'];
const THEME_LABEL = { auto: '자동', light: '밝게', dark: '어둡게' };
const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');

function getTheme() {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return THEMES.includes(t) ? t : 'auto';
  } catch { return 'auto'; }
}

function applyTheme(t) {
  const root = document.documentElement;
  if (t === 'auto') delete root.dataset.theme;
  else root.dataset.theme = t;
  const dark = t === 'dark' || (t === 'auto' && darkQuery.matches);
  document.querySelector('meta[name="theme-color"]').content = dark ? '#111417' : '#f6f7f9';
  $('edit-theme').textContent = `화면: ${THEME_LABEL[t]}`;
}

$('edit-theme').onclick = () => {
  const next = THEMES[(THEMES.indexOf(getTheme()) + 1) % THEMES.length];
  try {
    if (next === 'auto') localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, next);
  } catch { /* 사파리 비공개 모드: 이번 세션에만 적용된다 */ }
  applyTheme(next);
};
// '자동' 일 때 기기의 다크모드가 바뀌면 따라간다 (CSS 는 알아서 따라가고, 이건 상단 색).
darkQuery.addEventListener?.('change', () => applyTheme(getTheme()));
applyTheme(getTheme());

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
