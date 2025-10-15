import os, math, random, requests
from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)

# ===== Kakao API 키 환경변수에서 가져오기 =====
KAKAO_API_KEY = os.environ.get("KAKAO_API_KEY")

# ===== HTML 페이지 =====
HTML_PAGE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>응급실 탐색 시스템</title>
    <style>
        body { font-family: 'Noto Sans KR', sans-serif; text-align:center; margin-top:80px; }
        button { font-size:1.2em; padding:10px 20px; margin:10px; border-radius:8px; cursor:pointer; }
        #status { margin-top:20px; font-size:1.1em; }
    </style>
</head>
<body>
    <h2>🚑 실시간 응급실 탐색</h2>
    <button onclick="startTracking()">실시간 추적 시작</button>
    <p id="status">GPS 대기중...</p>

    <script>
    function startTracking() {
        if (!navigator.geolocation) {
            document.getElementById("status").innerText = "❌ GPS를 지원하지 않는 기기입니다.";
            return;
        }
        document.getElementById("status").innerText = "위치 추적 중...";
        navigator.geolocation.getCurrentPosition(pos => {
            fetch("/update", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ lat: pos.coords.latitude, lon: pos.coords.longitude })
            }).then(r => r.json()).then(data => {
                document.getElementById("status").innerText = "📍 위치 전송 완료";
                fetch("/nearby");
            });
        }, err => {
            document.getElementById("status").innerText = "❌ 위치 정보를 가져올 수 없습니다.";
        });
    }
    </script>
</body>
</html>
"""

# ===== Flask 전역 좌표 저장 =====
coords = {"lat": None, "lon": None}

# ===== 거리 계산 =====
def calc_distance(lat1, lon1, lat2, lon2):
    return math.sqrt((lat1 - lat2)**2 + (lon1 - lon2)**2) * 111  # km 근사

# ===== 카카오맵 장소 검색 (응급실) =====
def get_nearby_hospitals(lat, lon):
    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    params = {"query": "응급실", "y": lat, "x": lon, "radius": 5000}
    res = requests.get(url, headers=headers, params=params)
    if res.status_code != 200:
        print("❌ 응급실 정보 조회 실패:", res.text)
        return []
    return res.json().get("documents", [])

# ===== A* + GA 혼합 =====
def evaluate_hospitals(lat, lon, a_ratio=0.7, g_ratio=0.3):
    hospitals = get_nearby_hospitals(lat, lon)
    if not hospitals:
        print("❌ 주변 응급실 정보 없음.")
        return

    # 무작위 비가용 병원 설정
    unavailable_rate = round(random.uniform(20, 40), 1)
    unavail = random.sample(hospitals, max(1, int(len(hospitals) * unavailable_rate / 100)))
    for h in hospitals:
        h["available"] = h not in unavail

    # 시간 계산
    for h in hospitals:
        h_lat, h_lon = float(h["y"]), float(h["x"])
        h["distance_km"] = calc_distance(lat, lon, h_lat, h_lon)
        h["time_min"] = h["distance_km"] / 0.5  # 0.5 km/min ≈ 30km/h

    # GA 보정값 적용
    for h in hospitals:
        h["ga_factor"] = random.uniform(0.5, 1.0)
        h["final_score"] = (
            (a_ratio * (1 / h["time_min"])) + (g_ratio * h["ga_factor"])
            if h["available"]
            else 0
        )

    # 최적 병원 선택
    best = max(hospitals, key=lambda x: x["final_score"])

    # ===== 콘솔 출력 =====
    print(f"\n📍 출발지 위치: lat={lat:.3f}, lon={lon:.3f}")
    print(f"🚫 무작위로 {unavailable_rate}% 병원 비가용 처리: {[h['place_name'] for h in unavail]}")
    print("\n=== 병원 평가 결과 (분 단위) ===")
    for i, h in enumerate(hospitals, start=1):
        status = "가용" if h["available"] else "비가용"
        time_str = f"{h['time_min']:.1f}분" if h["available"] else "N/A분"
        print(f"{i}. {h['place_name']} | {status} | {time_str}")

    print(f"\n🏥 최적 병원: {best['place_name']} (예상 소요 {best['time_min']:.1f}분)\n")


# ===== Flask 라우팅 =====
@app.route("/")
def index():
    return render_template_string(HTML_PAGE)

@app.route("/update", methods=["POST"])
def update_location():
    data = request.get_json()
    coords["lat"] = data.get("lat")
    coords["lon"] = data.get("lon")
    print(f"📡 현재 좌표 갱신됨 → {coords}")
    return jsonify(success=True)

@app.route("/nearby")
def nearby():
    if coords["lat"] and coords["lon"]:
        evaluate_hospitals(coords["lat"], coords["lon"])
        return jsonify(success=True)
    return jsonify(success=False)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
