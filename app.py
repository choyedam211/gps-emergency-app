# ================================================
# 🚑 Ambulance Route Optimization (Hybrid: A* 70% + GA 30%)
# ✅ 실시간 GPS + 카카오 API (app.py용)
# ✅ GA 후보 출력 생략, 병원 번호 표시
# ✅ ngrok 사용하지 않음 (로컬/서버에서 직접 실행)
# ✅ 무작위 비가용 병원은 한 번 지정되면 서버 재시작 전까지 고정 유지
# ================================================

import os
import time
import random
import math
import requests
from flask import Flask, request, render_template_string, jsonify

# ===== 환경변수에서 API 키 가져오기 (필수) =====
# 실행 전에 반드시 REST API 키를 환경변수로 설정하세요:
#   export KAKAO_API_KEY="여기에_REST_API_KEY"
KAKAO_API_KEY = os.environ.get("KAKAO_API_KEY")
PORT = int(os.environ.get("PORT", 5000))

# ===== 전역 상태 변수 =====
coords = {"lat": None, "lon": None, "accuracy": None, "ts": None}
cached_unavail = None   # 한 번 정해진 비가용 병원 이름 리스트를 여기 저장 (서버 재시작 전까지 유지)

# ===== 가중치 / 설정 =====
WEIGHT_NARROW = 0.3
WEIGHT_ALLEY = 0.5
A_STAR_WEIGHT = 0.7
GA_WEIGHT = 0.3

# ===================== HELPER FUNCTIONS =====================
def compute_weighted_time(distance_m, road_name=""):
    """
    거리 기반 시간 계산 (45 km/h 기준) + 도로명에 따른 페널티 적용
    distance_m: 미터 단위
    """
    # 분 단위로 계산: distance(km) / speed(km/h) * 60
    time_min = distance_m / (45_000 / 60)
    penalty = 0
    if any(k in road_name for k in ["골목", "이면", "소로"]):
        penalty += WEIGHT_ALLEY
    elif "좁" in road_name:
        penalty += WEIGHT_NARROW
    return time_min * (1 + penalty)

def assign_random_availability(hospitals, max_unavail_frac=0.4):
    """
    hospitals: 병원 dict 리스트 (각 항목에 'name' 키 존재)
    동작:
      - 전역 cached_unavail이 이미 정해져 있으면 그걸 사용해서 hospitals에 available/status 설정하고 반환
      - cached_unavail이 None이면 무작위로 일부 병원을 비가용 처리한 후 cached_unavail에 저장 (한번만)
    반환값:
      - 비가용 병원 이름 리스트 (빈 리스트 가능)
    """
    global cached_unavail

    # 이미 결정된 비가용 목록이 있으면 그대로 유지
    if cached_unavail is not None:
        for h in hospitals:
            h["available"] = (h["name"] not in cached_unavail)
            h["status"] = "가용" if h["available"] else "비가용"
        return list(cached_unavail)

    # 처음 호출되는 경우: 무작위로 선택
    if not hospitals:
        cached_unavail = []
        return cached_unavail

    # frac 범위: 0 ~ max_unavail_frac (0이면 아무도 비가용 아님)
    frac = random.uniform(0, max_unavail_frac)

    # --- 변경점: 반올림 사용 (0이 계속 나오는 현상을 완화하기 위함)
    num_unavail = int(round(len(hospitals) * frac))
    # sample 개수가 0이면 빈 리스트
    unavail = random.sample(hospitals, num_unavail) if num_unavail > 0 else []
    cached_unavail = [h["name"] for h in unavail]

    for h in hospitals:
        h["available"] = (h["name"] not in cached_unavail)
        h["status"] = "가용" if h["available"] else "비가용"

    # 로그
    print(f"[assign_random_availability] frac={frac:.3f}, num_unavail={num_unavail}, cached_unavail={cached_unavail}")
    return list(cached_unavail)

def select_best_GA(hospitals, pop_size=10, gens=5, mutation_rate=0.2):
    """
    단순한 GA: 가용한 병원 인덱스들로 permutation population 생성,
    적합도는 첫번째 유전자의 weighted_time 역수로 계산.
    (결과는 'best' 병원 dict 반환하거나 None)
    """
    available_indices = [i for i, h in enumerate(hospitals) if h.get("available", True)]
    if not available_indices:
        return None

    n = len(available_indices)
    population = [random.sample(available_indices, n) for _ in range(pop_size)]

    def fitness(chrom):
        first = hospitals[chrom[0]]
        wt = first.get("weighted_time", math.inf)
        if wt == math.inf:
            return 0
        return 1.0 / (wt + 1.0)

    for _ in range(gens):
        population.sort(key=fitness, reverse=True)
        next_gen = population[:2]  # 엘리트 유지
        while len(next_gen) < pop_size:
            p1, p2 = random.sample(population[:max(2, pop_size // 2)], 2)
            cut = random.randint(1, n - 1)
            child = p1[:cut] + [c for c in p2 if c not in p1[:cut]]
            if random.random() < mutation_rate and len(child) >= 2:
                i, j = random.sample(range(len(child)), 2)
                child[i], child[j] = child[j], child[i]
            next_gen.append(child)
        population = next_gen

    best_ch = max(population, key=fitness)
    return hospitals[best_ch[0]]

# ===================== FLASK APP / HTML =====================
app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>실시간 GPS → 응급실 검색</title>
<style>
body { font-family: system-ui, -apple-system, sans-serif; padding:16px; }
button { font-size:18px; padding:12px 16px; margin-right:8px; border-radius:8px; cursor:pointer;}
#log { margin-top:12px; white-space:pre-line; line-height:1.4; }
#hospitals { margin-top:16px; }
</style>
</head>
<body>
<h2>📍 실시간 GPS 전송 & 주변 응급실</h2>
<p>아래 버튼을 눌러 <b>위치 권한</b>을 허용하세요. (모바일/데스크탑 모두 가능)</p>
<button id="startBtn">실시간 추적 시작</button>
<button id="stopBtn" disabled>정지</button>
<div id="log">대기 중…</div>
<div id="hospitals"></div>

<script>
let watchId = null;
function log(msg){ document.getElementById('log').textContent = msg; }
function send(lat, lon, acc){
  fetch('/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({lat: lat, lon: lon, accuracy: acc})
  }).catch(e => {});
}
function fetchNearby(){
  fetch('/nearby')
    .then(r => r.json())
    .then(data => {
      const div = document.getElementById('hospitals');
      if(!data.ok){
        div.innerHTML = '<p style="color:crimson">⚠️ 주변 응급실 정보 없음 — ' + (data.error || '') + '</p>';
        return;
      }
      let html = '<h3>🚑 주변 응급실 (예상 소요 빠른 순)</h3>';
      html += '<p>🚫 비가용 병원: ' + (data.unavail && data.unavail.length ? data.unavail.join(', ') : '없음') + '</p>';
      html += '<ol>';
      data.hospitals.forEach((h) => {
        const timeStr = Number.isFinite(h.time_min) ? h.time_min.toFixed(1) + '분' : 'N/A';
        // 숫자 접두사 제거 — ol의 자동 번호만 사용
        html += `<li>${h.name} | ${h.address} | 거리: ${h.distance}m | 예상 소요: ${timeStr} | 상태: ${h.status}</li>`;
      });
      html += '</ol>';
      if(data.best){
        const b = data.best;
        html += `<p>🏥 최적 병원: ${b.name} | 거리: ${b.distance}m | 예상 소요: ${Number.isFinite(b.time_min) ? b.time_min.toFixed(1)+'분' : 'N/A'}</p>`;
      }
      div.innerHTML = html;
    })
    .catch(e => {
      document.getElementById('hospitals').innerHTML = '<p style="color:crimson">❌ 응급실 정보 조회 실패 (네트워크 또는 서버 오류)</p>';
    });
}

document.getElementById('startBtn').onclick = () => {
  if(!navigator.geolocation){ log('❌ GPS 미지원'); return; }
  document.getElementById('startBtn').disabled = true;
  document.getElementById('stopBtn').disabled = false;
  log('⏳ 위치 권한 요청 중...');
  watchId = navigator.geolocation.watchPosition(pos => {
    const lat = pos.coords.latitude.toFixed(6);
    const lon = pos.coords.longitude.toFixed(6);
    const acc = Math.round(pos.coords.accuracy || 0);
    log('✅ 위치 전송 중 → 위도: ' + lat + '\\n경도: ' + lon + '\\n오차: ±' + acc + 'm');
    send(lat, lon, acc);
    fetchNearby();
  }, err => {
    log('❌ 위치 수집 실패: ' + err.message);
  }, {enableHighAccuracy:true, maximumAge:0, timeout:10000});
};

document.getElementById('stopBtn').onclick = () => {
  if(watchId !== null){ navigator.geolocation.clearWatch(watchId); watchId = null; }
  document.getElementById('startBtn').disabled = false;
  document.getElementById('stopBtn').disabled = true;
  log('⏹ 추적 중지');
};
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/update", methods=["POST"])
def update():
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data.get("lat"))
        lon = float(data.get("lon"))
        acc = float(data.get("accuracy")) if data.get("accuracy") is not None else None
    except Exception:
        return jsonify(ok=False, error="invalid update payload"), 400
    coords.update({"lat": lat, "lon": lon, "accuracy": acc, "ts": time.time()})
    # 디버그 로그
    print(f"[update] coords updated: lat={lat}, lon={lon}, acc={acc}")
    return jsonify(ok=True)

@app.route("/nearby")
def nearby():
    # 기본 검증
    if not KAKAO_API_KEY:
        print("[nearby] Kakao API key not set (KAKAO_API_KEY env var missing).")
        return jsonify(ok=False, error="KAKAO_API_KEY 미설정"), 400
    if coords["lat"] is None or coords["lon"] is None:
        print("[nearby] coords not set yet.")
        return jsonify(ok=False, error="위치 정보 없음"), 400

    lat = coords["lat"]
    lon = coords["lon"]
    print(f"[nearby] called with coords lat={lat}, lon={lon}")

    # 카카오 로컬 검색 호출 (반경 넉넉히 15000~35000 재시도)
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}

    # 반경 15km → 25km → 35km 순서로 재시도
    docs = []
    for radius in (15000, 25000, 35000):
        params = {
            "query": "응급실",
            "x": str(lon),   # str으로 전달 (호환성 강화)
            "y": str(lat),
            "radius": radius,
            "size": 15,
            "sort": "distance"
        }
        try:
            print(f"[nearby] Kakao request radius={radius}")
            res = requests.get(url, headers=headers, params=params, timeout=6)
            print(f"[nearby] status {res.status_code}")
            res.raise_for_status()
            data = res.json()
            docs = data.get("documents", [])
            print(f"[nearby] documents returned: {len(docs)}")
            if docs:
                break
        except Exception as e:
            print(f"[nearby] Kakao API 호출 실패 (radius={radius}): {e}")
            # 계속 재시도 (다음 radius)
            continue

    if not docs:
        print("[nearby] 모든 반경에서 문서 없음")
        return jsonify(ok=False, error="응급실 정보를 찾을 수 없음"), 404

    # ------------------------
    # 여기부터는 네가 준 키워드를 절대 변경하지 않음
    # ------------------------
    exclude_keywords = ["동물","치과","한의원","약국","떡볶이","카페","편의점","이송","은행","의원"]
    include_keywords = ["응급","응급실","응급의료","의료센터","병원","대학병원","응급센터","응급의료센터"]

    hospitals = []
    for d in docs:
        name = d.get("place_name")
        if not name:
            continue
        if any(x in name for x in exclude_keywords):
            continue
        if not any(x in name for x in include_keywords):
            continue
        hospitals.append({
            "name": name,
            "address": d.get("road_address_name") or d.get("address_name", ""),
            "distance_m": float(d.get("distance", 0)),
            "road_name": d.get("road_address_name", "") or ""
        })

    if not hospitals:
        print("[nearby] 필터링 후 남은 병원 없음")
        return jsonify(ok=False, error="응급실 없음"), 404

    # ========== 비가용 병원 한 번만 결정 (서버 재시작 전까지 고정) ==========
    unavail_list = assign_random_availability(hospitals, max_unavail_frac=0.4)

    # ========== A* 역할: 거리 기반 가중 시간 계산 ==========
    for h in hospitals:
        if h.get("available", True):
            h["weighted_time"] = compute_weighted_time(h["distance_m"], h.get("road_name", ""))
        else:
            h["weighted_time"] = math.inf

    # ========== GA 후보 선택 (내부용, 출력 생략) ==========
    best_ga = select_best_GA(hospitals)

    # ========== 최종 선택 (A* 70% + GA 30% 방식 단순 적용) ==========
    best_a_star = min((h for h in hospitals if h.get("available", False)), key=lambda x: x["weighted_time"], default=None)
    best_final = None
    if best_a_star and best_ga:
        # 확률적으로 A* 우선 적용 (단순 예시)
        if random.random() < A_STAR_WEIGHT:
            best_final = best_a_star
        else:
            best_final = best_ga
    else:
        best_final = best_a_star or best_ga

    # 출력용 정렬 및 직렬화
    hospitals_sorted = sorted(hospitals, key=lambda x: (x["weighted_time"] if x["weighted_time"] is not None else math.inf))
    hospitals_out = []
    for h in hospitals_sorted[:10]:
        time_min = h["weighted_time"] if math.isfinite(h.get("weighted_time", math.inf)) else float("inf")
        hospitals_out.append({
            "name": h["name"],
            "address": h.get("address", ""),
            "distance": int(h.get("distance_m", 0)),
            "time_min": time_min,
            "status": h.get("status", "가용" if h.get("available", True) else "비가용")
        })

    best_out = None
    if best_final:
        best_out = {
            "name": best_final["name"],
            "address": best_final.get("address", ""),
            "distance": int(best_final.get("distance_m", 0)),
            "time_min": best_final.get("weighted_time", float("inf"))
        }

    print(f"[nearby] returning {len(hospitals_out)} hospitals, unavail={unavail_list}, best={best_out['name'] if best_out else None}")
    return jsonify(ok=True, hospitals=hospitals_out, best=best_out, unavail=unavail_list)

# ===================== Flask 실행 =====================
if __name__ == "__main__":
    print("=== Starting app.py ===")
    if KAKAO_API_KEY:
        print("KAKAO_API_KEY loaded (ok).")
    else:
        print("WARNING: KAKAO_API_KEY is NOT set. Set environment variable before running.")
    print(f"Server listening on port {PORT}. Open http://localhost:{PORT} in browser.")
    app.run(host="0.0.0.0", port=PORT)
