import os, time, random, math, requests
from flask import Flask, request, render_template_string, jsonify

# ===== 환경변수에서 API 키 가져오기 =====
KAKAO_API_KEY = os.environ.get("KAKAO_API_KEY")
PORT = int(os.environ.get("PORT", 5000))  # Render에서 할당

coords = {"lat": None, "lon": None, "accuracy": None, "ts": None}

# ===== Flask 앱 =====
app = Flask(__name__)

# ===== Helper =====
WEIGHT_NARROW = 0.3
WEIGHT_ALLEY = 0.5
hospitals_state = None  # 비가용 병원 상태 유지

def assign_random_availability(hospitals, max_unavail_frac=0.5):
    global hospitals_state
    if hospitals_state is not None:
        # 이미 상태가 설정돼 있으면 그대로 사용
        return hospitals_state
    frac = random.uniform(0, max_unavail_frac)
    num_unavail = int(len(hospitals) * frac)
    unavail = random.sample(hospitals, num_unavail) if num_unavail else []
    for h in hospitals:
        h["available"] = (h not in unavail)
    hospitals_state = (frac, [h["name"] for h in unavail])
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

# ===== HTML =====
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
      html += '<h3>🚑 주변 응급실 (예상 소요 빠른 순)</h3><ol>';
      data.hospitals.forEach((h,i)=>{
        html += `<li>${h.name} | ${h.address} | 거리: ${h.distance}m | 예상 소요: ${h.time_min.toFixed(1)}분 | 상태: ${h.status}</li>`;
      });
      html += '</ol>';
      if(data.best){
        html += `<br>🏆 최적 응급실: ${data.best.name} | ${data.best.address} | 거리: ${data.best.distance}m | 예상 소요: ${data.best.time_min.toFixed(1)}분`;
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
      const lat=pos.coords.latitude.toFixed(6);
      const lon=pos.coords.longitude.toFixed(6);
      const acc=Math.round(pos.coords.accuracy);
      log('✅ 전송됨 → 위도 '+lat+', 경도 '+lon+' (±'+acc+'m)');
      send(lat,lon,acc);
      fetchNearby();
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

# ===== Routes =====
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
        if res.status_code != 200:
            return jsonify(ok=False,error=f"API 호출 실패: {res.status_code}")
        docs = res.json().get("documents", [])
        if not docs:
            return jsonify(ok=False,error="검색된 응급실 없음")
        
        # ✅ 사용자 지정 필터
        exclude_keywords = ["동물", "치과", "한의원", "약국", "떡볶이", "카페", "편의점", "이송", "은행", "의원"]
        include_keywords = ["응급", "응급실", "응급의료", "의료센터", "병원", "대학병원", "응급센터", "응급의료센터"]

        hospitals = []
        for d in docs:
            name = d["place_name"]
            if any(k.lower() in name.lower() for k in exclude_keywords):
                continue
            if not any(k.lower() in name.lower() for k in include_keywords):
                continue
            hospitals.append({
                "name": name,
                "address": d.get("road_address_name") or d.get("address_name",""),
                "distance_m": float(d.get("distance",0)),
                "road_name": d.get("road_address_name","")
            })
        if not hospitals:
            return jsonify(ok=False,error="필터링 후 남은 병원 없음")

        # 🚫 무작위 비가용 유지
        frac, unavail = assign_random_availability(hospitals, 0.5)

        # 🧮 소요 시간 계산
        for h in hospitals:
            if h["name"] in unavail:
                h["available"] = False
                h["weighted_time"] = math.inf
                h["status"] = "비가용"
            else:
                h["available"] = True
                h["weighted_time"] = compute_weighted_time(h["distance_m"], h["road_name"])
                h["status"] = "가용"

        avail = [h for h in hospitals if h["available"]]
        best = min(avail, key=lambda x: x["weighted_time"]) if avail else None

        hospitals_sorted = sorted(hospitals, key=lambda x: x["weighted_time"])
        result = {
            "ok": True,
            "unavail": unavail,
            "hospitals": [
                {
                    "name": h["name"],
                    "address": h["address"],
                    "distance": int(h["distance_m"]),
                    "time_min": 0 if math.isinf(h["weighted_time"]) else h["weighted_time"],
                    "status": h["status"]
                } for h in hospitals_sorted[:10]
            ],
            "best": {
                "name": best["name"],
                "address": best["address"],
                "distance": int(best["distance_m"]),
                "time_min": best["weighted_time"]
            } if best else None
        }
        return jsonify(result)
    except Exception as e:
        return jsonify(ok=False,error=str(e))

# ===== Main =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
