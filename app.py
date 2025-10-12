# ======================================
# 🚑 실시간 GPS + 카카오 API 응급실 탐색
# ✅ 무작위 비가용 병원 반영 + 최적 병원 표시
# ======================================

import os, time, random, math, requests
from flask import Flask, request, render_template_string, jsonify
from pyngrok import ngrok, conf

# ===== 설정 =====
NGROK_AUTHTOKEN = os.environ.get("NGROK_AUTHTOKEN", "YOUR_NGROK_AUTHTOKEN")
KAKAO_API_KEY = os.environ.get("KAKAO_API_KEY", "YOUR_KAKAO_API_KEY")
PORT = int(os.environ.get("PORT", 5000))
coords = {"lat": None, "lon": None, "accuracy": None, "ts": None}

conf.get_default().auth_token = NGROK_AUTHTOKEN

# ===== 상수 =====
WEIGHT_NARROW = 0.3
WEIGHT_ALLEY = 0.5
exclude_keywords = ["동물", "치과", "한의원", "약국", "떡볶이", "카페", "편의점", "이송", "은행", "의원"]
include_keywords = ["응급", "응급실", "응급의료", "의료센터", "병원", "대학병원", "응급센터", "응급의료센터"]

# ===== 헬퍼 =====
def assign_random_availability(hospitals, max_unavail_frac=0.5):
    frac = random.uniform(0, max_unavail_frac)
    num_unavail = int(len(hospitals) * frac)
    unavail = random.sample(hospitals, num_unavail) if num_unavail else []
    for h in hospitals:
        h["available"] = (h not in unavail)
    return frac, [h["name"] for h in unavail]

def compute_weighted_time(distance_m, road_name=""):
    time_min = distance_m / (45_000 / 60)
    penalty = 0
    if any(k in road_name for k in ["골목", "이면", "소로"]):
        penalty += WEIGHT_ALLEY
    elif "좁" in road_name:
        penalty += WEIGHT_NARROW
    return time_min * (1 + penalty)

def search_hospitals(lat, lon):
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}
    params = {
        "query": "응급실",
        "x": lon,
        "y": lat,
        "radius": 10000,
        "size": 15,
        "sort": "distance"
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=5)
        if res.status_code != 200:
            return {"error": f"카카오 API 호출 실패 (HTTP {res.status_code})"}
        docs = res.json().get("documents", [])
        hospitals = []
        for d in docs:
            name = d["place_name"]
            if any(x in name for x in exclude_keywords):
                continue
            if not any(x in name for x in include_keywords):
                continue
            hospitals.append({
                "name": name,
                "address": d.get("road_address_name") or d.get("address_name",""),
                "distance_m": float(d.get("distance", 0)),
                "road_name": d.get("road_address_name", "")
            })

        if not hospitals:
            return {"error": "필터링 후 남은 병원이 없습니다."}

        # 무작위 비가용
        frac, unavail = assign_random_availability(hospitals, 0.5)

        for h in hospitals:
            if not h["available"]:
                h["weighted_time"] = math.inf
            else:
                h["weighted_time"] = compute_weighted_time(h["distance_m"], h["road_name"])

        avail = [h for h in hospitals if h["available"]]
        best = min(avail, key=lambda x: x["weighted_time"]) if avail else None

        return {
            "unavailable_fraction": frac,
            "unavailable_list": unavail,
            "hospitals": hospitals,
            "best": best
        }

    except Exception as e:
        return {"error": f"카카오 API 호출 실패 (예외: {e})"}

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
#log { margin-top:12px; white-space:pre-line; max-height:400px; overflow:auto; }
</style>
</head>
<body>
<h2>📍 실시간 GPS 전송</h2>
<p>아래 버튼 누른 뒤, <b>위치 권한</b>을 <b>허용</b>하세요.</p>
<button id="startBtn">실시간 추적 시작</button>
<button id="stopBtn" disabled>정지</button>
<div id="log">대기 중…</div>
<script>
let watchId = null;
function log(msg) { document.getElementById('log').textContent += msg + "\\n"; }
function send(lat, lon, acc) {
  fetch('/update', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({lat,lon,accuracy:acc})
  })
  .then(res=>res.json())
  .then(data=>{
    if(data.error){ log("❌ "+data.error); return; }
    log("\\n🚫 무작위 비가용 병원 처리: "+(data.unavailable_fraction*100).toFixed(1)+"% → "+JSON.stringify(data.unavailable_list));
    log("🚑 주변 응급실 (빠른 순):");
    data.hospitals.forEach((h,i)=>{
      let time_str = h.weighted_time !== null && h.weighted_time !== Infinity ? h.weighted_time.toFixed(1) : "N/A";
      let status = h.available ? "가용":"비가용";
      log(`${i+1}. ${h.name} | ${h.address} | 거리: ${parseInt(h.distance_m)}m | 예상 소요: ${time_str}분 | 상태: ${status}`);
    });
    if(data.best){
      let b = data.best;
      let best_time = b.weighted_time !== null && b.weighted_time !== Infinity ? b.weighted_time.toFixed(1) : "N/A";
      log(`🏆 최적 응급실: ${b.name} | ${b.address} | 거리: ${parseInt(b.distance_m)}m | 예상 소요: ${best_time}분`);
    }
  })
  .catch(e=>log("❌ 서버 오류: "+e));
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
    results = search_hospitals(lat, lon)
    return jsonify(**results)

# ===== Flask 실행 =====
if __name__ == "__main__":
   
