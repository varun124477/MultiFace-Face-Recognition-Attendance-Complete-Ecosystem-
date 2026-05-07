from flask import Flask, Response, request, jsonify, redirect
import numpy as np
import cv2
import boto3
import threading
import time
import io
import csv
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import urllib.request

import engine.gpu_attendance_engine as engine

app = Flask(__name__)

dynamodb         = boto3.resource("dynamodb", region_name="ap-south-1")
attendance_table = dynamodb.Table("Attendance")
students_table   = dynamodb.Table("Students")
sessions_table   = dynamodb.Table("Sessions")
teachers_table   = dynamodb.Table("Teachers")

# ── IST timezone ──
IST = timezone(timedelta(hours=5, minutes=30))

def ts_to_ist(ts):
    return datetime.fromtimestamp(ts, IST).strftime("%H:%M")

# ── ESP32 capture endpoint (same network only) ──
ESP32_IP          = "172.20.10.3"
ESP32_CAPTURE_URL = f"http://{ESP32_IP}:81/capture"

# ─────────────────────────────────────────
#  FRAME BUFFER
#  ESP32 pushes frames via POST /upload
#  /video_feed serves them as MJPEG stream
#  This works because EC2 never needs to
#  connect TO the ESP32 — ESP32 connects TO EC2
# ─────────────────────────────────────────
latest_frame     = None       # raw annotated frame bytes
latest_raw_frame = None       # raw unannotated frame bytes
frame_lock       = threading.Lock()
new_frame_event  = threading.Event()


def push_frame(annotated_bgr, raw_bgr=None):
    """Store latest annotated frame. Signal waiting clients."""
    global latest_frame, latest_raw_frame

    ret, buffer = cv2.imencode(
        '.jpg', annotated_bgr,
        [cv2.IMWRITE_JPEG_QUALITY, 70]
    )
    if ret:
        with frame_lock:
            latest_frame = buffer.tobytes()
            if raw_bgr is not None:
                ret2, buf2 = cv2.imencode(
                    '.jpg', raw_bgr,
                    [cv2.IMWRITE_JPEG_QUALITY, 70]
                )
                if ret2:
                    latest_raw_frame = buf2.tobytes()

        new_frame_event.set()
        new_frame_event.clear()


# ─────────────────────────────────────────
#  UPLOAD — ESP32 posts here
# ─────────────────────────────────────────

@app.route('/upload', methods=['POST'])
def upload():
    img_bytes = request.data
    npimg     = np.frombuffer(img_bytes, np.uint8)
    frame     = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
    if frame is None:
        return "Invalid image", 400

    # Resize if too large
    h, w = frame.shape[:2]
    if w > 640:
        frame = cv2.resize(frame, (640, int(h * 640 / w)))

    raw = frame.copy()
    annotated = engine.recognize_frame(frame)
    push_frame(annotated, raw)
    return "OK", 200


# ─────────────────────────────────────────
#  VIDEO FEED — annotated stream
#  Serves frames from push buffer
#  Works even when EC2 can't reach ESP32
# ─────────────────────────────────────────

def mjpeg_generator(use_annotated=True):
    """Yields MJPEG frames from the push buffer."""
    while True:
        # Wait for new frame — max 2s
        new_frame_event.wait(timeout=2.0)

        with frame_lock:
            frame = latest_frame if use_annotated else latest_raw_frame

        if frame is None:
            # Send a blank placeholder so browser doesn't hang
            continue

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')


@app.route("/video_feed")
def video_feed():
    """Annotated stream with green boxes — from push buffer."""
    return Response(
        mjpeg_generator(use_annotated=True),
        mimetype='multipart/x-mixed-replace; boundary=frame',
        headers={
            'Cache-Control':     'no-cache, no-store, must-revalidate',
            'Pragma':            'no-cache',
            'Expires':           '0',
            'X-Accel-Buffering': 'no'
        }
    )


@app.route("/video_feed_annotated")
def video_feed_annotated():
    """Alias — same as /video_feed."""
    return video_feed()


# ─────────────────────────────────────────
#  ESP32 CAPTURE — for registration
#  Server fetches from ESP32 /capture
#  Only works when on same network
#  Falls back to latest push buffer frame
# ─────────────────────────────────────────

@app.route("/api/capture_frame")
def capture_frame():
    try:
        req  = urllib.request.urlopen(ESP32_CAPTURE_URL, timeout=3)
        data = req.read()
        return Response(data, mimetype='image/jpeg')
    except Exception as e:
        print(f"ESP32 capture failed ({e}), using buffer frame")
        with frame_lock:
            frame = latest_raw_frame or latest_frame
        if frame:
            return Response(frame, mimetype='image/jpeg')
        return jsonify({"error": "No frame available — make sure ESP32 is uploading"}), 503


@app.route("/api/check_face", methods=["POST"])
def check_face():
    try:
        photo     = request.files.get('photo')
        if not photo:
            return jsonify({"error": "No photo"}), 400
        img_bytes = photo.read()
        npimg     = np.frombuffer(img_bytes, np.uint8)
        img       = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
        if img is None:
            return jsonify({"face_detected": False})
        faces = engine.app.get(img)
        return jsonify({"face_detected": len(faces) > 0, "count": len(faces)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────
#  STUDENT REGISTRATION
# ─────────────────────────────────────────

@app.route("/api/register_student", methods=["POST"])
def register_student():
    try:
        student_id = request.form.get("student_id", "").strip()
        name       = request.form.get("name", "").strip()
        roll       = request.form.get("roll", "").strip()
        batch      = request.form.get("batch", "").strip()
        photo      = request.files.get("photo")

        if not all([student_id, name, roll, batch]):
            return jsonify({"error": "Missing required fields"}), 400
        if not photo:
            return jsonify({"error": "Photo is required"}), 400

        img_bytes = photo.read()
        npimg     = np.frombuffer(img_bytes, np.uint8)
        img       = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
        if img is None:
            return jsonify({"error": "Invalid image"}), 400

        faces = engine.app.get(img)
        if not faces:
            return jsonify({"error": "No face detected. Use a clear front-facing photo."}), 400

        embedding = [Decimal(str(x)) for x in faces[0].embedding.tolist()]

        students_table.put_item(Item={
            "student_id": student_id,
            "name":       name,
            "roll":       roll,
            "batch":      batch,
            "embedding":  embedding,
        })
        engine.load_embeddings()
        return jsonify({"message": f"Student {name} registered successfully",
                        "embedding": "extracted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/delete_student/<student_id>", methods=["DELETE"])
def delete_student(student_id):
    try:
        students_table.delete_item(Key={"student_id": student_id})
        engine.load_embeddings()
        return jsonify({"message": "Student deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────
#  SESSION WINDOW STATUS
# ─────────────────────────────────────────

@app.route("/api/session_window_status")
def session_window_status():
    try:
        session = engine.get_active_session()
        if not session:
            return jsonify({"active": False})

        sid_key            = session["session_id"]
        win_idx, win_label = engine.get_active_window(session)
        dets               = engine.window_detections.get(sid_key, {})
        now_ts             = int(time.time())
        start_ts           = int(session["start_ts"])
        end_ts             = int(session["end_ts"])
        dur                = end_ts - start_ts

        windows_info = [
            {"label":"START","index":0,"count":len(dets.get(0,{})),
             "students":list(dets.get(0,{}).values()),
             "from":start_ts,"to":start_ts+600,
             "from_str":ts_to_ist(start_ts),"to_str":ts_to_ist(start_ts+600)},
            {"label":"MID","index":1,"count":len(dets.get(1,{})),
             "students":list(dets.get(1,{}).values()),
             "from":start_ts+dur//2-300,"to":start_ts+dur//2+300,
             "from_str":ts_to_ist(start_ts+dur//2-300),
             "to_str":ts_to_ist(start_ts+dur//2+300)},
            {"label":"END","index":2,"count":len(dets.get(2,{})),
             "students":list(dets.get(2,{}).values()),
             "from":end_ts-600,"to":end_ts,
             "from_str":ts_to_ist(end_ts-600),"to_str":ts_to_ist(end_ts)},
        ]

        return jsonify({
            "active":True,"session_id":sid_key,
            "subject":session.get("subject",""),
            "teacher":session.get("teacher",""),
            "room":session.get("room",""),
            "start_ts":start_ts,"end_ts":end_ts,"now_ts":now_ts,
            "active_window":win_label,"windows":windows_info
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────
#  ATTENDANCE APIs
# ─────────────────────────────────────────

@app.route("/api/attendance")
def api_attendance():
    try:
        records = attendance_table.scan().get("Items", [])
        records.sort(key=lambda x: x.get("timestamp",""), reverse=True)
        return jsonify(records)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/students")
def api_students():
    try:
        return jsonify(students_table.scan().get("Items", []))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/low_attendance")
def api_low_attendance():
    try:
        THRESHOLD = 75
        students  = {s["student_id"]: s for s in students_table.scan()["Items"]}
        records   = attendance_table.scan().get("Items", [])
        from collections import defaultdict
        count = defaultdict(set)
        for r in records:
            if r.get("status") == "present":
                count[r["student_id"]].add(r.get("date",""))
        low = []
        for sid, s in students.items():
            total    = int(s.get("total_classes", 30))
            attended = len(count[sid])
            pct      = round((attended/total)*100,1) if total else 0
            if pct < THRESHOLD:
                low.append({"name":s.get("name",""),"roll":s.get("roll",""),
                            "batch":s.get("batch",""),"student_id":sid,
                            "attended":attended,"total":total,"percentage":pct})
        low.sort(key=lambda x: x["percentage"])
        return jsonify(low)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/export_today_csv")
def export_today_csv():
    try:
        today         = datetime.now(IST).strftime("%Y-%m-%d")
        all_students  = {s["student_id"]:s for s in students_table.scan()["Items"]}
        all_records   = attendance_table.scan().get("Items",[])
        today_records = {r["student_id"]:r for r in all_records if r.get("date")==today}
        session       = engine.get_active_session()
        sid_key       = session["session_id"] if session else None
        dets          = engine.window_detections.get(sid_key,{}) if sid_key else {}

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Name","Roll No.","Batch","Student ID","Date","Time (IST)",
                         "Subject","Room","Teacher",
                         "Window 1 (START)","Window 2 (MID)","Window 3 (END)",
                         "Windows Seen","Status"])

        for sid, student in sorted(all_students.items(),
                                    key=lambda x: x[1].get("name","")):
            rec   = today_records.get(sid)
            name  = student.get("name","")
            roll  = student.get("roll","")
            batch = student.get("batch","")

            if rec:
                ts        = rec.get("timestamp","")
                date_part = ts.split(" ")[0] if " " in ts else today
                time_part = ts.split(" ")[1] if " " in ts else ""
                subject   = rec.get("subject","")
                room      = rec.get("room","")
                teacher   = rec.get("teacher","")
                wins_seen = rec.get("windows_seen",0)
                status    = rec.get("status","absent")
            else:
                date_part = today; time_part = ""
                subject   = session.get("subject","") if session else ""
                room      = session.get("room","")    if session else ""
                teacher   = session.get("teacher","") if session else ""
                wins_seen = 0; status = "absent"

            win1 = "✓" if sid in dets.get(0,{}) else "✗"
            win2 = "✓" if sid in dets.get(1,{}) else "✗"
            win3 = "✓" if sid in dets.get(2,{}) else "✗"

            writer.writerow([name,roll,batch,sid,date_part,time_part,
                             subject,room,teacher,win1,win2,win3,wins_seen,status])

        output.seek(0)
        filename = f"attendance_{today}.csv"
        return Response(output.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition":f"attachment; filename={filename}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────
#  SESSIONS
# ─────────────────────────────────────────

@app.route("/api/sessions", methods=["GET"])
def get_sessions():
    try:
        items = sessions_table.scan().get("Items",[])
        items.sort(key=lambda x: x.get("start_ts",0), reverse=True)
        return jsonify(items)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sessions", methods=["POST"])
def create_session():
    try:
        import uuid
        data         = request.get_json()
        subject      = data.get("subject","")
        teacher_id   = data.get("teacher_id","")
        teacher_name = data.get("teacher_name","")
        room         = data.get("room","")
        start_ts     = int(data.get("start_ts"))
        duration_min = int(data.get("duration_mins",55))
        duration_sec = duration_min * 60
        end_ts       = start_ts + duration_sec
        session_id   = str(uuid.uuid4())[:8]

        sessions_table.put_item(Item={
            "session_id":session_id,"subject":subject,
            "teacher_id":teacher_id,"teacher":teacher_name,
            "room":room,"start_ts":start_ts,"end_ts":end_ts,
            "duration_mins":duration_min,"status":"active",
            "created_at":int(time.time())
        })
        dur = duration_sec
        return jsonify({
            "message":"Session created","session_id":session_id,
            "windows":{"start":{"from":start_ts,"to":start_ts+600},
                       "mid":{"from":start_ts+dur//2-300,"to":start_ts+dur//2+300},
                       "end":{"from":end_ts-600,"to":end_ts}}
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def delete_session(session_id):
    try:
        sessions_table.delete_item(Key={"session_id":session_id})
        return jsonify({"message":"Session deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────
#  AUTH + TEACHERS
# ─────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def api_login():
    data     = request.get_json()
    username = data.get("username","").strip()
    password = data.get("password","")
    role     = data.get("role","teacher")
    if role == "admin":
        if username == "admin" and password == "Admin@1234":
            return jsonify({"role":"admin","username":username})
        return jsonify({"error":"Invalid admin credentials"}), 401
    else:
        try:
            for t in teachers_table.scan().get("Items",[]):
                if t.get("username")==username and t.get("password")==password:
                    return jsonify({"role":"teacher","username":username,
                                    "name":t.get("name",username)})
            return jsonify({"error":"Invalid teacher credentials"}), 401
        except Exception as e:
            return jsonify({"error": str(e)}), 500


@app.route("/api/register_teacher", methods=["POST"])
def register_teacher():
    try:
        data  = request.get_json()
        tid   = data.get("teacher_id","").strip()
        name  = data.get("name","").strip()
        uname = data.get("username","").strip()
        pwd   = data.get("password","")
        dept  = data.get("department","")
        subjs = data.get("subjects",[])
        if not all([tid,name,uname,pwd]):
            return jsonify({"error":"Missing fields"}), 400
        teachers_table.put_item(Item={
            "teacher_id":tid,"name":name,"username":uname,
            "password":pwd,"department":dept,"subjects":subjs
        })
        return jsonify({"message":f"Teacher {name} registered"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/teachers")
def get_teachers():
    try:
        items = teachers_table.scan().get("Items",[])
        return jsonify([{k:v for k,v in t.items() if k!="password"} for t in items])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/delete_teacher/<teacher_id>", methods=["DELETE"])
def delete_teacher(teacher_id):
    try:
        teachers_table.delete_item(Key={"teacher_id":teacher_id})
        return jsonify({"message":"Teacher deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────
#  PAGES
# ─────────────────────────────────────────

@app.route("/")
def index():
    return redirect("/login")

@app.route("/dashboard")
def dashboard():
    with open("templates/dashboard.html") as f: return f.read()

@app.route("/admin")
def admin_page():
    with open("templates/admin.html") as f: return f.read()

@app.route("/login")
def login_page():
    with open("templates/login.html") as f: return f.read()

@app.route("/report")
def report_page():
    with open("templates/report.html") as f: return f.read()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)
