import os
import json
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate, make_msgid
from contextlib import asynccontextmanager
from fastapi import FastAPI
import firebase_admin
from firebase_admin import credentials, firestore
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

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

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465

def send_email(to_email: str, subject: str, body: str) -> bool:
    sender_email = (os.getenv("SENDER_EMAIL") or "").strip()
    sender_password = (os.getenv("SENDER_PASSWORD") or "").replace(" ", "").strip()

    if not sender_email or not sender_password:
        print("[ERROR] SENDER_EMAIL or SENDER_PASSWORD is missing in your .env file!")
        return False

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = f"Task Reminder App <{sender_email}>"
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=20) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
            print(f"[SUCCESS] Email sent to {to_email}: {subject}")
            return True
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

        # Custom Reminder Logic
        if custom_mins and not task.get("customReminderSent", False):
            # Triggers if the current time is within a 10-minute window of the custom request
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