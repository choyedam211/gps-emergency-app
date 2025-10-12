# ======================================
# 🚑 실시간 GPS + 카카오 API 응급실 탐색 (app.py)
# ✅ 무작위 비가용 병원 반영 + 최적 병원 표시
# ======================================

import os, time, random, math, requests
from flask import Flask, request, render_template_string, jsonify

# ===== 환경변수에서 API 키 가져오기 =====
KAKAO_API_KEY = os.environ.get("KAKAO_API_KEY")
PORT = int(os.environ.get("PORT", 5000))  # Render에서 할당

coords = {"lat": None, "lon": None, "accuracy": None, "ts": None}
hospitals_state = None  # 무작위 비가용 병원 고정 저장

# ===== Helper =====
WEIGHT_NARROW = 0.3
WEIGHT_ALLEY = 0.5

def assign_random_availability(hospitals, max_unavail_frac=0.5):
    global hospitals_state
    if hospitals_state is not None:
        return hospitals_state
    frac = random.uniform(0, max_unavail_frac)
    num_unavail = int(len(hospitals) * frac)
    unavail = random.sample(hospitals, num_unavail) if num_unavail else []
    unavail_names = []
    for h in hospitals:
        if h in unavail:
            h["available"] = False
            unavail_names.append(h["name"])
        else:
            h["available"] = True
    hospitals_state = (frac, unavail_names)
    return hospitals_state

def compute_weighted_time(distance_m, road_name=""):
    """거리 기반 시간 계산 (평균 45km/h) + 골목 가중치"""
    time_min = distance_m / (45_000 / 60)
    penalty = 0
    if any(k in road_name for k in ["골목", "이면", "소로"]):
        penalty += WEIGHT_ALLEY
    elif "좁" in road_name:
        penalty += WEIGHT_NARROW
    return time_min * (1 + penalty)

# ===== Flask 앱 =====
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
<p>아래 버튼 누른 뒤, <b>위치 권한</b>을 <b>허용</b>하세요.</p>
<button id="startBtn">실시간 추적 시작</button>
<button id="stopBtn" disabled>정지</button>
<div id="log">대기 중…</div>
<div id="hospitals"></div>
<script>
let watchId = null;
function log(msg) { document.getElementById('log').textContent = msg; }

function send(lat, lon, acc) {
  fetch('/update', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({lat,lon,accuracy:acc})
  }).catch(e=>{});
}

function fetchNearby() {
  fetch('/nearby')
    .then(r=>r.json())
    .then(data=>{
      const div = document.getElementById('hospitals');
      if(!data.ok) {
        div.innerHTML = '⚠️ 주변 응급실 정보 없음';
        return;
      }
      let html = '';
      if(data.unavail && data.unavail.length>0){
          html += '🚫 비가용 병원: ' + data.unavail.join(', ') + '<br><br>';
      }
      html += '<h3>🚑 주변 응급실 (응급 관련 키워드 포함, 소요시간 빠른 순)</h3><ol>';
      data.hospitals.forEach(h=>{
        html += `<li>${h.name} | ${h.address} | 거리: ${h.distance}m | 예상 소요: ${h.time_min.toFixed(1)}분 | 상태: ${h.status}</li>`;
      });
      html += '</ol>';
      if(data.best){
          html += `<br>🏆 최적의 응급실: ${data.best.name} | ${data.best.address} | 거리: ${data.best.distance}m | 예상 소요: ${data.best.time_min.toFixed(1)}분`;
      }
      div.innerHTML = html;
    }).catch(e=>{
      document.getElementById('hospitals').innerHTML = '❌ 주변 응급실 조회 실패';
    });
}

document.getElementById('startBtn').onclick = () => {
  if(!navigator.geolocation){log('❌ GPS 미지원'); return;}
  document.getElementById('startBtn').disabled=true;
  document.getElementById('stopBtn').disabled=false;
  log('⏳ 위치 권한 요청 중…');

  watchId = navigator.geolocation.watchPosition(
    pos => {
      const lat=pos.coords.latitude;
      const lon=pos.coords.longitude;
      const acc=Math.round(pos.coords.accuracy);
      log('✅ 전송됨 → 위도 '+lat+', 경도 '+lon+' (±'+acc+'m)');
      send(lat,lon,acc);
      fetchNearby(); // 좌표 전송 후 주변 응급실 조회
    },
    err => { log('❌ 실패: '+err.message); },
    {enableHighAccuracy:true, maximumAge:0, timeout:10000}
  );
};

document.getElementById('stopBtn').onclick = () => {
  if(watchId!==null){navigator.geolocation.clearWatch(watchId); watchId=null;}
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
        lat = float(data.get("lat"))
        lon = float(data.get("lon"))
        acc = float(data.get("accuracy")) if data.get("accuracy") else None
    except:
        return jsonify(ok=False,error="bad payload"),400
    coords.update({"lat":lat,"lon":lon,"accuracy":acc,"ts":time.time()})
    return jsonify(ok=True)

@app.route("/nearby")
def nearby():
    if coords["lat"] is None:
        return jsonify(ok=False,error="좌표 없음")
    
    url_local = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}
    params_local = {
        "query": "응급실",
        "x": coords["lon"],
        "y": coords["lat"],
        "radius": 10000,
        "size": 15,
        "sort": "distance"
    }

    try:
        response_local = requests.get(url_local, headers=headers, params=params_local, timeout=5)
        result_local = response_local.json()
    except:
        return jsonify(ok=False,error="API 호출 실패")

    exclude_keywords = ["동물", "치과", "한의원", "약국", "떡볶이", "카페", "편의점", "이송", "은행", "의원"]
    include_keywords = ["응급", "응급실", "응급의료", "의료센터", "병원", "대학병원", "응급센터", "응급의료센터"]

    hospitals = []
    for doc in result_local.get("documents", []):
        name = doc["place_name"]
        if any(x.lower() in name.lower() for x in exclude_keywords):
            continue
        if not any(x.lower() in name.lower() for x in include_keywords):
            continue
        hospitals.append({
            "name": name,
            "address": doc.get("road_address_name") or doc.get("address_name",""),
            "distance_m": float(doc.get("distance",0)),
            "road_name": doc.get("road_address_name","")
        })

    if not hospitals:
        return jsonify(ok=False,error="응급실 없음")

    # 🚫 무작위 비가용 병원 적용
    frac, unavail = assign_random_availability(hospitals, 0.5)

    # 🧮 소요 시간 계산 및 상태
    for h in hospitals:
        if h["available"]:
            h["weighted_time"] = compute_weighted_time(h["distance_m"], h["road_name"])
            h["status"] = "가용"
        else:
            h["weighted_time"] = math.inf
            h["status"] = "비가용"

    avail = [h for h in hospitals if h["available"]]
    best = min(avail, key=lambda x: x["weighted_time"]) if avail else None

    # 정렬 및 출력
    hospitals_sorted = sorted(hospitals, key=lambda x: x["weighted_time"])
    hospitals_out = []
    for h in hospitals_sorted[:10]:
        hospitals_out.append({
            "name": h["name"],
            "address": h["address"],
            "distance": int(h["distance_m"]),
            "time_min": h["weighted_time"],
            "status": h["status"]
        })

    best_out = None
    if best:
        best_out = {
            "name": best["name"],
            "address": best["address"],
            "distance": int(best["distance_m"]),
            "time_min": best["weighted_time"]
        }

    return jsonify(ok=True, hospitals=hospitals_out, best=best_out, unavail=unavail)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
