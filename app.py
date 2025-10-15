# ================================================
# 🚑 Ambulance Route Optimization (Hybrid: A* 70% + GA 30%)
# ✅ 실시간 GPS + 카카오 API (app.py용)
# ✅ "응급실 정보 조회 실패" 문제 해결
# ✅ 반경 자동 재시도 로직 추가 (15km → 25km → 35km)
# ✅ 나머지 로직 및 출력 구조 동일
# ================================================

import os
import time
import random
import math
import requests
from flask import Flask, request, render_template_string, jsonify

# ===== 환경변수에서 API 키 가져오기 =====
KAKAO_API_KEY = os.environ.get("KAKAO_API_KEY")
PORT = int(os.environ.get("PORT", 5000))

# ===== 전역 상태 =====
coords = {"lat": None, "lon": None, "accuracy": None, "ts": None}
cached_unavail = None

# ===== 가중치 =====
WEIGHT_NARROW = 0.3
WEIGHT_ALLEY = 0.5
A_STAR_WEIGHT = 0.7
GA_WEIGHT = 0.3

# -------------------------------------------------
# Helper functions
# -------------------------------------------------
def compute_weighted_time(distance_m, road_name=""):
    time_min = distance_m / (45_000 / 60)
    penalty = 0
    if any(k in road_name for k in ["골목", "이면", "소로"]):
        penalty += WEIGHT_ALLEY
    elif "좁" in road_name:
        penalty += WEIGHT_NARROW
    return time_min * (1 + penalty)

def assign_random_availability(hospitals, max_unavail_frac=0.4):
    global cached_unavail
    if cached_unavail is not None:
        for h in hospitals:
            h["available"] = (h["name"] not in cached_unavail)
            h["status"] = "가용" if h["available"] else "비가용"
        return list(cached_unavail)

    if not hospitals:
        cached_unavail = []
        return cached_unavail

    frac = random.uniform(0, max_unavail_frac)
    num_unavail = int(len(hospitals) * frac)
    unavail = random.sample(hospitals, num_unavail) if num_unavail > 0 else []
    cached_unavail = [h["name"] for h in unavail]

    for h in hospitals:
        h["available"] = (h["name"] not in cached_unavail)
        h["status"] = "가용" if h["available"] else "비가용"

    print(f"[assign_random_availability] num_unavail={num_unavail}, cached_unavail={cached_unavail}")
    return list(cached_unavail)

def select_best_GA(hospitals, pop_size=10, gens=5, mutation_rate=0.2):
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

# -------------------------------------------------
# Flask App
# -------------------------------------------------
app = Flask(__name__)

HTML = """<!doctype html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>실시간 GPS → 응급실 검색</title>
<style>body{font-family:sans-serif;padding:16px;}button{font-size:18px;padding:10px 14px;border-radius:8px;cursor:pointer;margin-right:8px;}#log{margin-top:10px;white-space:pre-line;}#hospitals{margin-top:16px;}</style>
</head><body>
<h2>📍 실시간 GPS 전송 & 주변 응급실</h2>
<button id="startBtn">실시간 추적 시작</button><button id="stopBtn" disabled>정지</button>
<div id="log">대기 중…</div><div id="hospitals"></div>
<script>
let watchId=null;
function log(m){document.getElementById('log').textContent=m;}
function send(lat,lon,acc){fetch('/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lat,lon,accuracy:acc})});}
function fetchNearby(){
  fetch('/nearby').then(r=>r.json()).then(d=>{
    const div=document.getElementById('hospitals');
    if(!d.ok){div.innerHTML='<p style="color:red">⚠️ '+(d.error||'조회 실패')+'</p>';return;}
    let html='<h3>🚑 주변 응급실 (빠른 순)</h3>';
    html+='<p>🚫 비가용 병원: '+(d.unavail?.length?d.unavail.join(', '):'없음')+'</p><ol>';
    d.hospitals.forEach((h,i)=>{html+=`<li>${i+1}. ${h.name} | 거리 ${h.distance}m | ${h.time_min.toFixed(1)}분 | ${h.status}</li>`});
    html+='</ol>';
    if(d.best){const b=d.best;html+=`<p>🏥 최적 병원: ${b.name} (${b.distance}m, ${b.time_min.toFixed(1)}분)</p>`;}
    div.innerHTML=html;
  }).catch(()=>{document.getElementById('hospitals').innerHTML='<p style="color:red">❌ 서버 오류</p>';});
}
document.getElementById('startBtn').onclick=()=>{
  if(!navigator.geolocation){log('❌ GPS 미지원');return;}
  document.getElementById('startBtn').disabled=true;document.getElementById('stopBtn').disabled=false;
  log('⏳ 위치 전송 중...');
  watchId=navigator.geolocation.watchPosition(p=>{
    const lat=p.coords.latitude.toFixed(6),lon=p.coords.longitude.toFixed(6),acc=Math.round(p.coords.accuracy||0);
    log('✅ 위도 '+lat+' 경도 '+lon+' 오차 ±'+acc+'m');
    send(lat,lon,acc);fetchNearby();
  },e=>log('❌ '+e.message),{enableHighAccuracy:true,timeout:10000});
};
document.getElementById('stopBtn').onclick=()=>{if(watchId){navigator.geolocation.clearWatch(watchId);watchId=null;}
  document.getElementById('startBtn').disabled=false;document.getElementById('stopBtn').disabled=true;log('⏹ 중지됨');};
</script></body></html>"""

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
    except Exception:
        return jsonify(ok=False, error="Invalid data"), 400
    coords.update({"lat": lat, "lon": lon, "accuracy": acc, "ts": time.time()})
    print(f"[update] lat={lat}, lon={lon}")
    return jsonify(ok=True)

@app.route("/nearby")
def nearby():
    if not KAKAO_API_KEY:
        return jsonify(ok=False, error="KAKAO_API_KEY 미설정"), 400
    if coords["lat"] is None or coords["lon"] is None:
        return jsonify(ok=False, error="위치 정보 없음"), 400

    lat, lon = coords["lat"], coords["lon"]
    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"

    # 반경 15km → 25km → 35km 순서로 재시도
    docs = []
    for radius in [15000, 25000, 35000]:
        params = {"query": "응급실", "x": str(lon), "y": str(lat), "radius": radius, "size": 15, "sort": "distance"}
        try:
            print(f"[nearby] 요청 중: radius={radius}")
            res = requests.get(url, headers=headers, params=params, timeout=6)
            res.raise_for_status()
            data = res.json()
            docs = data.get("documents", [])
            if docs:
                break
        except Exception as e:
            print(f"[nearby] Kakao API 오류 ({radius}m): {e}")
            continue

    if not docs:
        return jsonify(ok=False, error="응급실 정보를 찾을 수 없음"), 404

    exclude_keywords = ["동물", "치과", "한의원", "약국", "카페", "편의점", "이송", "은행", "의원"]
    include_keywords = ["응급", "응급실", "병원", "의료센터", "응급의료", "응급센터", "대학병원"]

    hospitals = []
    for d in docs:
        name = d.get("place_name")
        if not name or any(x in name for x in exclude_keywords):
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
        return jsonify(ok=False, error="필터링 후 남은 병원 없음"), 404

    unavail_list = assign_random_availability(hospitals)
    for h in hospitals:
        if h.get("available", True):
            h["weighted_time"] = compute_weighted_time(h["distance_m"], h.get("road_name", ""))
        else:
            h["weighted_time"] = math.inf

    best_ga = select_best_GA(hospitals)
    best_a_star = min((h for h in hospitals if h.get("available", False)), key=lambda x: x["weighted_time"], default=None)
    best_final = best_a_star if random.random() < A_STAR_WEIGHT else best_ga or best_a_star

    hospitals_sorted = sorted(hospitals, key=lambda x: x["weighted_time"])
    hospitals_out = [{
        "name": h["name"],
        "address": h.get("address", ""),
        "distance": int(h["distance_m"]),
        "time_min": h["weighted_time"] if math.isfinite(h["weighted_time"]) else float("inf"),
        "status": h.get("status", "가용")
    } for h in hospitals_sorted[:10]]

    best_out = None
    if best_final:
        best_out = {
            "name": best_final["name"],
            "address": best_final.get("address", ""),
            "distance": int(best_final.get("distance_m", 0)),
            "time_min": best_final.get("weighted_time", float("inf"))
        }

    print(f"[nearby] 결과 {len(hospitals_out)}개 반환, best={best_out['name'] if best_out else None}")
    return jsonify(ok=True, hospitals=hospitals_out, best=best_out, unavail=unavail_list)

# -------------------------------------------------
# Run Flask
# -------------------------------------------------
if __name__ == "__main__":
    print("=== Flask 실행 ===")
    if KAKAO_API_KEY:
        print("✅ Kakao API key loaded")
    else:
        print("⚠️ Kakao API key not set")
    print(f"서버 실행 중: http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT)
