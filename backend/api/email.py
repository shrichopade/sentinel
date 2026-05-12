# email.py — sends action letters via Resend and stores an email log row in Supabase.
# If Resend is not configured, the module still stores message details for audit/history.
import os, warnings, resend
from datetime import datetime, timezone
from dotenv import load_dotenv
from api.db import supabase

load_dotenv()
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")
if not RESEND_API_KEY:
    warnings.warn("RESEND_API_KEY is not set in .env, email sending will not work. Message will be stored in the database")
resend.api_key = RESEND_API_KEY


# Send one plain-text email and always store key email details in Supabase.
def send_letter(to_email: str, subject: str, body: str) -> dict:
    payload = {"from": RESEND_FROM_EMAIL, "to": [to_email], "subject": subject, "text": body}
    try:
        response = resend.Emails.send(payload)
        resend_id = response.get("id")
        supabase.table("emails").insert({"subject": subject, "from_email": RESEND_FROM_EMAIL, "to_email": to_email, "body": body, "resend_id": resend_id, "status": "sent", "created_at": datetime.now(timezone.utc).isoformat()}).execute()
        return {"success": True, "id": resend_id}
    except Exception as e:
        print(f"[email] {e}")
        try:
            supabase.table("emails").insert({"subject": subject, "from_email": RESEND_FROM_EMAIL, "to_email": to_email, "body": body, "status": "error", "error": str(e), "created_at": datetime.now(timezone.utc).isoformat()}).execute()
        except Exception as db_error:
            print(f"[email] failed to store email row: {db_error}")
        return {"success": False, "error": str(e)}
