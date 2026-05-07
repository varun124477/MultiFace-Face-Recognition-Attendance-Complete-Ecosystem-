"""
AttendAI — Email Service via AWS SES
Sends attendance summary emails to students after session ends.

Setup steps:
1. Go to AWS SES Console → Verified identities
2. Verify your sender email address (e.g. attendai@yourdomain.com)
3. If in SES sandbox, also verify each recipient email
4. To send to any email (production), request SES production access
"""

import boto3
from datetime import datetime, timezone, timedelta

# ── Config ──────────────────────────────────────
SENDER_EMAIL    = "varun.124477@stu.upes.ac.in"   # ← change to your verified SES email
SENDER_NAME     = "AttendAI Attendance System"
AWS_REGION      = "ap-south-1"
IST             = timezone(timedelta(hours=5, minutes=30))
# ────────────────────────────────────────────────

ses_client = boto3.client("ses", region_name=AWS_REGION)


def send_attendance_email(student_name, student_email, session_info, status, windows_seen, window_details):
    """
    Send attendance result email to a student.

    Args:
        student_name   : str  — student's full name
        student_email  : str  — student's email address
        session_info   : dict — {subject, teacher, room, batch, date, start_time, end_time}
        status         : str  — "present" or "absent"
        windows_seen   : int  — number of windows detected (0-3)
        window_details : list — [{"label":"START","detected":True/False, "time":"HH:MM:SS"}, ...]
    """
    if not student_email:
        print(f"No email for {student_name} — skipping")
        return False

    subject_line = f"AttendAI: Attendance {'✓ Marked' if status=='present' else '✗ Absent'} — {session_info.get('subject','')}"

    html_body = build_html_email(
        student_name, session_info, status, windows_seen, window_details
    )
    text_body = build_text_email(
        student_name, session_info, status, windows_seen
    )

    try:
        ses_client.send_email(
            Source=f"{SENDER_NAME} <{SENDER_EMAIL}>",
            Destination={"ToAddresses": [student_email]},
            Message={
                "Subject": {"Data": subject_line, "Charset": "UTF-8"},
                "Body": {
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                }
            }
        )
        print(f"✉ Email sent to {student_name} ({student_email}) — {status}")
        return True

    except Exception as e:
        print(f"✗ Email failed for {student_name}: {e}")
        return False


def build_html_email(student_name, session_info, status, windows_seen, window_details):
    """Build a clean HTML email."""

    is_present   = status == "present"
    status_color = "#00e5a0" if is_present else "#f54b4b"
    status_bg    = "#0a2620" if is_present else "#2a0a0a"
    status_text  = "PRESENT ✓" if is_present else "ABSENT ✗"
    status_msg   = (
        "You have been marked <strong>present</strong> for this session."
        if is_present else
        "You have been marked <strong>absent</strong> for this session. "
        "If you believe this is an error, please contact your teacher."
    )

    # Window rows
    window_rows = ""
    for w in window_details:
        detected    = w.get("detected", False)
        w_color     = "#00e5a0" if detected else "#f54b4b"
        w_icon      = "✓" if detected else "✗"
        w_time      = w.get("time", "—") if detected else "Not detected"
        window_rows += f"""
        <tr>
          <td style="padding:10px 16px;border-bottom:1px solid #252836;font-family:'Courier New',monospace;font-size:13px;color:#9ca3af">{w.get('label','')}</td>
          <td style="padding:10px 16px;border-bottom:1px solid #252836;text-align:center">
            <span style="color:{w_color};font-weight:700;font-size:14px">{w_icon}</span>
          </td>
          <td style="padding:10px 16px;border-bottom:1px solid #252836;font-family:'Courier New',monospace;font-size:12px;color:{w_color}">{w_time}</td>
        </tr>"""

    date_str = session_info.get("date", datetime.now(IST).strftime("%Y-%m-%d"))
    name_cap = student_name.title()

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#0a0b0f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0b0f;padding:32px 16px">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%">

      <!-- Header -->
      <tr><td style="background:#12141a;border-radius:16px 16px 0 0;padding:28px 32px;border-bottom:1px solid #252836">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td><span style="font-family:'Courier New',monospace;font-size:22px;font-weight:700;color:#00e5a0;letter-spacing:-1px">AttendAI</span></td>
            <td align="right"><span style="font-size:11px;color:#6b7280;letter-spacing:2px;text-transform:uppercase">Attendance Report</span></td>
          </tr>
        </table>
      </td></tr>

      <!-- Status Banner -->
      <tr><td style="background:{status_bg};padding:24px 32px;border-bottom:1px solid #252836">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td>
              <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">Attendance Status</div>
              <div style="font-size:28px;font-weight:700;color:{status_color};font-family:'Courier New',monospace">{status_text}</div>
            </td>
            <td align="right">
              <div style="font-size:32px;font-weight:700;color:{status_color};font-family:'Courier New',monospace">{windows_seen}/3</div>
              <div style="font-size:11px;color:#6b7280">windows detected</div>
            </td>
          </tr>
        </table>
      </td></tr>

      <!-- Greeting -->
      <tr><td style="background:#1a1d26;padding:24px 32px;border-bottom:1px solid #252836">
        <p style="margin:0;font-size:15px;color:#e8eaf0">Dear <strong>{name_cap}</strong>,</p>
        <p style="margin:12px 0 0;font-size:14px;color:#9ca3af;line-height:1.6">{status_msg}</p>
      </td></tr>

      <!-- Session Info -->
      <tr><td style="background:#1a1d26;padding:0 32px 24px;border-bottom:1px solid #252836">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#12141a;border-radius:10px;overflow:hidden">
          <tr><td style="padding:12px 16px;border-bottom:1px solid #252836;background:#0a0b0f">
            <span style="font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:1px;font-family:'Courier New',monospace">Session Details</span>
          </td></tr>
          <tr><td style="padding:0">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding:10px 16px;border-bottom:1px solid #252836;font-size:12px;color:#6b7280;width:40%">Subject</td>
                <td style="padding:10px 16px;border-bottom:1px solid #252836;font-size:13px;color:#e8eaf0;font-weight:600">{session_info.get('subject','—')}</td>
              </tr>
              <tr>
                <td style="padding:10px 16px;border-bottom:1px solid #252836;font-size:12px;color:#6b7280">Teacher</td>
                <td style="padding:10px 16px;border-bottom:1px solid #252836;font-size:13px;color:#e8eaf0">{session_info.get('teacher','—').title()}</td>
              </tr>
              <tr>
                <td style="padding:10px 16px;border-bottom:1px solid #252836;font-size:12px;color:#6b7280">Room</td>
                <td style="padding:10px 16px;border-bottom:1px solid #252836;font-size:13px;color:#e8eaf0">{session_info.get('room','—')}</td>
              </tr>
              <tr>
                <td style="padding:10px 16px;border-bottom:1px solid #252836;font-size:12px;color:#6b7280">Batch</td>
                <td style="padding:10px 16px;border-bottom:1px solid #252836;font-size:13px;color:#5b6ef5">{session_info.get('batch','—')}</td>
              </tr>
              <tr>
                <td style="padding:10px 16px;border-bottom:1px solid #252836;font-size:12px;color:#6b7280">Date</td>
                <td style="padding:10px 16px;border-bottom:1px solid #252836;font-size:13px;color:#e8eaf0;font-family:'Courier New',monospace">{date_str}</td>
              </tr>
              <tr>
                <td style="padding:10px 16px;font-size:12px;color:#6b7280">Time</td>
                <td style="padding:10px 16px;font-size:13px;color:#e8eaf0;font-family:'Courier New',monospace">{session_info.get('start_time','—')} – {session_info.get('end_time','—')}</td>
              </tr>
            </table>
          </td></tr>
        </table>
      </td></tr>

      <!-- Window Detection -->
      <tr><td style="background:#1a1d26;padding:0 32px 24px">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#12141a;border-radius:10px;overflow:hidden;margin-top:0">
          <tr><td style="padding:12px 16px;border-bottom:1px solid #252836;background:#0a0b0f">
            <span style="font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:1px;font-family:'Courier New',monospace">Anti-Proxy Detection Windows</span>
          </td></tr>
          <tr><td style="padding:0">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr style="background:#0a0b0f">
                <th style="padding:8px 16px;text-align:left;font-size:10px;color:#6b7280;font-weight:500;text-transform:uppercase;letter-spacing:1px">Window</th>
                <th style="padding:8px 16px;text-align:center;font-size:10px;color:#6b7280;font-weight:500;text-transform:uppercase;letter-spacing:1px">Detected</th>
                <th style="padding:8px 16px;text-align:left;font-size:10px;color:#6b7280;font-weight:500;text-transform:uppercase;letter-spacing:1px">Time (IST)</th>
              </tr>
              {window_rows}
            </table>
          </td></tr>
        </table>
        <p style="margin:14px 0 0;font-size:12px;color:#6b7280;line-height:1.5">
          Present = detected in <strong style="color:#e8eaf0">2 or more</strong> of the 3 windows.
          Each window is 10 minutes long at the start, middle, and end of class.
        </p>
      </td></tr>

      <!-- Footer -->
      <tr><td style="background:#12141a;border-radius:0 0 16px 16px;padding:20px 32px;border-top:1px solid #252836">
        <p style="margin:0;font-size:12px;color:#6b7280;text-align:center">
          This is an automated message from AttendAI. Do not reply to this email.<br>
          <span style="color:#374151">© {datetime.now(IST).year} AttendAI — Face Recognition Attendance System</span>
        </p>
      </td></tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""


def build_text_email(student_name, session_info, status, windows_seen):
    """Plain text fallback."""
    return f"""AttendAI — Attendance Report
{'='*40}

Dear {student_name.title()},

Your attendance status: {status.upper()}
Windows detected: {windows_seen}/3

Session: {session_info.get('subject','—')}
Teacher: {session_info.get('teacher','—')}
Room:    {session_info.get('room','—')}
Batch:   {session_info.get('batch','—')}
Date:    {session_info.get('date','—')}
Time:    {session_info.get('start_time','—')} - {session_info.get('end_time','—')}

Present = detected in 2 or more of the 3 windows.

This is an automated message from AttendAI.
"""


def send_session_emails(session, attendance_records, window_detections_for_session):
    """
    Send emails to all students after a session ends.
    Called from finalize_attendance().

    Args:
        session                       : dict — session info from DynamoDB
        attendance_records            : list — finalized attendance records
        window_detections_for_session : dict — {win_idx: {student_id: {name,roll,detected_at}}}
    """
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))

    start_ts = int(session.get("start_ts", 0))
    end_ts   = int(session.get("end_ts", 0))

    session_info = {
        "subject":    session.get("subject", ""),
        "teacher":    session.get("teacher", ""),
        "room":       session.get("room", ""),
        "batch":      session.get("batch", ""),
        "date":       datetime.fromtimestamp(start_ts, IST).strftime("%d %B %Y"),
        "start_time": datetime.fromtimestamp(start_ts, IST).strftime("%I:%M %p"),
        "end_time":   datetime.fromtimestamp(end_ts,   IST).strftime("%I:%M %p"),
    }

    sent = 0
    for rec in attendance_records:
        student_email = rec.get("email", "")
        if not student_email:
            continue

        student_name = rec.get("name", "Unknown")
        student_id   = rec.get("student_id", "")
        status       = rec.get("status", "absent")
        windows_seen = int(rec.get("windows_seen", 0))

        # Build per-window details
        dur     = end_ts - start_ts
        windows = [
            {"label": "START", "from": start_ts,            "to": start_ts + 600},
            {"label": "MID",   "from": start_ts+dur//2-300, "to": start_ts+dur//2+300},
            {"label": "END",   "from": end_ts - 600,        "to": end_ts},
        ]

        window_details = []
        for i, w in enumerate(windows):
            win_data = window_detections_for_session.get(i, {})
            detected = student_id in win_data
            det_time = win_data.get(student_id, {}).get("detected_at", "") if detected else ""
            window_details.append({
                "label":    w["label"],
                "detected": detected,
                "time":     det_time
            })

        success = send_attendance_email(
            student_name   = student_name,
            student_email  = student_email,
            session_info   = session_info,
            status         = status,
            windows_seen   = windows_seen,
            window_details = window_details
        )
        if success:
            sent += 1

    print(f"✉ Emails sent: {sent}/{len(attendance_records)}")
    return sent
