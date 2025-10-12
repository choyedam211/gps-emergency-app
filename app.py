import os, time, random, math, requests
from flask import Flask, request, render_template_string, jsonify

# ===== 환경변수에서 API 키 가져오기 =====
KAKAO_API_KEY = os.environ.get("KAKAO_API_KEY")
PORT = int(os.environ.get("PORT", 5000))  # Render에서 할당

coords = {"lat": None, "lon": None, "accuracy": None, "ts": None}

# ===== 고정 비가용 병원 저장 =====
fixed_unavail_hospitals = []

# ===== HELPER =====
WEIGHT_NARROW = 0.3
WEIGHT_ALLEY = 0.5

def assign_random_availability(hospitals, max_unavail_frac=0.5):
    """한 번만 무작위로 비가용 병원 지정"""
    global fixed_unavail_hospitals
    if fixed_unavail_hospitals:
        # 이미 고정되어 있으면 그대로 적용
        for h in hospitals:
            h["available"] = (h["name"] not in fixed_unavail_hospitals)
        return 0, fixed_unavail_hospitals
    frac = random.uniform(0, max_unavail_frac)
    num_unavail = int(len(hospitals) * frac)
    unavail = random.sample(hospitals, num_unavail) if num_unavail else []
    fixed_unavail_hospitals = [h["name"] for h in unavail]
    for h in hospitals:
        h["available"] = (h not in unavail)
    return frac, fixed_unavail_hospitals

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
#unavail { margin-top:16px; color:red; }
</style>
</head>
<body>
<h2>📍 실시간 GPS 전송 & 주변 응급실</h2>
<p>아래 버튼 누른 뒤, <b>위치 권한</b>을 <b>허용</b>하세요.</p>
<button id="startBtn">실시간 추적 시작</button>
<button id="stopBtn" disabled>정지</button>
<div id="log">대기 중…</div>
<div id="hospitals"></div>
<div id="unavail"></div>
<script>
let watchId = null;
function log(msg) { document.getElementById('log').textContent = msg; }
function send(lat, lon, acc) {
  fetch('/update', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({lat,lon,accuracy:acc})}).catch(e=>{});
}

function fetchNearby() {
  fetch('/nearby')
    .then(r=>r.json())
    .then(data=>{
      const div = document.getElementById('hospitals');
      if(!data.ok){ div.innerHTML = '⚠️ 주변 응급실 정보 없음'; return; }
      let html = '<h3>🚑 주변 응급실 (예상 소요 빠른 순)</h3><ol>';
      data.hospitals.forEach(h=>{
        html += `<li>${h.name} | ${h.address} | 거리: ${h.distance}m | 예상 소요: ${h.time_min.toFixed(1)}분 | 상태: ${h.status}</li>`;
      });
      html += '</ol>';
      div.innerHTML = html;

      // 무작위 비가용 병원 표시
      if(data.unavail && data.unavail.length>0){
        let unavail_html = '<h3>🚫 무작위 비가용 병원</h3><ul>';
        data.unavail.forEach(h => { unavail_html += `<li>${h}</li>`; });
        unavail_html += '</ul>';
        document.getElementById('unavail').innerHTML = unavail_html;
      }
    })
    .catch(e=>{ document.getElementById('hospitals').innerHTML = '❌ 주변 응급실 조회 실패'; });
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
            return jsonify(ok=False,error=f"HTTP {res.status_code}")

        docs = res.json().get("documents", [])
        if not docs:
            return jsonify(ok=False,error="검색된 응급실 없음")

        exclude_keywords = ["동물", "치과", "한의원", "약국", "떡볶이", "카페", "편의점", "이송", "은행", "의원"]
        include_keywords = ["응급", "응급실", "응급의료", "의료센터", "병원", "대학병원", "응급센터", "응급의료센터"]

        hospitals = []
        for d in docs:
            name = d["place_name"]
            if any(x in name for x in exclude_keywords): continue
            if not any(x in name for x in include_keywords): continue
            hospitals.append({
                "name": name,
                "address": d.get("road_address_name") or d.get("address_name",""),
                "distance_m": float(d.get("distance",0)),
                "road_name": d.get("road_address_name","")
            })

        if not hospitals:
            return jsonify(ok=False,error="필터링 후 병원 없음")

        # 🚫 무작위 비가용 반영
        frac, unavail_hospitals = assign_random_availability(hospitals, 0.5)

        # 🧮 소요 시간 계산
        for h in hospitals:
            if not h["available"]:
                h["weighted_time"] = math.inf
            else:
                h["weighted_time"] = compute_weighted_time(h["distance_m"], h["road_name"])
            h["status"] = "가용" if h["available"] else "비가용"

        hospitals_sorted = sorted(hospitals, key=lambda x: x["weighted_time"])
        best = next((h for h in hospitals_sorted if h["available"]), None)

        return jsonify(ok=True, hospitals=hospitals_sorted[:10], best=best, unavail=unavail_hospitals)

    except Exception as e:
        return jsonify(ok=False,error=str(e))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
