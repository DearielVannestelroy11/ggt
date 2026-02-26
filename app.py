from flask import Flask, render_template, request, redirect, session, Response, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import cv2
import os
import re
from datetime import datetime
from dotenv import load_dotenv
from google import genai

# ================= 1. INISIALISASI =================
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY") 
client = genai.Client(api_key=api_key)

app = Flask(__name__)
app.secret_key = "glowscan_secret"

# ================= 2. DATABASE LOGIC (URUTAN TERBAIK) =================

def get_db():
    """Fungsi ini harus berada di PALING ATAS bagian database"""
    # SQLite akan otomatis membuat file users.db jika belum ada
    conn = sqlite3.connect("users.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Fungsi ini memanggil get_db() yang sudah didefinisikan di atas"""
    try:
        db = get_db()
        # Buat Tabel Users
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT,
                role TEXT DEFAULT 'user'
            )
        """)
        
        # Buat Tabel History
        db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                hasil_scan TEXT,
                tanggal DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(username) REFERENCES users(username)
            )
        """)

        # Pastikan Admin selalu ada (Login: admin | Pass: admin123)
        db.execute("DELETE FROM users WHERE username='admin'")
        db.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
            ("admin", generate_password_hash("admin123"), "admin")
        )
        
        db.commit()
        db.close()
        print("✅ DATABASE BERHASIL DIBUAT (File 'users.db' muncul)!")
    except Exception as e:
        print(f"❌ ERROR DATABASE: {e}")

# PANGGIL init_db tepat setelah fungsinya selesai ditulis
init_db()

# ================= 3. LOGIKA KAMERA =================
face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
hasil_ai_terbaru = "Menunggu analisis wajah..."
last_frame = None

def generate_frames():
    global hasil_ai_terbaru, last_frame
    cam = cv2.VideoCapture(0)
    while True:
        success, frame = cam.read()
        if not success: break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)

        if len(faces) == 0:
            hasil_ai_terbaru = "❌ Wajah tidak terdeteksi"
        else:
            x, y, w, h = faces[0]
            face_roi = gray[y:y+h, x:x+w]
            brightness = face_roi.mean()

            if brightness > 160: kondisi = "Kulit Glowing"
            elif brightness > 120: kondisi = "Kulit Normal"
            elif brightness > 80: kondisi = "Kulit Kusam"
            else: kondisi = "Kulit Berminyak"

            hasil_ai_terbaru = f"1 Wajah terdeteksi. Kondisi: {kondisi}"
            cv2.rectangle(frame, (x,y), (x+w,y+h), (0,255,0), 2)

        last_frame = frame.copy()
        _, buffer = cv2.imencode(".jpg", frame)
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n")
    cam.release()

# ================= 4. SEMUA ROUTE FLASK =================

@app.route("/", methods=["GET","POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("username")
        p = request.form.get("password")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
        db.close()
        
        if user and check_password_hash(user["password"], p):
            session["user"] = user["username"]
            session["role"] = user["role"]
            return redirect("/admin_dashboard" if user["role"] == "admin" else "/dashboard")
        error = "Username atau Password salah!"
    return render_template("login.html", error=error)

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        try:
            db = get_db()
            db.execute("INSERT INTO users (username, password, role) VALUES (?, ?, 'user')", 
                       (request.form["username"], generate_password_hash(request.form["password"])))
            db.commit()
            db.close()
            return redirect("/")
        except:
            return render_template("register.html", error="Username sudah digunakan")
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/dashboard")
def dashboard():
    if "user" not in session: return redirect("/")
    return render_template("dashboard.html", user=session["user"])

@app.route("/camera")
def camera_page():
    if "user" not in session: return redirect("/")
    return render_template("camera.html")

@app.route("/video")
def video():
    return Response(generate_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/hasil_ai")
def hasil_ai():
    return hasil_ai_terbaru

@app.route("/capture", methods=["POST"])
def capture():
    global hasil_ai_terbaru
    if last_frame is None:
        return jsonify({"status":"error", "message": "Frame tidak ditemukan"})

    session['analisis_terakhir'] = hasil_ai_terbaru

    if "user" in session:
        db = get_db()
        db.execute("INSERT INTO history (username, hasil_scan) VALUES (?, ?)", 
                   (session["user"], hasil_ai_terbaru))
        db.commit()
        db.close()

    return jsonify({"status":"success", "hasil": hasil_ai_terbaru, "redirect": "/konsultasi"})

@app.route("/admin_dashboard")
def admin_dashboard():
    if "user" not in session or session.get("role") != "admin":
        return redirect("/")
    db = get_db()
    users_data = db.execute("SELECT * FROM users").fetchall()
    history_data = db.execute("SELECT * FROM history ORDER BY tanggal DESC").fetchall()
    db.close()
    return render_template("admin.html", users=users_data, history=history_data)

# ================= 5. GEMINI AI LOGIC =================

def gemini_konsultasi(pertanyaan, hasil_scan):
    try:
        prompt = f"Ahli GlowScan AI. Hasil scan: {hasil_scan}. Pertanyaan: {pertanyaan}. Jawab singkat & jelas."
        response = client.models.generate_content(model="gemini-flash-latest", contents=prompt)
        # Bersihkan format markdown
        return re.sub(r'[\*\#]', '', response.text).strip() if response.text else "AI tidak merespon."
    except Exception as e:
        return f"⚠️ Error AI: {str(e)}"

@app.route("/konsultasi", methods=["GET","POST"])
def konsultasi():
    if "user" not in session: return redirect("/")
    hasil_scan = session.get('analisis_terakhir', "Belum ada hasil scan.")
    jawaban = None
    if request.method == "GET" and 'analisis_terakhir' in session:
        jawaban = gemini_konsultasi("Sapa pengguna dan berikan saran perawatan singkat.", hasil_scan)
    if request.method == "POST":
        jawaban = gemini_konsultasi(request.form.get("pertanyaan",""), hasil_scan)
    return render_template("konsultasi.html", user=session["user"], jawaban=jawaban, hasil_scan=hasil_scan)

# ================= ADMIN ACTIONS =================
@app.route("/admin/hapus_user/<int:user_id>")
def hapus_user(user_id):
    # Keamanan: Cek apakah yang akses benar-benar admin
    if "user" not in session or session.get("role") != "admin":
        return redirect("/")
    
    db = get_db()
    # Jangan izinkan menghapus diri sendiri atau sesama admin lewat rute ini (opsional)
    db.execute("DELETE FROM users WHERE id=? AND role != 'admin'", (user_id,))
    db.commit()
    db.close()
    
    return redirect("/admin_dashboard")

if __name__ == "__main__":
    app.run(debug=True)