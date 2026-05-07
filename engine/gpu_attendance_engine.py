import cv2
import numpy as np
import boto3
from insightface.app import FaceAnalysis
from datetime import datetime, timezone, timedelta
import time
import threading

# ---------- AWS ----------
dynamodb         = boto3.resource("dynamodb", region_name="ap-south-1")
students_table   = dynamodb.Table("Students")
attendance_table = dynamodb.Table("Attendance")
sessions_table   = dynamodb.Table("Sessions")

# ---------- IST ----------
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():   return datetime.now(IST)
def ist_str():   return now_ist().strftime("%Y-%m-%d %H:%M:%S")
def ist_date():  return now_ist().strftime("%Y-%m-%d")
def ist_time():  return now_ist().strftime("%H:%M:%S")
def ts_to_ist(ts): return datetime.fromtimestamp(ts, IST).strftime("%H:%M")

# ---------- InsightFace ----------
app = FaceAnalysis(name="buffalo_l")
app.prepare(ctx_id=0, det_size=(640, 640))

# ---------- Student embeddings ----------
students_embeddings = {}

def load_embeddings():
    global students_embeddings
    students_embeddings = {}
    for item in students_table.scan()["Items"]:
        if "embedding" not in item:
            continue
        emb = np.array(item["embedding"], dtype=np.float32)
        students_embeddings[item["student_id"]] = {
            "name":      item.get("name", "Unknown"),
            "roll":      item.get("roll", "Unknown"),
            "email":     item.get("email", ""),
            "embedding": emb
        }
    print(f"Loaded {len(students_embeddings)} students")

load_embeddings()

# ---------- Cooldown ----------
last_seen = {}
COOLDOWN  = 60

# ---------- Window detections ----------
window_detections  = {}
finalized_sessions = set()


def get_active_session():
    try:
        now_ts = int(time.time())
        for s in sessions_table.scan().get("Items", []):
            start = int(s.get("start_ts", 0))
            end   = int(s.get("end_ts", 0))
            if start <= now_ts <= end and s.get("status") == "active":
                return s
    except Exception as e:
        print("Session lookup error:", e)
    return None


def get_recently_ended_session():
    try:
        now_ts = int(time.time())
        for s in sessions_table.scan().get("Items", []):
            end_ts = int(s.get("end_ts", 0))
            status = s.get("status", "")
            sid    = s.get("session_id", "")
            if (status == "active" and
                    0 < now_ts - end_ts < 300 and
                    sid not in finalized_sessions):
                return s
    except Exception as e:
        print("Recent session lookup error:", e)
    return None


def get_active_window(session):
    if not session:
        return None, None
    now_ts   = int(time.time())
    start_ts = int(session["start_ts"])
    end_ts   = int(session["end_ts"])
    dur      = end_ts - start_ts
    win_len  = 10 * 60

    windows = [
        (start_ts,                       start_ts + win_len,              0, "START"),
        (start_ts + dur//2 - win_len//2, start_ts + dur//2 + win_len//2, 1, "MID"),
        (end_ts   - win_len,             end_ts,                          2, "END"),
    ]
    for w_start, w_end, idx, label in windows:
        if w_start <= now_ts <= w_end:
            return idx, label
    return None, None


def finalize_attendance(session_id, session):
    """
    Write final attendance to DynamoDB.
    Send emails to all students.
    """
    if session_id in finalized_sessions:
        print(f"Session {session_id} already finalized")
        return

    finalized_sessions.add(session_id)

    subject = session.get("subject", "Unknown")
    room    = session.get("room", "")
    teacher = session.get("teacher", "")
    batch   = session.get("batch", "")
    date    = ist_date()
    dets    = window_detections.get(session_id, {})

    print(f"\n{'='*50}")
    print(f"Finalizing: {session_id} | {subject}")

    # All students seen in any window
    all_seen = set()
    for win_set in dets.values():
        all_seen.update(win_set.keys())

    finalized_records = []

    # Students who were seen
    for sid in all_seen:
        count   = sum(1 for win_set in dets.values() if sid in win_set)
        status  = "present" if count >= 2 else "absent"
        student = students_embeddings.get(sid, {})
        name    = student.get("name", "Unknown")
        roll    = student.get("roll", "Unknown")
        email   = student.get("email", "")
        now_str = ist_str()

        attendance_table.put_item(Item={
            "student_id":   sid,
            "session_id":   session_id,
            "date":         date,
            "timestamp":    now_str,
            "subject":      subject,
            "room":         room,
            "teacher":      teacher,
            "batch":        batch,
            "name":         name,
            "roll":         roll,
            "email":        email,
            "windows_seen": count,
            "status":       status
        })
        print(f"  {name}: {status} ({count}/3 windows)")

        finalized_records.append({
            "student_id":   sid,
            "name":         name,
            "roll":         roll,
            "email":        email,
            "status":       status,
            "windows_seen": count
        })

    # Students not seen at all → mark absent
    for sid, student in students_embeddings.items():
        if sid not in all_seen:
            name  = student.get("name", "")
            roll  = student.get("roll", "")
            email = student.get("email", "")

            attendance_table.put_item(Item={
                "student_id":   sid,
                "session_id":   session_id,
                "date":         date,
                "timestamp":    ist_str(),
                "subject":      subject,
                "room":         room,
                "teacher":      teacher,
                "batch":        batch,
                "name":         name,
                "roll":         roll,
                "email":        email,
                "windows_seen": 0,
                "status":       "absent"
            })
            print(f"  {name}: absent (0/3 windows)")

            finalized_records.append({
                "student_id":   sid,
                "name":         name,
                "roll":         roll,
                "email":        email,
                "status":       "absent",
                "windows_seen": 0
            })

    # Mark session finalized in DynamoDB
    try:
        sessions_table.update_item(
            Key={"session_id": session_id},
            UpdateExpression="SET #st = :s",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":s": "finalized"}
        )
    except Exception as e:
        print("Session finalize update error:", e)

    print(f"Session {session_id} finalized — {len(finalized_records)} records")
    print('='*50 + '\n')

    # ── Send emails in background thread ──
    # Don't block the main loop
    dets_copy = {k: dict(v) for k, v in dets.items()}  # snapshot before cleanup

    def send_emails_async():
        try:
            from email_service import send_session_emails
            send_session_emails(session, finalized_records, dets_copy)
        except Exception as e:
            print(f"Email sending error: {e}")

    email_thread = threading.Thread(target=send_emails_async, daemon=True)
    email_thread.start()

    # Clean up memory
    if session_id in window_detections:
        del window_detections[session_id]


# ── Background finalizer ──
def background_finalizer():
    while True:
        try:
            ended = get_recently_ended_session()
            if ended:
                print(f"Background finalizer: finalizing {ended['session_id']}...")
                finalize_attendance(ended["session_id"], ended)
        except Exception as e:
            print(f"Background finalizer error: {e}")
        time.sleep(30)

threading.Thread(target=background_finalizer, daemon=True,
                 name="SessionFinalizer").start()
print("Background session finalizer started")


def draw_label(frame, label, x1, y1, x2, y2, color):
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    label_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
    lw, lh = label_size
    label_y = max(y1, lh + 10)
    cv2.rectangle(frame, (x1, label_y - lh - 10),
                  (x1 + lw + 8, label_y), color, -1)
    cv2.putText(frame, label, (x1 + 4, label_y - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2, cv2.LINE_AA)


def recognize_frame(frame):
    faces = app.get(frame)
    if not faces:
        return frame

    session            = get_active_session()
    win_idx, win_label = get_active_window(session)

    # Stream overlay
    ist_now = now_ist().strftime("%H:%M:%S IST")
    if session:
        subj = session.get("subject", "")
        if win_label:
            overlay_text  = f"{subj} | {win_label} | {ist_now}"
            overlay_color = (0, 255, 0)
        else:
            overlay_text  = f"{subj} | BETWEEN WINDOWS | {ist_now}"
            overlay_color = (0, 200, 255)
    else:
        overlay_text  = f"NO SESSION | {ist_now}"
        overlay_color = (100, 100, 100)

    cv2.putText(frame, overlay_text,
                (10, frame.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, overlay_color, 2, cv2.LINE_AA)

    for face in faces:
        emb        = face.embedding
        best_score = 0.0
        best_sid   = None

        for sid, data in students_embeddings.items():
            score = float(
                np.dot(emb, data["embedding"]) /
                (np.linalg.norm(emb) * np.linalg.norm(data["embedding"]))
            )
            if score > best_score:
                best_score = score
                best_sid   = sid

        bbox = face.bbox.astype(int)
        x1 = max(0, bbox[0]);  y1 = max(0, bbox[1])
        x2 = min(frame.shape[1], bbox[2])
        y2 = min(frame.shape[0], bbox[3])

        if best_score >= 0.5 and best_sid:
            student = students_embeddings[best_sid]
            name    = student["name"]
            label   = f"{name.title()} ({best_score:.2f})"
            color   = (0, 255, 0)

            if session and win_idx is not None:
                sid_key = session["session_id"]
                if sid_key not in window_detections:
                    window_detections[sid_key] = {}
                if win_idx not in window_detections[sid_key]:
                    window_detections[sid_key][win_idx] = {}
                if best_sid not in window_detections[sid_key][win_idx]:
                    detected_at = ist_time()
                    window_detections[sid_key][win_idx][best_sid] = {
                        "name":        name,
                        "roll":        student["roll"],
                        "detected_at": detected_at
                    }
                    print(f"✓ Window {win_label}: {name} @ {detected_at} IST")
        else:
            label = "Unknown"
            color = (0, 0, 255)

        draw_label(frame, label, x1, y1, x2, y2, color)

    return frame
