import os
import json
import requests
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI
import firebase_admin
from firebase_admin import credentials, firestore
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

# Initialize Firebase Admin
if not firebase_admin._apps:
    firebase_json_str = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if firebase_json_str:
        cred_dict = json.loads(firebase_json_str)
        cred = credentials.Certificate(cred_dict)
    else:
        cred_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "serviceAccountKey.json")
        cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)

db = firestore.client()

def send_email(to_email: str, subject: str, body: str) -> bool:
    webhook_url = os.getenv("GOOGLE_WEBHOOK_URL")
    if not webhook_url:
        print("[ERROR] GOOGLE_WEBHOOK_URL is missing!")
        return False

    payload = {
        "to": to_email,
        "subject": subject,
        "body": body
    }
    
    try:
        # Sends data securely over HTTPS (Port 443) to bypass Render's SMTP block
        response = requests.post(webhook_url, json=payload, timeout=20)
        if response.status_code == 200:
            print(f"[SUCCESS] Email sent to {to_email}: {subject}")
            return True
        else:
            print(f"[ERROR] Webhook failed with status {response.status_code}")
            return False
    except Exception as e:
        print(f"[ERROR] Failed to send email to {to_email}: {e}")
        return False

def check_and_send_reminders():
    now = datetime.now(timezone.utc)
    print(f"[{now.strftime('%H:%M:%S UTC')}] Checking Firestore for upcoming reminders...")

    tasks_ref = db.collection("tasks").where("isCompleted", "==", False).stream()

    for doc in tasks_ref:
        task = doc.to_dict()
        due_date_str = task.get("dueDate")
        user_email = task.get("userEmail")
        title = task.get("title", "Untitled Task")
        custom_mins = task.get("customReminderMinutes")

        if not due_date_str or not user_email:
            continue

        try:
            due_date = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
        except ValueError:
            continue

        minutes_left = (due_date - now).total_seconds() / 60

        # 1-Hour Reminder
        if 45 <= minutes_left <= 61 and not task.get("reminder60Sent", False):
            subject = f"Reminder (1 Hour Left): {title}"
            body = f"Hi!\n\nThis is your 1-hour reminder for your scheduled task:\n'{title}'\n\nStay on track!"
            if send_email(user_email, subject, body):
                db.collection("tasks").document(doc.id).update({"reminder60Sent": True})

        # 30-Minute Reminder
        elif 15 <= minutes_left <= 31 and not task.get("reminder30Sent", False):
            subject = f"Urgent Reminder (30 Mins Left): {title}"
            body = f"Hi!\n\nYour scheduled task '{title}' is coming up in 30 minutes!"
            if send_email(user_email, subject, body):
                db.collection("tasks").document(doc.id).update({"reminder30Sent": True})

        # Custom Reminder
        if custom_mins and not task.get("customReminderSent", False):
            if (custom_mins - 10) <= minutes_left <= (custom_mins + 2):
                subject = f"Custom Reminder ({custom_mins} Mins Left): {title}"
                body = f"Hi!\n\nThis is your custom reminder. Your task '{title}' is due in {custom_mins} minutes!"
                if send_email(user_email, subject, body):
                    db.collection("tasks").document(doc.id).update({"customReminderSent": True})

scheduler = BackgroundScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(check_and_send_reminders, "interval", minutes=1)
    scheduler.start()
    print("Background Email Scheduler started!")
    yield
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/")
def health_check():
    return {"status": "online", "service": "Firebase Reminder Email Scheduler"}