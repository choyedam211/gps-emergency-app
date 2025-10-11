import os, time, random, requests
from flask import Flask, request, render_template_string, jsonify

# ===== 환경변수에서 API 키 가져오기 =====
KAKAO_API_KEY = os.environ.get("KAKAO_API_KEY")
PORT = int(os.environ.get("PORT", 5000))  # Render에서 할당

coords = {"lat": None, "lon": None, "accuracy": None, "ts": None}

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
      let html = '<h3>🚑 주변 응급실 (예상 소요 빠른 순)</h3><ol>';
      data.hospitals.forEach(h=>{
        html += `<li>${h.name} | ${h.address} | 거리: ${h.distance}m | 예상 소요: ${h.time_min.toFixed(1)}분</li>`;
      });
      html += '</ol>';
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
    response_local = requests.get(url_local, headers=headers, params=params_local)
    result_local = response_local.json()

    hospitals = []
    exclude_keywords = ["동물", "치과", "한의원", "약국", "떡볶이", "카페", "편의점", "이송", "은행", "의원"]

    for doc in result_local.get("documents", []):
        name = doc["place_name"]
        if any(k.lower() in name.lower() for k in exclude_keywords):
            continue
        address = doc.get("road_address_name") or doc.get("address_name")
        distance = int(doc.get("distance", 0))
        speed_kmh = random.uniform(40,50)
        speed_m_per_s = speed_kmh * 1000 / 3600
        estimated_time_min = distance / speed_m_per_s / 60
        hospitals.append({
            "name": name,
            "address": address,
            "distance": distance,
            "time_min": estimated_time_min
        })
    hospitals.sort(key=lambda x: x["time_min"])
    return jsonify(ok=True, hospitals=hospitals[:10])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
