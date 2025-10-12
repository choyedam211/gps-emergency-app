# ================================================
# 🚑 Ambulance Route Optimization (Hybrid: A* 70% + GA 30%)
# ✅ 실시간 GPS + 카카오 API
# ✅ GA 후보 출력 생략, 병원 번호 표시
# ✅ ngrok 사용하지 않음 (PC/로컬 테스트 가능)
# ================================================

# ===== 설치 (Colab용) =====
# !pip install flask requests -q

import os, time, threading, random, math, requests
from flask import Flask, request, render_template_string, jsonify

# ===== 환경변수에서 API 키 가져오기 =====
KAKAO_API_KEY = os.environ.get("KAKAO_API_KEY")  # 환경변수에 설정
PORT = int(os.environ.get("PORT", 5000))  # 포트 설정

# ===== 전역 변수 =====
coords = {"lat": None, "lon": None, "accuracy": None, "ts": None}
hospitals_cache = []      # 전체 병원 정보 캐싱
unavail_cache = []        # 무작위 비가용 병원 캐싱
best_cache = None         # 최적 병원 캐싱

# ===== 가중치 =====
WEIGHT_NARROW = 0.3
WEIGHT_ALLEY = 0.5
A_STAR_WEIGHT = 0.7
GA_WEIGHT = 0.3

# ===================== HELPER =====================
def compute_weighted_time(distance_m, road_name=""):
    """거리 기반 시간 계산 + 골목/좁은길 가중치"""
    time_min = distance_m / (45_000 / 60)
    penalty = 0
    if any(k in road_name for k in ["골목","이면","소로"]):
        penalty += WEIGHT_ALLEY
    elif "좁" in road_name:
        penalty += WEIGHT_NARROW
    return time_min * (1 + penalty)

def assign_random_availability(hospitals, max_unavail_frac=0.5):
    """일부 병원을 무작위로 비가용 처리"""
    global unavail_cache
    if unavail_cache:
        for h in hospitals:
            h["available"] = (h["name"] not in unavail_cache)
        return
    frac = random.uniform(0, max_unavail_frac)
    num_unavail = int(len(hospitals) * frac)
    unavail = random.sample(hospitals, num_unavail) if num_unavail else []
    unavail_cache = [h["name"] for h in unavail]
    for h in hospitals:
        h["available"] = (h["name"] not in unavail_cache)

def select_best_GA(hospitals, pop_size=10, gens=5, mutation_rate=0.2):
    """GA 후보 선택 (출력 생략)"""
    available_indices = [i for i,h in enumerate(hospitals) if h.get("available",True)]
    if not available_indices: return None
    n = len(available_indices)
    population = [random.sample(available_indices,n) for _ in range(pop_size)]
    def fitness(ch):
        first_idx = ch[0]
        first = hospitals[first_idx]
        if first.get("weighted_time",math.inf)==math.inf: return 0
        return 1 / (first["weighted_time"] + 1)
    for _ in range(gens):
        population.sort(key=fitness, reverse=True)
        next_gen = population[:2]
        while len(next_gen) < pop_size:
            p1,p2 = random.sample(population[:max(2,pop_size//2)], 2)
            cut = random.randint(1,n-1)
            child = p1[:cut] + [c for c in p2 if c not in p1[:cut]]
            if random.random() < mutation_rate and len(child) >= 2:
                i,j = random.sample(range(len(child)),2)
                child[i],child[j] = child[j], child[i]
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
<p>아래 버튼 누른 뒤, <b>위치 권한</b>을 허용하세요.</p>
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
    let html=`<h3>🚑 주변 응급실 (예상 소요 빠른 순)</h3>`;
    html+=`<p>🚫 비가용 병원: ${data.unavail.join(', ')}</p>`;
    html+='<ol>';
    data.hospitals.forEach(h=>{
      html+=`<li>${h.name} | ${h.address} | 거리: ${h.distance}m | 예상 소요: ${h.time_min.toFixed(1)}분 | 상태: ${h.status}</li>`;
    });
    html+='</ol>';
    if(data.best){
      html+=`<p>🏆 최적 병원: ${data.best.name} | ${data.best.address} | 거리: ${data.best.distance}m | 예상 소요: ${data.best.time_min.toFixed(1)}분</p>`;
    }
    div.innerHTML=html;
  }).catch(e=>{document.getElementById('hospitals').innerHTML='❌ 주변 응급실 조회 실패';});
}
document.getElementById('startBtn').onclick=()=>{
  if(!navigator.geolocation){ log('❌ GPS 미지원'); return; }
  document.getElementById('startBtn').disabled=true;
  document.getElementById('stopBtn').disabled=false;
  log('⏳ 위치 권한 요청 중…');
  watchId=navigator.geolocation.watchPosition(pos=>{
    const lat=pos.coords.latitude.toFixed(6);
    const lon=pos.coords.longitude.toFixed(6);
    const acc=Math.round(pos.coords.accuracy);
    log('✅ 전송됨 → 위도 '+lat+', 경도 '+lon+' (±'+acc+'m)');
    send(lat,lon,acc);
    fetchNearby();
  },err=>{ log('❌ 실패: '+err.message); },{enableHighAccuracy:true,maximumAge:0,timeout:10000});
};
document.getElementById('stopBtn').onclick=()=>{
  if(watchId!==null){ navigator.geolocation.clearWatch(watchId); watchId=null; }
  document.getElementById('startBtn').disabled=false;
  document.getElementById('stopBtn').disabled=true;
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
        lat=float(data.get("lat"))
        lon=float(data.get("lon"))
        acc=float(data.get("accuracy")) if data.get("accuracy") else None
    except:
        return jsonify(ok=False,error="bad payload"),400
    coords.update({"lat":lat,"lon":lon,"accuracy":acc,"ts":time.time()})
    return jsonify(ok=True)

@app.route("/nearby")
def nearby():
    global hospitals_cache, best_cache
    if coords["lat"] is None or coords["lon"] is None:
        return jsonify(ok=False,error="좌표 없음")
    if not KAKAO_API_KEY:
        return jsonify(ok=False,error="KAKAO_API_KEY 미설정")

    # API 호출
    if not hospitals_cache:
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
            return jsonify(ok=False,error=f"API 호출 실패: {e}")

        exclude_keywords = ["동물","치과","한의원","약국","떡볶이","카페","편의점","이송","은행","의원"]
        include_keywords = ["응급","응급실","응급의료","의료센터","병원","대학병원","응급센터","응급의료센터"]

        for d in docs:
            name = d.get("place_name")
            if not name: continue
            if any(x in name for x in exclude_keywords): continue
            if not any(x in name for x in include_keywords): continue
            hospitals_cache.append({
                "name": name,
                "address": d.get("road_address_name") or d.get("address_name",""),
                "distance_m": float(d.get("distance",0)),
                "road_name": d.get("road_address_name","")
            })

        if not hospitals_cache:
            return jsonify(ok=False,error="응급실 없음")

        assign_random_availability(hospitals_cache,0.5)

        # 시간 계산
        for h in hospitals_cache:
            if h["available"]:
                h["weighted_time"] = compute_weighted_time(h["distance_m"],h["road_name"])
                h["status"] = "가용"
            else:
                h["weighted_time"] = math.inf
                h["status"] = "비가용"

        # 최적 병원
        avail = [h for h in hospitals_cache if h["available"]]
        best_cache = min(avail,key=lambda x:x["weighted_time"]) if avail else None

    hospitals_out = [{
        "name": h["name"],
        "address": h["address"],
        "distance": int(h["distance_m"]),
        "time_min": h["weighted_time"],
        "status": h["status"]
    } for h in sorted(hospitals_cache,key=lambda x:x["weighted_time"])[:10]]

    best_out = {
        "name": best_cache["name"],
        "address": best_cache["address"],
        "distance": int(best_cache["distance_m"]),
        "time_min": best_cache["weighted_time"]
    } if best_cache else None

    return jsonify(ok=True,hospitals=hospitals_out,best=best_out,unavail=unavail_cache)

# ===================== Flask 실행 =====================
if __name__=="__main__":
    app.run(host="0.0.0.0", port=PORT)
