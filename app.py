# ======================================
# 🚑 실시간 GPS + 카카오 API 응급실 탐색
# ✅ 무작위 비가용 병원 반영 + 최적 병원 표시
# ✅ GPS 화면에서 결과 출력까지
# ======================================

import os, time, threading, random, math, requests
from flask import Flask, request, render_template_string, jsonify
from pyngrok import ngrok, conf

# ===== 설정 =====
NGROK_AUTHTOKEN = "33uQ9hZizsT26mrPEgDLjK7Sks9_xTWFXbjdvRoah6AaiPQz"
KAKAO_API_KEY = "589992c4b70f82eae97ba84fba5b4135"
PORT = int(os.environ.get("PORT", 5000))
coords = {"lat": None, "lon": None, "accuracy": None, "ts": None}

conf.get_default().auth_token = NGROK_AUTHTOKEN

# ===== Helper =====
WEIGHT_NARROW = 0.3
WEIGHT_ALLEY = 0.5

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
function log(msg) { document.getElementById('log').textContent = msg; }
function send(lat, lon, acc) {
  fetch('/update', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({lat,lon,accuracy:acc})})
    .then(r => r.json()).then(d => { if(d.hospitals) { log(d.hospitals); } });
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

    # ===== 카카오 API 호출 =====
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}
    params = {"query":"응급실", "x":lon, "y":lat, "radius":10000, "size":15, "sort":"distance"}

    try:
        res = requests.get(url, headers=headers, params=params, timeout=5)
        if res.status_code != 200:
            return jsonify(ok=False, hospitals="❌ 카카오 API 호출 실패")
        docs = res.json().get("documents", [])
        if not docs:
            return jsonify(ok=True, hospitals="⚠️ 검색된 응급실이 없습니다.")
        
        # ✅ 절대 변경 금지 키워드
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
            return jsonify(ok=True, hospitals="⚠️ 필터링 후 남은 병원이 없습니다.")

        # 🚫 무작위 비가용
        frac, unavail = assign_random_availability(hospitals, 0.5)

        # 🧮 소요 시간 계산
        for h in hospitals:
            if not h["available"]:
                h["weighted_time"] = math.inf
            else:
                h["weighted_time"] = compute_weighted_time(h["distance_m"], h["road_name"])

        avail = [h for h in hospitals if h["available"]]
        best = min(avail, key=lambda x: x["weighted_time"]) if avail else None

        # 🏥 결과 문자열 생성
        output = f"🚫 무작위로 {frac*100:.1f}% 병원 비가용 처리: {unavail}\n\n"
        output += "🚑 주변 응급실 (응급 관련 키워드 포함, 소요시간 빠른 순):\n\n"
        hospitals_sorted = sorted(hospitals, key=lambda x: x["weighted_time"])
        for i, h in enumerate(hospitals_sorted[:10], start=1):
            status = "가용" if h["available"] else "비가용"
            time_str = f"{h['weighted_time']:.1f}" if not math.isinf(h["weighted_time"]) else "N/A"
            output += f"{i}. {h['name']} | {h['address']} | 거리: {int(h['distance_m'])}m | 예상 소요: {time_str}분 | 상태: {status}\n"

        if best:
            output += f"\n🏆 최적의 응급실: {best['name']} | {best['address']} | 거리: {int(best['distance_m'])}m | 예상 소요: {best['weighted_time']:.1f}분"

        return jsonify(ok=True, hospitals=output)

    except Exception as e:
        return jsonify(ok=False, hospitals=f"❌ 카카오 API 호출 실패 (예외: {e})")

# ===== Flask 실행 =====
if __name__ == "__main__":
    try:
        public_url = ngrok.connect(PORT)
        print("📱 Render/로컬 접속 URL:", public_url)
    except Exception as e:
        print("❌ ngrok 연결 실패:", e)
    app.run(host="0.0.0.0", port=PORT, debug=False)
