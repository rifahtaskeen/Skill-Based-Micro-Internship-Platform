from flask import Flask, render_template, request, redirect, session, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mysqldb import MySQL
from werkzeug.utils import secure_filename
import os, subprocess
from config import *

app = Flask(__name__)
app.config.from_object('config')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

app.config['MYSQL_HOST'] = MYSQL_HOST
app.config['MYSQL_USER'] = MYSQL_USER
app.config['MYSQL_PASSWORD'] = MYSQL_PASSWORD
app.config['MYSQL_DB'] = MYSQL_DB

mysql = MySQL(app)

# ---------------- HELPER FUNCTIONS ----------------
def get_submissions(challenge_id):
    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT s.user_id,
               CONCAT(u.first_name,' ',u.last_name),
               s.score
        FROM submissions s
        JOIN users u ON s.user_id = u.id
        WHERE s.challenge_id=%s
        ORDER BY s.score DESC
    """, [challenge_id])
    data = cur.fetchall()
    cur.close()
    return data

def get_winner(challenge_id):
    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT CONCAT(u.first_name,' ',u.last_name)
        FROM users u
        JOIN challenges c ON u.id = c.winner_id
        WHERE c.id=%s
    """, [challenge_id])
    winner = cur.fetchone()
    cur.close()
    return winner[0] if winner else None

# ---------------- AUTH ----------------
@app.route('/')
def home():
    return render_template('landing.html')

@app.route('/', methods=['POST'])
def block_post_home():
    return redirect('/login')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        user = cur.fetchone()
        cur.close()
        
        # Fix indexes: password=6, role=7
        if user and check_password_hash(user[6], password):
            session['user_id'] = user[0]
            session['role'] = user[7]

            if user[7] == 'admin':
                return redirect('/admin')
            elif user[7] == 'company':
                return redirect('/company')
            else:
                return redirect('/student')
        else:
            flash('Invalid credentials')
    return render_template('login.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        first_name = request.form['first_name']
        middle_name = request.form.get('middle_name', '')
        last_name = request.form['last_name']
        phone = request.form['phone']

        email = request.form['email']
        password = generate_password_hash(request.form['password'])
        role = request.form['role']

        company_description = None
        company_logo = None

        if role == 'company':
            company_description = request.form.get('company_description')

            logo = request.files.get('company_logo')
            if logo and logo.filename != '':
                filename = secure_filename(logo.filename)
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                logo_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                logo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                company_logo = filename

        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM users WHERE email=%s", [email])
        if cur.fetchone():
            flash("Email already registered")
            cur.close()
            return redirect('/register')

        cur.execute("""
            INSERT INTO users
            (first_name, middle_name, last_name, phone,
             email, password, role, company_description, company_logo)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            first_name, middle_name, last_name, phone,
            email, password, role, company_description, company_logo
        ))

        mysql.connection.commit()
        cur.close()

        flash("Registered successfully!")
        return redirect('/login')

    return render_template('register.html')

# ---------------- ADMIN ----------------
@app.route('/admin')
def admin_dashboard():
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect('/login')
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM complaints")
    complaints = cur.fetchall()
    cur.execute("SELECT * FROM users")
    users = cur.fetchall()
    cur.close()
    return render_template('admin_dashboard.html', complaints=complaints, users=users)

# ---------------- COMPANY ----------------
@app.route('/company')
def company_dashboard():
    if 'user_id' not in session or session['role'] != 'company':
        return redirect('/login')
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM challenges WHERE company_id=%s", [session['user_id']])
    challenges = cur.fetchall()
    cur.close()
    return render_template('company_dashboard.html',
                           challenges=challenges,
                           get_submissions=get_submissions,
                           get_winner=get_winner)

@app.route('/add_challenge', methods=['POST'])
def add_challenge():
    title = request.form['title']
    description = request.form['description']
    correct_answer = request.form['correct_answer']

    cur = mysql.connection.cursor()
    cur.execute("""
        INSERT INTO challenges (title, description, correct_answer, company_id)
        VALUES (%s, %s, %s, %s)
    """, (title, description, correct_answer, session['user_id']))

    mysql.connection.commit()
    cur.close()

    return redirect(url_for('company_dashboard'))

@app.route('/declare_winner/<int:challenge_id>', methods=['POST'])
def declare_winner(challenge_id):
    user_id = request.form['winner_id']
    if not user_id:
        flash("No winner selected")
        return redirect('/company')
    cur = mysql.connection.cursor()
    cur.execute("UPDATE challenges SET winner_id=%s WHERE id=%s", (user_id, challenge_id))
    mysql.connection.commit()
    cur.close()
    flash("Winner declared successfully!")
    return redirect('/company')

# ---------------- STUDENT ----------------
@app.route('/student')
def student_dashboard():
    if 'user_id' not in session or session['role'] != 'student':
        return redirect('/login')

    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT 
            c.id,
            c.company_id,
            c.title,
            c.description,
            u.first_name,
            u.last_name,
            u.company_description,
            u.company_logo
        FROM challenges c
        JOIN users u ON c.company_id = u.id
    """)
    challenges = cur.fetchall()
    cur.close()
    return render_template('student_dashboard.html', challenges=challenges)

@app.route('/submit/<int:challenge_id>', methods=['POST'])
def submit_challenge(challenge_id):

    if 'user_id' not in session or session.get('role') != 'student':
        return redirect(url_for('login'))

    # Get student answer (GENERAL TEXT)
    student_answer = request.form['submission_answer']

    cur = mysql.connection.cursor()

    # Fetch correct answer from company
    cur.execute(
        "SELECT correct_answer FROM challenges WHERE id = %s",
        (challenge_id,)
    )
    result = cur.fetchone()

    if not result:
        flash("Invalid challenge")
        return redirect(url_for('student_dashboard'))

    correct_answer = result[0]

    # Simple evaluation logic (exact match)
    if student_answer.strip().lower() == correct_answer.strip().lower():
        score = 100
    else:
        score = 0

    # Store submission
    cur.execute("""
        INSERT INTO submissions (user_id, challenge_id, submission_text, score)
        VALUES (%s, %s, %s, %s)
    """, (session['user_id'], challenge_id, student_answer, score))

    mysql.connection.commit()
    cur.close()

    flash(f"Answer submitted successfully. Score: {score}")
    return redirect(url_for('student_dashboard'))


# ---------------- COMPLAINT ----------------
@app.route('/complaint', methods=['GET','POST'])
def complaint():
    if request.method == 'POST':
        msg = request.form['message']
        cur = mysql.connection.cursor()
        cur.execute("INSERT INTO complaints(user_id,message) VALUES(%s,%s)", (session['user_id'], msg))
        mysql.connection.commit()
        cur.close()
        flash("Complaint submitted successfully")
        return redirect(url_for('student_dashboard'))
    return render_template('complaint.html')

# ---------------- LEADERBOARD ----------------
@app.route('/leaderboard/<int:challenge_id>')
def leaderboard(challenge_id):
    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT CONCAT(u.first_name,' ',u.last_name), s.score
        FROM submissions s
        JOIN users u ON s.user_id = u.id
        WHERE s.challenge_id=%s
        ORDER BY s.score DESC
    """, [challenge_id])
    data = cur.fetchall()
    cur.close()
    return render_template('leaderboard.html', data=data)


# ---------------- LOGOUT ----------------
@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out successfully")
    return redirect(url_for('home'))  # redirect to landing page (home)


@app.route('/admin/delete_user/<int:user_id>')
def delete_user(user_id):
    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    if user_id == session.get('user_id'):
        flash("You cannot delete yourself")
        return redirect(url_for('admin_dashboard'))

    cur = mysql.connection.cursor()
    
    # Delete challenges posted by the user
    cur.execute("DELETE FROM challenges WHERE company_id=%s", (user_id,))
    
    # Delete user
    cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
    
    mysql.connection.commit()
    cur.close()

    flash("User deleted successfully")
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__':
    app.secret_key = SECRET_KEY
    app.run(debug=True)

