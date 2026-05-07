"""
Run this ONCE to set up AWS SES for sending emails.
Usage: python setup_ses.py

Steps this script does:
1. Verifies your sender email address in SES
2. Tests the email configuration
"""
import boto3

SENDER_EMAIL = "varun.124477@stu.upes.ac.in"   # ← change this
AWS_REGION   = "ap-south-1"

ses = boto3.client("ses", region_name=AWS_REGION)

print("="*50)
print("AttendAI — AWS SES Setup")
print("="*50)

# Step 1: Verify sender email
print(f"\n[1] Verifying sender email: {SENDER_EMAIL}")
try:
    ses.verify_email_identity(EmailAddress=SENDER_EMAIL)
    print(f"✓ Verification email sent to {SENDER_EMAIL}")
    print("  → Check your inbox and click the verification link")
except Exception as e:
    print(f"✗ Error: {e}")

# Step 2: Check verification status
print("\n[2] Currently verified emails:")
try:
    resp = ses.list_verified_email_addresses()
    emails = resp.get("VerifiedEmailAddresses", [])
    if emails:
        for e in emails:
            print(f"  ✓ {e}")
    else:
        print("  (none yet — check your inbox for verification email)")
except Exception as e:
    print(f"✗ Error: {e}")

# Step 3: Check SES sending limits
print("\n[3] SES Account Info:")
try:
    quota = ses.get_send_quota()
    print(f"  Max 24hr send: {quota['Max24HourSend']}")
    print(f"  Sent last 24h: {quota['SentLast24Hours']}")
    print(f"  Max per second: {quota['MaxSendRate']}")
except Exception as e:
    print(f"✗ Error: {e}")

print("\n" + "="*50)
print("NEXT STEPS:")
print("="*50)
print("1. Check your email inbox and verify the sender address")
print("2. Update SENDER_EMAIL in email_service.py")
print("3. If in SES Sandbox, also verify each student's email")
print("   OR request SES production access to send to any email:")
print("   AWS Console → SES → Account Dashboard → Request Production Access")
print("4. Restart frame_server.py")
print("="*50)
