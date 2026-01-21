from flask import (
    Flask, render_template, request, send_file,
    redirect, url_for, jsonify, session, flash
)
import os
from io import BytesIO
import sqlite3
import uuid
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from gtts import gTTS
from google import genai

# Optional PDF reader
try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

# ------------------------
# AI Config (Gemini)
# ------------------------
GEMINI_API_KEY = "YOUR_API_KEY_HERE"
client = genai.Client(api_key=GEMINI_API_KEY)

def generate_summary(text):
    try:
        prompt = (
            "You are an expert academic assistant. Summarize the following study notes. "
            "Use a clear, professional tone. Include a high-level overview and "
            "then list key points in bullet points.\n\n"
            f"NOTES:\n{text[:15000]}"
        )

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )

        if response and response.text:
            return response.text

        return "AI was unable to generate a summary."
    except Exception as e:
        return f"Error: {e}"

# ------------------------
# App Config
# ------------------------
app = Flask(__name__)
app.secret_key = "supersecretkey"

UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploads")
AUDIO_FOLDER = os.path.join("static", "audio")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(AUDIO_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# ------------------------
# Database
# ------------------------
DB_FILE = "history.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            filename TEXT,
            summary TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def add_history(action, filename, summary=""):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO history (action, filename, summary) VALUES (?, ?, ?)",
        (action, filename, summary)
    )
    conn.commit()
    conn.close()

init_db()

# ------------------------
# Auth Helper
# ------------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            flash("Please login first", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ------------------------
# Routes
# ------------------------
@app.route("/")
def home():
    if "user" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not (email and password and confirm_password):
            flash("Please fill all fields", "error")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Passwords do not match!", "error")
            return redirect(url_for("register"))

        hashed_password = generate_password_hash(password)
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("INSERT INTO users (username, password) VALUES (?, ?)", (email, hashed_password))
            conn.commit()
            conn.close()
            flash("Registration successful!", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Email already exists!", "error")

    return render_template("register.html", title="Register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT password FROM users WHERE username=?", (email,))
        row = c.fetchone()
        conn.close()

        if row and check_password_hash(row[0], password):
            session["user"] = email
            return redirect(url_for("dashboard"))
        flash("Invalid email or password", "error")

    return render_template("login.html", title="Login")

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM history")
    count = c.fetchone()[0]
    conn.close()

    stats = {"notes": count, "summaries": count, "plans": 0}
    return render_template("dashboard.html", stats=stats, title="Dashboard")

@app.route("/planner")
@login_required
def planner():
    return render_template("planner.html", title="Study Planner")

@app.route("/summary", methods=["GET", "POST"])
@login_required
def summary_page():
    original_text = ""
    summary_output = ""
    message = ""

    if request.method == "POST":
        original_text = request.form.get("text", "").strip()
        if original_text:
            summary_output = generate_summary(original_text)
            add_history("Text Summarize", "Text Input", summary_output)
        else:
            message = "No text provided."

    return render_template("summary.html", original_text=original_text, summary=summary_output, message=message)

@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload_page():
    message = ""
    original_text = ""
    summary_output = ""

    if request.method == "POST":
        file = request.files.get("notes_file")
        if file and file.filename:
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
            file.save(filepath)

            if file.filename.lower().endswith(".txt"):
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    original_text = f.read()
            elif file.filename.lower().endswith(".pdf") and PyPDF2:
                with open(filepath, "rb") as f:
                    reader = PyPDF2.PdfReader(f)
                    for page in reader.pages:
                        original_text += (page.extract_text() or "") + " "

            if original_text.strip():
                summary_output = generate_summary(original_text)
                add_history("Upload & Summarize", file.filename, summary_output)
                message = "File summarized successfully!"
            else:
                message = "Could not read text from file."

    return render_template("upload.html", message=message, original_text=original_text, summary=summary_output)

@app.route("/text-to-speech", methods=["POST"])
@login_required
def text_to_speech():
    text = request.form.get("text", "").strip()
    if not text:
        return jsonify({"error": "No text provided"})

    filename = f"{uuid.uuid4().hex}.mp3"
    filepath = os.path.join(AUDIO_FOLDER, filename)

    tts = gTTS(text=text, lang="en")
    tts.save(filepath)

    return jsonify({"audio_url": url_for("static", filename=f"audio/{filename}")})

if __name__ == "__main__":
    app.run(debug=True)
