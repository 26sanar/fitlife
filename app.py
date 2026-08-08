from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import requests
import random
from datetime import date, timedelta

app = Flask(__name__)
DATABASE = "fitlife.db"


workouts = {
    "Gentle Start":     {"level": "Beginner",     "minutes": 10, "focus": "Full body"},
    "Full-Body Reset":  {"level": "Beginner",     "minutes": 15, "focus": "Core, Legs, Back"},
    "Core Builder":     {"level": "Intermediate", "minutes": 15, "focus": "Core"},
    "Leg Burner":       {"level": "Intermediate", "minutes": 20, "focus": "Legs"},
    "Power Circuit":    {"level": "Advanced",     "minutes": 20, "focus": "Full body"}
}

API_URL = "https://oss.exercisedb.dev/api/v1/exercises"

focus_map = {
    "Full body": ["upper legs", "chest", "back", "cardio"],
    "Core":      ["waist"],
    "Legs":      ["upper legs", "lower legs"],
    "Back":      ["back"],
    "Chest":     ["chest"]
}

needs_apparatus = [
    "pull-up", "pull up", "chin-up", "chin up", "dip", "muscle up",
    "bar ", "bench", "parallel", "rings", "hang", "suspended", "assisted"
]

exercise_library = []


def is_equipment_free(exercise):
    """Return True only if an exercise needs no equipment at all."""
    
    if exercise["equipments"] != ["body weight"]:
        return False

    
    name = exercise["name"].lower()
    for word in needs_apparatus:
        if word in name:
            return False

    return True


def load_exercises():
    """Fetch bodyweight exercises from the ExerciseDB API when the app starts."""
    cursor_value = None

    for page in range(8):
        settings = {"limit": 25}
        if cursor_value is not None:
            settings["cursor"] = cursor_value

        try:
            response = requests.get(API_URL, params=settings, timeout=10)
            data = response.json()
        except Exception as error:
            print("API unavailable, using offline workouts. Reason:", error)
            return

        for exercise in data["data"]:
            if is_equipment_free(exercise):
                exercise_library.append({
                    "name": exercise["name"],
                    "body_parts": exercise["bodyParts"],
                    "instructions": exercise["instructions"],
                    "gif_url": exercise["gifUrl"]
                })

        cursor_value = data["meta"]["nextCursor"]
        if cursor_value is None:
            break

    print("Loaded", len(exercise_library), "equipment-free exercises from the API")


def get_exercises_for(focus, how_many):
    """Return a list of exercises matching a workout's focus area."""
    wanted_parts = []
    for area in focus.split(", "):
        for part in focus_map.get(area, []):
            wanted_parts.append(part)

    matches = []
    for exercise in exercise_library:
        for part in exercise["body_parts"]:
            if part in wanted_parts and exercise not in matches:
                matches.append(exercise)

    if len(matches) > how_many:
        matches = random.sample(matches, how_many)
    return matches


def create_database():
    """Create both database tables if they do not already exist."""
    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT NOT NULL,
            workout_name TEXT NOT NULL,
            minutes INTEGER NOT NULL,
            date_completed TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT NOT NULL UNIQUE,
            age INTEGER NOT NULL,
            level TEXT NOT NULL,
            goal TEXT NOT NULL,
            available_minutes INTEGER NOT NULL,
            date_updated TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS workout_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT NOT NULL,
            plan_date TEXT NOT NULL,
            workout_name TEXT NOT NULL,
            minutes INTEGER NOT NULL,
            completed INTEGER NOT NULL,
            UNIQUE(user_name, plan_date)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT NOT NULL UNIQUE,
            reminder_time TEXT NOT NULL,
            enabled INTEGER NOT NULL
        )
    """)

    connection.commit()
    connection.close()


def choose_workout(level, available_minutes):
    """Return the longest workout that matches the user's level and fits their time."""
    best_match = None
    for name, details in workouts.items():
        if details["level"] == level and details["minutes"] <= available_minutes:
            if best_match is None or details["minutes"] > workouts[best_match]["minutes"]:
                best_match = name
    if best_match is None:
        best_match = "Gentle Start"
    return best_match


def generate_weekly_plan(user_name, level, available_minutes):
    """Plan one workout for each of the next 7 days, rotating for variety."""
    suitable = []
    for name, details in workouts.items():
        if details["level"] == level and details["minutes"] <= available_minutes:
            suitable.append(name)

    if suitable == []:
        suitable = ["Gentle Start"]

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    for day_number in range(7):
        plan_date = date.today() + timedelta(days=day_number)
        workout_name = suitable[day_number % len(suitable)]
        minutes = workouts[workout_name]["minutes"]

        cursor.execute("""
            INSERT INTO workout_plans (user_name, plan_date, workout_name, minutes, completed)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(user_name, plan_date) DO NOTHING
        """, (user_name, str(plan_date), workout_name, minutes))

    connection.commit()
    connection.close()


def load_weekly_plan(user_name):
    """Return the user's next 7 planned days as a list of dictionaries."""
    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()
    cursor.execute("""
        SELECT plan_date, workout_name, minutes, completed
        FROM workout_plans
        WHERE user_name = ? AND plan_date >= ?
        ORDER BY plan_date
        LIMIT 7
    """, (user_name, str(date.today())))
    rows = cursor.fetchall()
    connection.close()

    plan = []
    for row in rows:
        day = date.fromisoformat(row[0])
        plan.append({
            "plan_date": row[0],
            "day_name": day.strftime("%A"),
            "workout_name": row[1],
            "minutes": row[2],
            "completed": row[3],
            "is_today": day == date.today()
        })
    return plan


def calculate_streak(user_name):
    """Count how many days in a row this user completed a workout, ending today."""
    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()
    cursor.execute("""
        SELECT DISTINCT date_completed FROM sessions
        WHERE user_name = ?
    """, (user_name,))
    dates = []
    for row in cursor.fetchall():
        dates.append(row[0])
    connection.close()

    streak = 0
    day = date.today()
    while str(day) in dates:
        streak = streak + 1
        day = day - timedelta(days=1)
    return streak


def get_weekly_minutes(user_name):
    """Return the minutes this user exercised in each of the last 8 weeks, oldest first."""
    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()
    cursor.execute("""
        SELECT date_completed, minutes FROM sessions
        WHERE user_name = ?
    """, (user_name,))
    rows = cursor.fetchall()
    connection.close()

    weeks = []
    for number in range(7, -1, -1):
        weeks.append({
            "start": date.today() - timedelta(days=number * 7 + 6),
            "end": date.today() - timedelta(days=number * 7),
            "minutes": 0
        })

    for row in rows:
        day = date.fromisoformat(row[0])
        for week in weeks:
            if week["start"] <= day <= week["end"]:
                week["minutes"] = week["minutes"] + row[1]

    busiest = 0
    for week in weeks:
        if week["minutes"] > busiest:
            busiest = week["minutes"]

    for week in weeks:
        if busiest > 0:
            week["height"] = int(week["minutes"] / busiest * 100)
        else:
            week["height"] = 0

    return weeks


def save_profile(user_name, age, level, goal, available_minutes):
    """Create a new profile, or update it if this name already exists."""
    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()
    cursor.execute("""
        INSERT INTO user_profiles (user_name, age, level, goal, available_minutes, date_updated)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_name) DO UPDATE SET
            age = excluded.age,
            level = excluded.level,
            goal = excluded.goal,
            available_minutes = excluded.available_minutes,
            date_updated = excluded.date_updated
    """, (user_name, age, level, goal, available_minutes, str(date.today())))
    connection.commit()
    connection.close()


def load_profile(user_name):
    """Find a saved profile by name and return it as a dictionary, or None."""
    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()
    cursor.execute("""
        SELECT user_name, age, level, goal, available_minutes
        FROM user_profiles
        WHERE user_name = ?
    """, (user_name,))
    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None

    profile = {
        "user_name": row[0],
        "age": row[1],
        "level": row[2],
        "goal": row[3],
        "available_minutes": row[4]
    }
    return profile


def save_reminder(user_name, reminder_time, enabled):
    """Create a reminder for this user, or update it if one already exists."""
    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()
    cursor.execute("""
        INSERT INTO reminders (user_name, reminder_time, enabled)
        VALUES (?, ?, ?)
        ON CONFLICT(user_name) DO UPDATE SET
            reminder_time = excluded.reminder_time,
            enabled = excluded.enabled
    """, (user_name, reminder_time, enabled))
    connection.commit()
    connection.close()


def load_reminder(user_name):
    """Find a saved reminder by name and return it as a dictionary, or None."""
    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()
    cursor.execute("""
        SELECT reminder_time, enabled
        FROM reminders
        WHERE user_name = ?
    """, (user_name,))
    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None

    reminder = {
        "reminder_time": row[0],
        "enabled": row[1]
    }
    return reminder


@app.route("/")
def index():
    return render_template("index.html", profile=None, message=None)


@app.route("/profile", methods=["POST"])
def show_profile():
    user_name = request.form["user_name"]
    profile = load_profile(user_name)

    if profile is None:
        message = "No profile was found for " + user_name + "."
        return render_template("index.html", profile=None, message=message)

    return render_template("index.html", profile=profile, message=None)


@app.route("/today", methods=["POST"])
def today():
    user_name = request.form["user_name"]
    age = int(request.form["age"])
    level = request.form["level"]
    goal = request.form["goal"]
    available_minutes = int(request.form["available_minutes"])

    save_profile(user_name, age, level, goal, available_minutes)
    generate_weekly_plan(user_name, level, available_minutes)

    workout_name = choose_workout(level, available_minutes)
    workout = workouts[workout_name]
    exercises = get_exercises_for(workout["focus"], 4)

    return render_template(
        "today.html",
        user_name=user_name,
        age=age,
        goal=goal,
        workout_name=workout_name,
        workout=workout,
        exercises=exercises,
        streak=calculate_streak(user_name),
        today_date=date.today().strftime("%A %d %B")
    )


@app.route("/complete", methods=["POST"])
def complete():
    user_name = request.form["user_name"]
    workout_name = request.form["workout_name"]
    minutes = int(request.form["minutes"])

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()
    cursor.execute("""
        INSERT INTO sessions (user_name, workout_name, minutes, date_completed)
        VALUES (?, ?, ?, ?)
    """, (user_name, workout_name, minutes, str(date.today())))

    cursor.execute("""
        UPDATE workout_plans SET completed = 1
        WHERE user_name = ? AND plan_date = ?
    """, (user_name, str(date.today())))

    connection.commit()
    connection.close()

    return redirect(url_for("history", user=user_name))


@app.route("/plan")
def plan():
    user_name = request.args.get("user", "").strip()

    if user_name == "":
        return render_template("plan.html", user_name=None, plan=[])

    return render_template(
        "plan.html",
        user_name=user_name,
        plan=load_weekly_plan(user_name)
    )


@app.route("/history")
def history():
    user_name = request.args.get("user", "").strip()

    if user_name == "":
        return render_template(
            "history.html",
            user_name=None,
            sessions=[],
            streak=0,
            total_minutes=0,
            weeks=[]
        )

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()
    cursor.execute("""
        SELECT * FROM sessions
        WHERE user_name = ?
        ORDER BY id DESC
    """, (user_name,))
    sessions = cursor.fetchall()
    connection.close()

    total_minutes = 0
    for session in sessions:
        total_minutes = total_minutes + session[3]

    return render_template(
        "history.html",
        user_name=user_name,
        sessions=sessions,
        streak=calculate_streak(user_name),
        total_minutes=total_minutes,
        weeks=get_weekly_minutes(user_name)
    )


@app.route("/reminders")
def reminders():
    user_name = request.args.get("user", "").strip()

    if user_name == "":
        return render_template("reminders.html", user_name=None, reminder=None)

    return render_template(
        "reminders.html",
        user_name=user_name,
        reminder=load_reminder(user_name)
    )


@app.route("/reminders/save", methods=["POST"])
def reminders_save():
    user_name = request.form["user_name"]
    reminder_time = request.form["reminder_time"]

    if "enabled" in request.form:
        enabled = 1
    else:
        enabled = 0

    save_reminder(user_name, reminder_time, enabled)
    return redirect(url_for("reminders", user=user_name))


create_database()
load_exercises()

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)