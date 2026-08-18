from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import requests
import random
from datetime import date, timedelta

app = Flask(__name__)

# DATABASE — the SQLite database is a single file stored alongside app.py.
# SQLite was chosen over a server-based database because it requires no
# installation or configuration, which suits a solution that must run on any
# machine with Python installed.
DATABASE = "fitlife.db"


# WORKOUT CATALOGUE — dictionary of dictionaries.
# A dictionary is used rather than a list because workouts are retrieved by
# name, e.g. workouts[workout_name], which is a direct key lookup. A list
# would require searching every item to find a match.
# Each value is itself a dictionary so that a workout's attributes are
# accessed by meaningful name (details["minutes"]) rather than by position
# (details[1]), which stays readable if attributes are added later.
# minutes is an integer, not text, so it can be compared numerically against
# the user's available time in choose_workout().
workouts = {
    "Gentle Start":     {"level": "Beginner",     "minutes": 10, "focus": "Full body"},
    "Full-Body Reset":  {"level": "Beginner",     "minutes": 15, "focus": "Core, Legs, Back"},
    "Core Builder":     {"level": "Intermediate", "minutes": 15, "focus": "Core"},
    "Leg Burner":       {"level": "Intermediate", "minutes": 20, "focus": "Legs"},
    "Power Circuit":    {"level": "Advanced",     "minutes": 20, "focus": "Full body"}
}

# PRIMARY DATA SOURCE — the ExerciseDB REST API.
# This external source was chosen because it supplies verified exercise data
# with step-by-step instructions and demonstration images, which would take
# far longer to write by hand and would be less reliable.
API_URL = "https://oss.exercisedb.dev/api/v1/exercises"

# FOCUS MAPPING — dictionary used as a translation table.
# FitLife describes workouts using its own focus areas ("Core", "Legs"), while
# the ExerciseDB API uses different body part names ("waist", "upper legs").
# This dictionary maps one vocabulary to the other so that the rest of the
# program never needs to know the API's terminology.
# Each value is a list because one focus area can map to several body parts.
focus_map = {
    "Full body": ["upper legs", "chest", "back", "cardio"],
    "Core":      ["waist"],
    "Legs":      ["upper legs", "lower legs"],
    "Back":      ["back"],
    "Chest":     ["chest"]
}

# APPARATUS KEYWORDS — list of strings.
# A list is appropriate because this data is only ever searched through in
# order; no key lookup is needed. Order does not matter and duplicates are not
# a concern, so a list is the least complex structure that does the job.
# These exercises are tagged as bodyweight by the API despite requiring a bar,
# bench or similar, so they are excluded by name.
needs_apparatus = [
    "pull-up", "pull up", "chin-up", "chin up", "dip", "muscle up",
    "bar ", "bench", "parallel", "rings", "hang", "suspended", "assisted"
]

# SECONDARY DATA SOURCE — built-in exercises used when the API is unreachable.
# FitLife draws exercise data from two sources: the ExerciseDB API as the
# primary source, and this list as a local fallback. The API is preferred
# because it is verified and includes demonstrations, but it is an external
# dependency that may be unavailable, so a local copy guarantees the solution
# still functions offline.
# Each entry deliberately uses the same keys as the records built from the API
# (name, body_parts, instructions, gif_url) so that get_exercises_for() works
# identically regardless of which source filled the library.
# gif_url is an empty string rather than None so the template can insert it
# without needing a type check.
offline_exercises = [
    {
        "name": "squat",
        "body_parts": ["upper legs"],
        "gif_url": "",
        "instructions": [
            "Stand with your feet shoulder-width apart and toes pointing slightly out.",
            "Bend your knees and push your hips back as if sitting into a chair.",
            "Lower until your thighs are about parallel with the floor, keeping your chest up.",
            "Push through your heels to stand back up, and repeat."
        ]
    },
    {
        "name": "reverse lunge",
        "body_parts": ["upper legs"],
        "gif_url": "",
        "instructions": [
            "Stand tall with your feet hip-width apart.",
            "Step one foot back and bend both knees until your back knee hovers just above the floor.",
            "Keep your chest upright and your front knee over your ankle.",
            "Push back to standing and repeat on the other leg."
        ]
    },
    {
        "name": "glute bridge",
        "body_parts": ["upper legs"],
        "gif_url": "",
        "instructions": [
            "Lie on your back with your knees bent and feet flat on the floor.",
            "Squeeze your glutes and lift your hips until your body is straight from knees to shoulders.",
            "Hold for a second at the top.",
            "Lower slowly and repeat."
        ]
    },
    {
        "name": "calf raise",
        "body_parts": ["lower legs"],
        "gif_url": "",
        "instructions": [
            "Stand tall with your feet hip-width apart.",
            "Push through the balls of your feet to lift your heels as high as you can.",
            "Pause briefly at the top.",
            "Lower your heels slowly back to the floor and repeat."
        ]
    },
    {
        "name": "push-up",
        "body_parts": ["chest"],
        "gif_url": "",
        "instructions": [
            "Start in a high plank with your hands slightly wider than your shoulders.",
            "Keep your body in a straight line from head to heels.",
            "Bend your elbows to lower your chest towards the floor.",
            "Push back up to the starting position and repeat."
        ]
    },
    {
        "name": "wide push-up",
        "body_parts": ["chest"],
        "gif_url": "",
        "instructions": [
            "Start in a push-up position with your hands about double shoulder-width apart.",
            "Lower your chest towards the floor, keeping your elbows over your wrists.",
            "Press back up until your arms are straight.",
            "Keep your hips level the whole time."
        ]
    },
    {
        "name": "crunch",
        "body_parts": ["waist"],
        "gif_url": "",
        "instructions": [
            "Lie on your back with your knees bent and hands lightly behind your head.",
            "Tighten your stomach and curl your shoulders off the floor.",
            "Avoid pulling on your neck.",
            "Lower back down with control and repeat."
        ]
    },
    {
        "name": "plank hold",
        "body_parts": ["waist"],
        "gif_url": "",
        "instructions": [
            "Rest on your forearms with your elbows under your shoulders and legs straight behind you.",
            "Squeeze your stomach and glutes so your body forms a straight line.",
            "Keep your neck relaxed and breathe steadily.",
            "Hold this position for the full time."
        ]
    },
    {
        "name": "bicycle crunch",
        "body_parts": ["waist"],
        "gif_url": "",
        "instructions": [
            "Lie on your back with your hands behind your head and legs lifted.",
            "Bring one knee in while twisting your opposite elbow towards it.",
            "Switch sides in a smooth pedalling motion.",
            "Keep your lower back pressed into the floor."
        ]
    },
    {
        "name": "mountain climber",
        "body_parts": ["waist", "cardio"],
        "gif_url": "",
        "instructions": [
            "Start in a high plank with your hands under your shoulders.",
            "Drive one knee towards your chest, then quickly switch legs.",
            "Keep your hips low and your back flat.",
            "Continue switching at a steady running pace."
        ]
    },
    {
        "name": "superman hold",
        "body_parts": ["back"],
        "gif_url": "",
        "instructions": [
            "Lie face down with your arms stretched out in front of you.",
            "Lift your arms, chest and legs a few centimetres off the floor at the same time.",
            "Squeeze your back and glutes, and hold for a moment.",
            "Lower down with control and repeat."
        ]
    },
    {
        "name": "bird dog",
        "body_parts": ["back", "waist"],
        "gif_url": "",
        "instructions": [
            "Start on your hands and knees with a flat back.",
            "Reach one arm forward while extending the opposite leg back.",
            "Pause, keeping your hips level and stomach tight.",
            "Return and repeat with the other arm and leg."
        ]
    },
    {
        "name": "jumping jack",
        "body_parts": ["cardio"],
        "gif_url": "",
        "instructions": [
            "Stand upright with your feet together and arms by your sides.",
            "Jump your feet out wide while raising your arms overhead.",
            "Jump back to the starting position.",
            "Repeat at a steady rhythm."
        ]
    },
    {
        "name": "high knees",
        "body_parts": ["cardio", "upper legs"],
        "gif_url": "",
        "instructions": [
            "Stand tall with your feet hip-width apart.",
            "Run on the spot, driving your knees up towards hip height.",
            "Pump your arms and stay light on your feet.",
            "Keep a quick, steady pace."
        ]
    },
    {
        "name": "burpee",
        "body_parts": ["cardio", "chest", "upper legs"],
        "gif_url": "",
        "instructions": [
            "From standing, crouch down and place your hands on the floor.",
            "Jump your feet back into a high plank.",
            "Jump your feet back in towards your hands.",
            "Stand up and jump, then repeat."
        ]
    }
]

# EXERCISE LIBRARY — module-level list acting as an in-memory cache.
# Exercises are fetched once when the application starts rather than on every
# request. A page load then reads from memory instead of waiting on a network
# call, which supports the requirement that pages load in under three seconds.
# A list is used because exercises are only ever iterated through and sampled
# randomly, never looked up by key.
exercise_library = []


def is_equipment_free(exercise):
    """Return True only if an exercise needs no equipment at all."""

    # The equipment list must contain bodyweight and nothing else. Testing
    # an earlier version showed that checking with "in" allowed exercises
    # tagged with both bodyweight and a machine to pass the filter.
    if exercise["equipments"] != ["body weight"]:
        return False

    # Second layer: reject exercises whose names imply apparatus the API's
    # own tagging does not capture, such as pull-ups requiring a bar.
    name = exercise["name"].lower()
    for word in needs_apparatus:
        if word in name:
            return False

    return True


def use_offline_exercises():
    """Fill the library with the built-in list when the API is unreachable."""
    for exercise in offline_exercises:
        exercise_library.append(exercise)
    print("Using", len(exercise_library), "built-in offline exercises")


def load_exercises():
    """Fetch bodyweight exercises from the ExerciseDB API when the app starts."""
    cursor_value = None

    # The API returns a maximum of 25 records per request, so it is called
    # repeatedly. Each response includes a cursor marking where the previous
    # batch ended, which is sent back to retrieve the next batch.
    for page in range(8):
        settings = {"limit": 25}
        if cursor_value is not None:
            settings["cursor"] = cursor_value

        try:
            # The browser-style User-Agent stops the API's bot filter from
            # rejecting the request with a 403 Forbidden error.
            response = requests.get(API_URL, params=settings, timeout=10,
                                    headers={"User-Agent": "Mozilla/5.0"})
            if response.status_code != 200:
                raise Exception("API returned status " + str(response.status_code))
            data = response.json()
        except Exception as error:
            # If the primary data source fails, fall back to the local copy
            # so the solution continues to function rather than failing.
            print("API unavailable. Reason:", error)
            if exercise_library == []:
                use_offline_exercises()
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

    if exercise_library == []:
        use_offline_exercises()
        return

    print("Loaded", len(exercise_library), "equipment-free exercises from the API")


def get_exercises_for(focus, how_many):
    """Return a list of exercises matching a workout's focus area."""

    # Translate the workout's focus areas into the body part names the
    # exercise records use. A workout may list several areas separated by
    # commas, and each area may map to more than one body part.
    wanted_parts = []
    for area in focus.split(", "):
        for part in focus_map.get(area, []):
            wanted_parts.append(part)

    matches = []
    for exercise in exercise_library:
        for part in exercise["body_parts"]:
            if part in wanted_parts and exercise not in matches:
                matches.append(exercise)

    # Random selection means a user repeating the same workout receives a
    # different set of exercises, which supports the engagement criterion.
    if len(matches) > how_many:
        matches = random.sample(matches, how_many)
    return matches


def create_database():
    """Create all four database tables if they do not already exist."""
    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    # DATA TYPES — SQLite has no native date or boolean type.
    # Dates are stored as TEXT in ISO format (YYYY-MM-DD) because ISO format
    # sorts correctly as plain text, so ORDER BY and date comparisons work
    # without conversion. Booleans are stored as INTEGER 0 or 1.
    # minutes and age are INTEGER because both are used in arithmetic, while
    # names and workout titles are TEXT.
    # Every table is created with IF NOT EXISTS so that startup never
    # overwrites data that already exists.

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT NOT NULL,
            workout_name TEXT NOT NULL,
            minutes INTEGER NOT NULL,
            date_completed TEXT NOT NULL
        )
    """)

    # user_name is UNIQUE so that one person cannot hold two profiles. This
    # constraint is what allows save_profile() to use a single upsert
    # statement rather than checking for an existing record first.
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

    # The composite constraint UNIQUE(user_name, plan_date) enforces one
    # workout per user per day at the database level rather than relying on
    # the program to check. This is deliberately stricter than the sessions
    # table, which allows several entries per day, because a plan is a
    # schedule while history is a log of what actually happened.
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

    # reminder_time is TEXT in 24-hour HH:MM format. Zero-padded times of
    # this form compare correctly as strings, so "09:15" > "07:30" behaves
    # as expected without converting to a time object.
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

    # The longest fitting workout is chosen rather than the shortest so that a
    # user reporting more available time receives proportionally more
    # exercise, making progress visible as their routine develops.
    best_match = None
    for name, details in workouts.items():
        if details["level"] == level and details["minutes"] <= available_minutes:
            if best_match is None or details["minutes"] > workouts[best_match]["minutes"]:
                best_match = name

    # Fallback so that a workout is always returned. Without this, an Advanced
    # user with only ten minutes available would receive nothing.
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

        # The modulo operator wraps the index around the end of the list, so
        # two suitable workouts alternate across the week rather than one
        # workout repeating seven times.
        workout_name = suitable[day_number % len(suitable)]
        minutes = workouts[workout_name]["minutes"]

        # ON CONFLICT DO NOTHING means re-running the generator fills only
        # days that do not yet exist. A day already marked complete is never
        # overwritten.
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

    # Each row is converted from a tuple into a dictionary so the template
    # can refer to fields by name rather than by position, which stays
    # readable if a column is added to the table later.
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

    # SELECT DISTINCT means two sessions completed on the same date count as
    # a single day, so the streak cannot be inflated by repeat entries.
    # The WHERE clause restricts the result to one user; without it the
    # streak would include every user's sessions.
    cursor.execute("""
        SELECT DISTINCT date_completed FROM sessions
        WHERE user_name = ?
    """, (user_name,))
    dates = []
    for row in cursor.fetchall():
        dates.append(row[0])
    connection.close()

    # Start at today and step backwards one day at a time. The loop stops at
    # the first date not present, which is the first missed day.
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

    # A list of dictionaries is used rather than several parallel lists so
    # that each week's start date, end date, total and bar height stay
    # grouped in one record and cannot fall out of alignment.
    weeks = []
    for number in range(7, -1, -1):
        weeks.append({
            "start": date.today() - timedelta(days=number * 7 + 6),
            "end": date.today() - timedelta(days=number * 7),
            "minutes": 0
        })

    # The stored date is TEXT, so it is converted back to a date object here
    # in order to compare it against each week's start and end.
    for row in rows:
        day = date.fromisoformat(row[0])
        for week in weeks:
            if week["start"] <= day <= week["end"]:
                week["minutes"] = week["minutes"] + row[1]

    busiest = 0
    for week in weeks:
        if week["minutes"] > busiest:
            busiest = week["minutes"]

    # Totals are converted into a percentage of the busiest week so the chart
    # scales correctly whether the user exercised for twenty minutes or two
    # hundred. The guard prevents division by zero for a user with no
    # recorded sessions.
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

    # This is an upsert: insert the record, or update it if the UNIQUE
    # constraint on user_name is violated. Doing this in one statement avoids
    # a separate SELECT to check whether the profile already exists.
    # The ? placeholders pass values as data rather than as SQL, which
    # prevents SQL injection.
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

    # Returning None rather than an empty dictionary lets the calling route
    # distinguish clearly between "no profile exists" and "a profile exists
    # but is empty", and is what the not-found message is based on.
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
    # profile and message are passed as None so the template's conditional
    # blocks always have a value to test and never raise an error.
    return render_template("index.html", profile=None, message=None)


@app.route("/profile", methods=["POST"])
def show_profile():
    user_name = request.form["user_name"]
    profile = load_profile(user_name)

    # Existence check on retrieved data: if no record was found, the user is
    # told rather than being shown an empty form with no explanation.
    if profile is None:
        message = "No profile was found for " + user_name + "."
        return render_template("index.html", profile=None, message=message)

    return render_template("index.html", profile=profile, message=None)


@app.route("/today", methods=["POST"])
def today():
    # int() converts the submitted text into a whole number so the values can
    # be used in numeric comparisons. A non-numeric value raises an error
    # here rather than being stored in the database as text.
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


@app.route("/today", methods=["GET"])
def today_view():
    """Show today's workout for a returning user, using their saved profile,
    so the navigation bar can link here without redoing onboarding."""
    user_name = request.args.get("user", "").strip()

    # Existence check: without a name there is nobody to show a workout for,
    # so the user is returned to the start rather than shown an empty page.
    if user_name == "":
        return redirect(url_for("index"))

    profile = load_profile(user_name)
    if profile is None:
        return redirect(url_for("index"))

    # Top up the weekly plan so it always covers the next 7 days.
    # Existing days are left alone because of ON CONFLICT DO NOTHING.
    generate_weekly_plan(user_name, profile["level"], profile["available_minutes"])

    workout_name = choose_workout(profile["level"], profile["available_minutes"])
    workout = workouts[workout_name]
    exercises = get_exercises_for(workout["focus"], 4)

    return render_template(
        "today.html",
        user_name=user_name,
        age=profile["age"],
        goal=profile["goal"],
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

    # Two writes are made from one connection: the session is logged, and the
    # matching day in the plan is marked complete. Both are committed
    # together so the plan and the history cannot disagree.
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

    # Redirecting after a POST rather than rendering directly means that
    # refreshing the page reloads the history instead of resubmitting the
    # form and recording a duplicate session.
    return redirect(url_for("history", user=user_name))


@app.route("/plan")
def plan():
    user_name = request.args.get("user", "").strip()

    # Without a name, an empty state is rendered. No other user's plan is
    # shown by default.
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

    # The WHERE clause restricts results to one user. An earlier version
    # omitted it, which caused every user's sessions to appear together.
    cursor.execute("""
        SELECT * FROM sessions
        WHERE user_name = ?
        ORDER BY id DESC
    """, (user_name,))
    sessions = cursor.fetchall()
    connection.close()

    # Column 3 of each row is minutes, matching the order the columns were
    # declared in create_database().
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
        reminder=load_reminder(user_name),
        profile=load_profile(user_name)
    )


@app.route("/reminders/save", methods=["POST"])
def reminders_save():
    user_name = request.form["user_name"]
    reminder_time = request.form["reminder_time"]

    # An unticked checkbox is not sent by the browser at all, so the presence
    # of the field is what indicates the reminder is switched on. The value is
    # stored as 0 or 1 because SQLite has no boolean type.
    if "enabled" in request.form:
        enabled = 1
    else:
        enabled = 0

    save_reminder(user_name, reminder_time, enabled)
    return redirect(url_for("reminders", user=user_name))


# These run once when the file is loaded, before the server starts, so the
# tables exist and the exercise library is populated before any request is
# handled. They sit outside the __main__ block because Flask's reloader can
# skip that block, which previously meant the library was never filled.
create_database()
load_exercises()

if __name__ == "__main__":
    # use_reloader is disabled so the file is not loaded twice at startup,
    # which would call the API twice and risk hitting its rate limit.
    app.run(debug=True, use_reloader=False)