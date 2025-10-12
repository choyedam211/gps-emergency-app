# ================================================# ================================================
# 🚑 Ambulance Route Optimization (Hybrid: A* 70% + GA 30%)
# ✅ 실시간 GPS + 카카오 API
# ✅ GA 후보 출력 생략, 병원 번호 표시
# ✅ ngrok 사용하지 않음 (app.py 실행 가능)
# ✅ 매번 새롭게 비가용 병원 랜덤 지정
# ================================================

import os, time, random, math, requests
from flask import Flask, request, render_template_string, jsonify

# ===== 환경변수에서 API 키 가져오기 =====
KAKAO_API_KEY = os.environ.get("KAKAO_API_KEY")  # ex) export KAKAO_API_KEY='키'
PORT = int(os.environ.get("PORT", 5000))

# ===== 전역 변수 =====
coords = {"lat": None, "lon": None, "accuracy": None, "ts": None}

# ===== 가중치 =====
WEIGHT_NARROW = 0.3
WEIGHT_ALLEY = 0.5
A_STAR_WEIGHT = 0.7
GA_WEIGHT = 0.3

# ===================== HELPER =====================
def compute_weighted_time(distance_m, road_name=""):
    """거리 기반 시간 계산 + 골목/좁은길 가중치"""
    time_min = distance_m / (45_000 / 60)  # 45km/h 기준
    penalty = 0
    if any(k in road_name for k in ["골목", "이면", "소로"]):
        penalty += WEIGHT_ALLEY
    elif "좁" in road_name:
        penalty += WEIGHT_NARROW
    return time_min * (1 + penalty)

def assign_random_availability(hospitals, max_unavail_frac=0.4):
    """일부 병원을 무작위로 비가용 처리"""
    frac = random.uniform(0, max_unavail_frac)
    num_unavail = int(len(hospitals) * frac)
    unavail = random.sample(hospitals, num_unavail) if num_unavail else []
    for h in hospitals:
        h["available"] = h not in unavail
        h["status"] = "가용" if h["available"] else "비가용"
    return [h["name"] for h in unavail]

def select_best_GA(hospitals, pop_size=10, gens=5, mutation_rate=0.2):
    """GA 방식으로 최적 후보 선택 (출력 생략)"""
    available = [i for i, h in enumerate(hospitals) if h.get("available", True)]
    if not available:
        return None
    n = len(available)
    population = [random.sample(available, n) for _ in range(pop_size)]

    def fitness(ch):
        first = hospitals[ch[0]]
        return 1 / (first.get("weighted_time", math.inf) + 1)

    for _ in range(gens):
        population.sort(key=fitness, reverse=True)
        next_gen = population[:2]
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

# ===================== Flask 앱 =====================
app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>실시간 GPS → 응급실 검색</title>
<style>
body { font-family: system-ui, -apple-system, sans-serif; padding:16px; }
button { font-size:18px; padding:12px 16px; margin-right:8px; }
#log { margin-top:12px; white-space:pre-line; }
#hospitals { margin-top:16px; }
</style>
</head>
<body>
<h2>📍 실시간 GPS 전송 & 주변 응급실</h2>
<p>아래 버튼을 눌러 <b>위치 권한</b>을 허용하세요.</p>
<button id="startBtn">실시간 추적 시작</button>
<button id="stopBtn" disabled>정지</button>
<div id="log">대기 중…</div>
<div id="hospitals"></div>
<script>
let watchId = null;
function log(msg){ document.getElementById('log').textContent = msg; }
function send(lat, lon, acc){
  fetch('/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lat,lon,accuracy:acc})}).catch(e=>{});
}
function fetchNearby(){
  fetch('/nearby').then(r=>r.json()).then(data=>{
    const div = document.getElementById('hospitals');
    if(!data.ok){ div.innerHTML='⚠️ 주변 응급실 정보 없음'; return; }
    let html = '<h3>🚑 주변 응급실 (예상 소요 빠른 순)</h3>';
    html += '<p>🚫 비가용 병원: ' + (data.unavail.length ? data.unavail.join(', ') : '없음') + '</p>';
    html += '<ol>';
    data.hospitals.forEach((h,i)=>{
      html += `<li>${i+1}. ${h.name} | ${h.address} | 거리: ${h.distance}m | 예상 소요: ${h.time_min.toFixed(1)}분 | 상태: ${h.status}</li>`;
    });
    html += '</ol>';
    if(data.best){
      html += `<p>🏆 최적 병원: ${data.best.name} (${data.best.distance}m, ${data.best.time_min.toFixed(1)}분)</p>`;
    }
    div.innerHTML = html;
  }).catch(e=>{
    document.getElementById('hospitals').innerHTML='❌ 응급실 정보 조회 실패';
  });
}
document.getElementById('startBtn').onclick = ()=>{
  if(!navigator.geolocation){ log('❌ GPS 미지원'); return; }
  document.getElementById('startBtn').disabled = true;
  document.getElementById('stopBtn').disabled = false;
  log('⏳ 위치 권한 요청 중...');
  watchId = navigator.geolocation.watchPosition(pos=>{
    const lat = pos.coords.latitude.toFixed(6);
    const lon = pos.coords.longitude.toFixed(6);
    const acc = Math.round(pos.coords.accuracy);
    log('✅ 위치 전송 중 → ' + lat + ', ' + lon + ' (±' + acc + 'm)');
    send(lat, lon, acc);
    fetchNearby();
  }, err=>{
    log('❌ 실패: ' + err.message);
  }, {enableHighAccuracy:true, maximumAge:0, timeout:10000});
};
document.getElementById('stopBtn').onclick = ()=>{
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
        acc = float(data.get("accuracy")) if data.get("accuracy") else None
    except:
        return jsonify(ok=False, error="invalid data"), 400
    coords.update({"lat": lat, "lon": lon, "accuracy": acc, "ts": time.time()})
    return jsonify(ok=True)

@app.route("/nearby")
def nearby():
    if not KAKAO_API_KEY:
        return jsonify(ok=False, error="KAKAO_API_KEY 미설정")
    if coords["lat"] is None or coords["lon"] is None:
        return jsonify(ok=False, error="위치 정보 없음")

    # 카카오 API로 주변 응급실 검색
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}
    params = {
        "query": "응급실",
        "x": coords["lon"],
        "y": coords["lat"],
        "radius": 10000,
        "size": 15,
        "sort": "distance"
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=5)
        res.raise_for_status()
        docs = res.json().get("documents", [])
    except Exception as e:
        return jsonify(ok=False, error=f"API 호출 실패: {e}")

    exclude_keywords = ["동물", "치과", "한의원", "약국", "편의점", "카페"]
    include_keywords = ["응급", "병원", "의료", "응급의료", "응급센터", "의료센터"]

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
            "road_name": d.get("road_address_name", "")
        })

    if not hospitals:
        return jsonify(ok=False, error="응급실 없음")

    unavail_list = assign_random_availability(hospitals, 0.5)

    for h in hospitals:
        if h["available"]:
            h["weighted_time"] = compute_weighted_time(h["distance_m"], h["road_name"])
        else:
            h["weighted_time"] = math.inf

    best_a_star = min((h for h in hospitals if h["available"]), key=lambda x: x["weighted_time"], default=None)
    best_ga = select_best_GA(hospitals)
    best_final = None
    if best_a_star and best_ga:
        if random.random() < A_STAR_WEIGHT:
            best_final = best_a_star
        else:
            best_final = best_ga
    else:
        best_final = best_a_star or best_ga

    hospitals_out = [{
        "name": h["name"],
        "address": h["address"],
        "distance": int(h["distance_m"]),
        "time_min": h["weighted_time"] if math.isfinite(h["weighted_time"]) else float("inf"),
        "status": h["status"]
    } for h in sorted(hospitals, key=lambda x: x["weighted_time"])[:10]]

    best_out = None
    if best_final:
        best_out = {
            "name": best_final["name"],
            "address": best_final["address"],
            "distance": int(best_final["distance_m"]),
            "time_min": best_final["weighted_time"]
        }

    return jsonify(ok=True, hospitals=hospitals_out, best=best_out, unavail=unavail_list)

# ===================== Flask 실행 =====================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)


