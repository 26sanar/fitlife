from flask import Flask, render_template, request, redirect
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

# Maps the focus areas used in FitLife to the body part names used by the API
focus_map = {
    "Full body": ["upper legs", "chest", "back", "cardio"],
    "Core":      ["waist"],
    "Legs":      ["upper legs", "lower legs"],
    "Back":      ["back"],
    "Chest":     ["chest"]
}

exercise_library = []


def load_exercises():
    """Fetch bodyweight exercises from the ExerciseDB API when the app starts."""
    cursor_value = None

    for page in range(6):
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
            if "body weight" in exercise["equipments"]:
                exercise_library.append({
                    "name": exercise["name"],
                    "body_parts": exercise["bodyParts"],
                    "instructions": exercise["instructions"],
                    "gif_url": exercise["gifUrl"]
                })

        cursor_value = data["meta"]["nextCursor"]
        if cursor_value is None:
            break

    print("Loaded", len(exercise_library), "bodyweight exercises from the API")


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


def calculate_streak():
    """Count how many days in a row a workout was completed, ending today."""
    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()
    cursor.execute("SELECT DISTINCT date_completed FROM sessions")
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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/today", methods=["POST"])
def today():
    user_name = request.form["user_name"]
    age = int(request.form["age"])
    level = request.form["level"]
    available_minutes = int(request.form["available_minutes"])

    workout_name = choose_workout(level, available_minutes)
    workout = workouts[workout_name]
    exercises = get_exercises_for(workout["focus"], 4)

    return render_template(
        "today.html",
        user_name=user_name,
        age=age,
        workout_name=workout_name,
        workout=workout,
        exercises=exercises,
        streak=calculate_streak()
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
    connection.commit()
    connection.close()

    return redirect("/history")


@app.route("/history")
def history():
    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM sessions ORDER BY id DESC")
    sessions = cursor.fetchall()
    connection.close()

    total_minutes = 0
    for session in sessions:
        total_minutes = total_minutes + session[3]

    return render_template(
        "history.html",
        sessions=sessions,
        streak=calculate_streak(),
        total_minutes=total_minutes
    )

create_database()
load_exercises()

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)