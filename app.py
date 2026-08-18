from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import requests
import random
from datetime import date, timedelta

app = Flask(__name__)

# The SQLite database is a single file stored alongside app.py. SQLite was
# chosen over a server-based database because it requires no installation or
# configuration, which suits a solution that must run on any machine with
# Python installed.
DATABASE_FILE = "fitlife.db"

# PRIMARY DATA SOURCE - the ExerciseDB REST API. This external source was
# chosen because it supplies verified exercise data with step-by-step
# instructions and demonstration images, which would take far longer to write
# by hand and would be less reliable.
API_URL = "https://oss.exercisedb.dev/api/v1/exercises"


# =====================================================================
#  DATABASE ACCESS
# =====================================================================

class Database:
    """Handles every connection to the SQLite file.

    Every other class in this program goes through this one to reach the
    database. Because the connection logic lives in a single place, no other
    class needs to know that sqlite3 is being used at all. If the storage
    method were ever changed, only this class would need rewriting.
    """

    def __init__(self, filename):
        # The filename is stored as a private attribute. The leading
        # underscore signals that code outside this class should not read or
        # change it directly.
        self._filename = filename

    def _connect(self):
        """Open a connection. Private, because only this class should use it."""
        return sqlite3.connect(self._filename)

    def execute(self, sql, values=()):
        """Run a statement that changes data, then save the change."""
        connection = self._connect()
        cursor = connection.cursor()
        cursor.execute(sql, values)
        connection.commit()
        connection.close()

    def execute_many_statements(self, statements):
        """Run several statements on one connection, saving them together.

        Used when two changes must both succeed or both fail, so that the
        data cannot be left in a half-finished state.
        """
        connection = self._connect()
        cursor = connection.cursor()
        for sql, values in statements:
            cursor.execute(sql, values)
        connection.commit()
        connection.close()

    def query_all(self, sql, values=()):
        """Run a query and return every matching row as a list of tuples."""
        connection = self._connect()
        cursor = connection.cursor()
        cursor.execute(sql, values)
        rows = cursor.fetchall()
        connection.close()
        return rows

    def query_one(self, sql, values=()):
        """Run a query and return the first matching row, or None."""
        connection = self._connect()
        cursor = connection.cursor()
        cursor.execute(sql, values)
        row = cursor.fetchone()
        connection.close()
        return row

    def create_tables(self):
        """Create all four tables if they do not already exist.

        DATA TYPES - SQLite has no native date or boolean type. Dates are
        stored as TEXT in ISO format (YYYY-MM-DD) because ISO format sorts
        correctly as plain text, so ORDER BY and date comparisons work
        without conversion. Booleans are stored as INTEGER 0 or 1.
        minutes and age are INTEGER because both are used in arithmetic,
        while names and workout titles are TEXT.
        """
        connection = self._connect()
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

        # user_name is UNIQUE so that one person cannot hold two profiles.
        # This constraint is what allows the profile to be saved with a single
        # upsert statement rather than checking for an existing record first.
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
        # workout per user per day at the database level rather than relying
        # on the program to check. This is deliberately stricter than the
        # sessions table, which allows several entries per day, because a plan
        # is a schedule while history is a log of what actually happened.
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


class Repository:
    """Base class for the four classes that read and write one table each.

    INHERITANCE - every repository needs the same two things: a reference to
    the Database, and the name of the table it works with. Writing that setup
    here once means the four subclasses do not repeat it.

    POLYMORPHISM - count_all() below is written once in this class, but uses
    self._table, which each subclass supplies. The same inherited method
    therefore queries a different table depending on the object it is called
    on.
    """

    def __init__(self, database, table):
        self._db = database
        self._table = table

    def count_all(self):
        """Return how many rows this repository's table contains."""
        row = self._db.query_one("SELECT COUNT(*) FROM " + self._table)
        return row[0]


class ProfileRepository(Repository):
    """Reads and writes records in the user_profiles table."""

    def __init__(self, database):
        # Calling the parent's __init__ stores the database and table name
        # using the shared code in Repository rather than repeating it here.
        super().__init__(database, "user_profiles")

    def save(self, user_name, age, level, goal, available_minutes):
        """Create a profile, or update it if this name already exists.

        This is an upsert: insert the record, or update it if the UNIQUE
        constraint on user_name is violated. Doing this in one statement
        avoids a separate query to check whether the profile already exists.
        The ? placeholders pass values as data rather than as SQL, which
        prevents SQL injection.
        """
        self._db.execute("""
            INSERT INTO user_profiles
                (user_name, age, level, goal, available_minutes, date_updated)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_name) DO UPDATE SET
                age = excluded.age,
                level = excluded.level,
                goal = excluded.goal,
                available_minutes = excluded.available_minutes,
                date_updated = excluded.date_updated
        """, (user_name, age, level, goal, available_minutes, str(date.today())))

    def find(self, user_name):
        """Return one profile as a dictionary, or None if no record exists."""
        row = self._db.query_one("""
            SELECT user_name, age, level, goal, available_minutes
            FROM user_profiles
            WHERE user_name = ?
        """, (user_name,))

        # Returning None rather than an empty dictionary lets the calling code
        # tell the difference between "no profile exists" and "a profile
        # exists but is empty". The not-found message is based on this.
        if row is None:
            return None

        return {
            "user_name": row[0],
            "age": row[1],
            "level": row[2],
            "goal": row[3],
            "available_minutes": row[4]
        }


class SessionRepository(Repository):
    """Reads and writes records in the sessions table."""

    def __init__(self, database):
        super().__init__(database, "sessions")

    def add(self, user_name, workout_name, minutes):
        """Record one completed workout for today."""
        self._db.execute("""
            INSERT INTO sessions (user_name, workout_name, minutes, date_completed)
            VALUES (?, ?, ?, ?)
        """, (user_name, workout_name, minutes, str(date.today())))

    def all_for(self, user_name):
        """Return every session for one user, newest first."""
        return self._db.query_all("""
            SELECT * FROM sessions
            WHERE user_name = ?
            ORDER BY id DESC
        """, (user_name,))

    def distinct_dates_for(self, user_name):
        """Return each date this user completed at least one workout.

        SELECT DISTINCT means two sessions completed on the same date count
        as a single day, so a streak cannot be inflated by repeat entries.
        """
        rows = self._db.query_all("""
            SELECT DISTINCT date_completed FROM sessions
            WHERE user_name = ?
        """, (user_name,))

        dates = []
        for row in rows:
            dates.append(row[0])
        return dates

    def dates_and_minutes_for(self, user_name):
        """Return the date and length of every session for one user."""
        return self._db.query_all("""
            SELECT date_completed, minutes FROM sessions
            WHERE user_name = ?
        """, (user_name,))


class PlanRepository(Repository):
    """Reads and writes records in the workout_plans table."""

    def __init__(self, database):
        super().__init__(database, "workout_plans")

    def add_day(self, user_name, plan_date, workout_name, minutes):
        """Schedule one day, leaving any existing entry untouched.

        ON CONFLICT DO NOTHING means re-running the plan generator fills only
        days that do not yet exist. A day already marked complete is never
        overwritten.
        """
        self._db.execute("""
            INSERT INTO workout_plans
                (user_name, plan_date, workout_name, minutes, completed)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(user_name, plan_date) DO NOTHING
        """, (user_name, str(plan_date), workout_name, minutes))

    def next_seven_days(self, user_name):
        """Return this user's next seven scheduled days, soonest first."""
        return self._db.query_all("""
            SELECT plan_date, workout_name, minutes, completed
            FROM workout_plans
            WHERE user_name = ? AND plan_date >= ?
            ORDER BY plan_date
            LIMIT 7
        """, (user_name, str(date.today())))


class ReminderRepository(Repository):
    """Reads and writes records in the reminders table."""

    def __init__(self, database):
        super().__init__(database, "reminders")

    def save(self, user_name, reminder_time, enabled):
        """Create a reminder, or update it if one already exists."""
        self._db.execute("""
            INSERT INTO reminders (user_name, reminder_time, enabled)
            VALUES (?, ?, ?)
            ON CONFLICT(user_name) DO UPDATE SET
                reminder_time = excluded.reminder_time,
                enabled = excluded.enabled
        """, (user_name, reminder_time, enabled))

    def find(self, user_name):
        """Return one reminder as a dictionary, or None if none is set."""
        row = self._db.query_one("""
            SELECT reminder_time, enabled
            FROM reminders
            WHERE user_name = ?
        """, (user_name,))

        if row is None:
            return None

        return {
            "reminder_time": row[0],
            "enabled": row[1]
        }


# =====================================================================
#  EXERCISES
# =====================================================================

class Exercise:
    """A single exercise, together with the rules about its equipment.

    Grouping the equipment rules with the exercise data is an example of
    encapsulation: the object that holds the data is also the object that
    knows how to judge it.
    """

    # Exercises tagged as bodyweight by the API that still require a bar,
    # bench or other apparatus. A list is used because this data is only ever
    # searched through in order; no key lookup is needed.
    NEEDS_APPARATUS = [
        "pull-up", "pull up", "chin-up", "chin up", "dip", "muscle up",
        "bar ", "bench", "parallel", "rings", "hang", "suspended", "assisted"
    ]

    def __init__(self, name, body_parts, instructions, gif_url):
        self.name = name
        self.body_parts = body_parts
        self.instructions = instructions
        # gif_url is an empty string rather than None for offline exercises so
        # the template can insert it without needing a type check.
        self.gif_url = gif_url

    @classmethod
    def from_api(cls, data):
        """Build an Exercise from one record returned by the API.

        A class method is used because this creates a new object rather than
        acting on an existing one. It keeps the knowledge of the API's field
        names inside this class, so no other class needs to know them.
        """
        return cls(
            name=data["name"],
            body_parts=data["bodyParts"],
            instructions=data["instructions"],
            gif_url=data["gifUrl"]
        )

    @classmethod
    def from_dict(cls, data):
        """Build an Exercise from one record in the built-in offline list."""
        return cls(
            name=data["name"],
            body_parts=data["body_parts"],
            instructions=data["instructions"],
            gif_url=data["gif_url"]
        )

    @staticmethod
    def api_record_is_equipment_free(data):
        """Return True only if an API record needs no equipment at all.

        A static method is used because this judges raw API data before an
        Exercise object has been built, so it does not act on an object.

        The check has two layers. First, the equipment list must contain
        bodyweight and nothing else. Testing an earlier version showed that
        checking with "in" allowed exercises tagged with both bodyweight and a
        machine to pass the filter.
        """
        if data["equipments"] != ["body weight"]:
            return False

        # Second layer: reject exercises whose names imply apparatus that the
        # API's own tagging does not capture, such as pull-ups needing a bar.
        name = data["name"].lower()
        for word in Exercise.NEEDS_APPARATUS:
            if word in name:
                return False

        return True

    def matches_any(self, wanted_parts):
        """Return True if this exercise trains any of the given body parts."""
        for part in self.body_parts:
            if part in wanted_parts:
                return True
        return False

    def to_dict(self):
        """Return this exercise as a plain dictionary.

        The templates convert the exercise list to JSON for the workout
        player's JavaScript, and JSON cannot represent a custom object. The
        object model is therefore used inside the program, and converted back
        to plain data at the point where it is handed to a template.
        """
        return {
            "name": self.name,
            "body_parts": self.body_parts,
            "instructions": self.instructions,
            "gif_url": self.gif_url
        }


class ExerciseLibrary:
    """Loads and stores every exercise the app can recommend.

    Exercises are fetched once when the application starts rather than on
    every request. A page load then reads from memory instead of waiting on a
    network call, which supports the requirement that pages load in under
    three seconds.
    """

    # FOCUS MAPPING - a dictionary used as a translation table. FitLife
    # describes workouts using its own focus areas ("Core", "Legs"), while the
    # API uses different body part names ("waist", "upper legs"). This maps
    # one vocabulary to the other, so no other class needs to know the API's
    # terminology. Each value is a list because one focus area can map to
    # several body parts.
    FOCUS_MAP = {
        "Full body": ["upper legs", "chest", "back", "cardio"],
        "Core":      ["waist"],
        "Legs":      ["upper legs", "lower legs"],
        "Back":      ["back"],
        "Chest":     ["chest"]
    }

    # SECONDARY DATA SOURCE - built-in exercises used when the API cannot be
    # reached. The API is preferred because it is verified and includes
    # demonstrations, but it is an external dependency that may be
    # unavailable, so a local copy guarantees the solution still functions.
    OFFLINE_EXERCISES = [
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

    def __init__(self, api_url):
        self._api_url = api_url
        # A list is used because exercises are only ever looped through and
        # sampled randomly, never looked up by key.
        self._exercises = []

    def count(self):
        """Return how many exercises are currently loaded."""
        return len(self._exercises)

    def _load_offline(self):
        """Fill the library from the built-in list. Private helper."""
        for record in ExerciseLibrary.OFFLINE_EXERCISES:
            self._exercises.append(Exercise.from_dict(record))
        print("Using", self.count(), "built-in offline exercises")

    def load(self):
        """Fetch exercises from the API, falling back to the offline list."""
        cursor_value = None

        # The API returns a maximum of 25 records per request, so it is called
        # repeatedly. Each response includes a cursor marking where the
        # previous batch ended, which is sent back to get the next batch.
        for page in range(8):
            settings = {"limit": 25}
            if cursor_value is not None:
                settings["cursor"] = cursor_value

            try:
                # The browser-style User-Agent stops the API's bot filter from
                # rejecting the request with a 403 Forbidden error.
                response = requests.get(self._api_url, params=settings,
                                        timeout=10,
                                        headers={"User-Agent": "Mozilla/5.0"})
                if response.status_code != 200:
                    raise Exception("API returned status " + str(response.status_code))
                data = response.json()
            except Exception as error:
                # If the primary data source fails, fall back to the local
                # copy so the solution continues to work rather than failing.
                print("API unavailable. Reason:", error)
                if self.count() == 0:
                    self._load_offline()
                return

            for record in data["data"]:
                if Exercise.api_record_is_equipment_free(record):
                    self._exercises.append(Exercise.from_api(record))

            cursor_value = data["meta"]["nextCursor"]
            if cursor_value is None:
                break

        if self.count() == 0:
            self._load_offline()
            return

        print("Loaded", self.count(), "equipment-free exercises from the API")

    def get_for(self, focus, how_many):
        """Return exercises matching a workout's focus, as dictionaries."""

        # Translate the workout's focus areas into the body part names the
        # exercises use. A workout may list several areas separated by commas,
        # and each area may map to more than one body part.
        wanted_parts = []
        for area in focus.split(", "):
            for part in ExerciseLibrary.FOCUS_MAP.get(area, []):
                wanted_parts.append(part)

        matches = []
        for exercise in self._exercises:
            if exercise.matches_any(wanted_parts) and exercise not in matches:
                matches.append(exercise)

        # SAFETY NET - a data source may contain no exercises at all for a
        # given focus area. Testing found that the API's first 200 records
        # include no "waist" exercises, so the Core Builder workout matched
        # nothing and the user was shown an empty list. Falling back to the
        # whole library means a workout can always be completed, even if the
        # exercises are less closely targeted.
        if matches == []:
            for exercise in self._exercises:
                matches.append(exercise)

        # Random selection means a user repeating the same workout receives a
        # different set of exercises, supporting the engagement criterion.
        if len(matches) > how_many:
            matches = random.sample(matches, how_many)

        # Converted to plain dictionaries at this boundary because the
        # template serialises them to JSON for the workout player.
        chosen = []
        for exercise in matches:
            chosen.append(exercise.to_dict())
        return chosen


# =====================================================================
#  WORKOUTS
# =====================================================================

class Workout:
    """A single named workout."""

    def __init__(self, name, level, minutes, focus):
        self.name = name
        self.level = level
        # minutes is an integer, not text, so it can be compared numerically
        # against the user's available time.
        self.minutes = minutes
        self.focus = focus

    def suits(self, level, available_minutes):
        """Return True if this workout matches the level and fits the time."""
        return self.level == level and self.minutes <= available_minutes

    def to_dict(self):
        """Return this workout as a plain dictionary for the template."""
        return {
            "level": self.level,
            "minutes": self.minutes,
            "focus": self.focus
        }


class WorkoutCatalogue:
    """All available workouts, and the rules for choosing between them.

    This class is the recommendation engine. A rule-based engine was chosen
    over an external AI service because it can only ever suggest workouts from
    a checked list, which matters when recommending physical exercise to
    beginners, and because the same input always produces the same output,
    which makes it testable.
    """

    # The name of the workout used when nothing else fits.
    FALLBACK = "Gentle Start"

    def __init__(self):
        # A dictionary is used rather than a list because workouts are
        # retrieved by name, which is a direct key lookup. A list would
        # require searching every item to find a match.
        self._workouts = {
            "Gentle Start":    Workout("Gentle Start",    "Beginner",     10, "Full body"),
            "Full-Body Reset": Workout("Full-Body Reset", "Beginner",     15, "Core, Legs, Back"),
            "Core Builder":    Workout("Core Builder",    "Intermediate", 15, "Core"),
            "Leg Burner":      Workout("Leg Burner",      "Intermediate", 20, "Legs"),
            "Power Circuit":   Workout("Power Circuit",   "Advanced",     20, "Full body")
        }

    def get(self, name):
        """Return one Workout object by name."""
        return self._workouts[name]

    def suitable_for(self, level, available_minutes):
        """Return the names of every workout that matches the user."""
        suitable = []
        for name in self._workouts:
            if self._workouts[name].suits(level, available_minutes):
                suitable.append(name)

        # Guarantee at least one option, so a user always receives a plan.
        if suitable == []:
            suitable = [WorkoutCatalogue.FALLBACK]
        return suitable

    def choose_for(self, level, available_minutes):
        """Return the longest workout that matches the level and fits the time.

        The longest fitting workout is chosen rather than the shortest so that
        a user reporting more available time receives proportionally more
        exercise, making progress visible as their routine develops.
        """
        best_match = None
        for name in self._workouts:
            workout = self._workouts[name]
            if workout.suits(level, available_minutes):
                if best_match is None or workout.minutes > self._workouts[best_match].minutes:
                    best_match = name

        # Fallback so that a workout is always returned. Without this, an
        # Advanced user with only ten minutes available would receive nothing.
        if best_match is None:
            best_match = WorkoutCatalogue.FALLBACK
        return best_match


# =====================================================================
#  USER
# =====================================================================

class User:
    """One person, and everything the application knows about them.

    Every piece of data belonging to a user is reached through this class.
    Because the user's name is stored once when the object is created, it
    cannot be left out of a query by mistake. An earlier version of the
    program passed the name separately to each function, and one query was
    written without it, which caused every user's history to appear together.
    """

    def __init__(self, name, database, catalogue, library):
        self.name = name
        self._profiles = ProfileRepository(database)
        self._sessions = SessionRepository(database)
        self._plans = PlanRepository(database)
        self._reminders = ReminderRepository(database)
        self._catalogue = catalogue
        self._library = library
        self._db = database

    # ---------- profile ----------

    def profile(self):
        """Return this user's saved profile, or None."""
        return self._profiles.find(self.name)

    def save_profile(self, age, level, goal, available_minutes):
        """Create or update this user's profile."""
        self._profiles.save(self.name, age, level, goal, available_minutes)

    # ---------- workouts ----------

    def todays_workout_name(self, level, available_minutes):
        """Return the name of the workout recommended for this user."""
        return self._catalogue.choose_for(level, available_minutes)

    def exercises_for(self, focus, how_many=4):
        """Return the exercises for a workout with the given focus."""
        return self._library.get_for(focus, how_many)

    # ---------- plan ----------

    def generate_plan(self, level, available_minutes):
        """Schedule one workout for each of the next seven days."""
        suitable = self._catalogue.suitable_for(level, available_minutes)

        for day_number in range(7):
            plan_date = date.today() + timedelta(days=day_number)

            # The modulo operator wraps the index around the end of the list,
            # so two suitable workouts alternate across the week rather than
            # one workout repeating seven times.
            workout_name = suitable[day_number % len(suitable)]
            minutes = self._catalogue.get(workout_name).minutes
            self._plans.add_day(self.name, plan_date, workout_name, minutes)

    def plan(self):
        """Return this user's next seven days as a list of dictionaries."""
        rows = self._plans.next_seven_days(self.name)

        # Each row is converted from a tuple into a dictionary so the template
        # can refer to fields by name rather than by position, which stays
        # readable if a column is added to the table later.
        days = []
        for row in rows:
            day = date.fromisoformat(row[0])
            days.append({
                "plan_date": row[0],
                "day_name": day.strftime("%A"),
                "workout_name": row[1],
                "minutes": row[2],
                "completed": row[3],
                "is_today": day == date.today()
            })
        return days

    # ---------- sessions ----------

    def log_session(self, workout_name, minutes):
        """Record a completed workout and mark today's plan entry as done.

        Both changes are made on one connection and saved together, so the
        plan and the history cannot disagree with each other.
        """
        today_text = str(date.today())
        self._db.execute_many_statements([
            ("""
                INSERT INTO sessions (user_name, workout_name, minutes, date_completed)
                VALUES (?, ?, ?, ?)
            """, (self.name, workout_name, minutes, today_text)),
            ("""
                UPDATE workout_plans SET completed = 1
                WHERE user_name = ? AND plan_date = ?
            """, (self.name, today_text))
        ])

    def sessions(self):
        """Return every session for this user, newest first."""
        return self._sessions.all_for(self.name)

    def total_minutes(self):
        """Return the total minutes this user has exercised."""
        total = 0
        # Column 3 of each row is minutes, matching the order the columns were
        # declared when the table was created.
        for session in self.sessions():
            total = total + session[3]
        return total

    def streak(self):
        """Return how many days in a row this user has completed a workout.

        The count starts at today and steps backwards one day at a time. The
        loop stops at the first date not present, which is the first day the
        user missed.
        """
        dates = self._sessions.distinct_dates_for(self.name)

        streak = 0
        day = date.today()
        while str(day) in dates:
            streak = streak + 1
            day = day - timedelta(days=1)
        return streak

    def weekly_minutes(self):
        """Return the minutes exercised in each of the last eight weeks."""
        rows = self._sessions.dates_and_minutes_for(self.name)

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

        # The stored date is TEXT, so it is converted back to a date object
        # here in order to compare it against each week's start and end.
        for row in rows:
            day = date.fromisoformat(row[0])
            for week in weeks:
                if week["start"] <= day <= week["end"]:
                    week["minutes"] = week["minutes"] + row[1]

        busiest = 0
        for week in weeks:
            if week["minutes"] > busiest:
                busiest = week["minutes"]

        # Totals are converted into a percentage of the busiest week so the
        # chart scales correctly whether the user exercised for twenty minutes
        # or two hundred. The guard prevents division by zero for a user with
        # no recorded sessions.
        for week in weeks:
            if busiest > 0:
                week["height"] = int(week["minutes"] / busiest * 100)
            else:
                week["height"] = 0

        return weeks

    # ---------- reminders ----------

    def reminder(self):
        """Return this user's reminder setting, or None."""
        return self._reminders.find(self.name)

    def save_reminder(self, reminder_time, enabled):
        """Create or update this user's reminder setting."""
        self._reminders.save(self.name, reminder_time, enabled)


# =====================================================================
#  APPLICATION OBJECTS
# =====================================================================

# One object of each supporting class is created when the program starts, and
# shared for the lifetime of the application.
database = Database(DATABASE_FILE)
catalogue = WorkoutCatalogue()
library = ExerciseLibrary(API_URL)


def get_user(name):
    """Build a User object with everything it needs to reach its data."""
    return User(name, database, catalogue, library)


# =====================================================================
#  ROUTES
# =====================================================================

@app.route("/")
def index():
    # profile and message are passed as None so the template's conditional
    # blocks always have a value to test and never raise an error.
    return render_template("index.html", profile=None, message=None)


@app.route("/profile", methods=["POST"])
def show_profile():
    user_name = request.form["user_name"]
    user = get_user(user_name)
    profile = user.profile()

    # Existence check on retrieved data: if no record was found, the user is
    # told rather than being shown an empty form with no explanation.
    if profile is None:
        message = "No profile was found for " + user_name + "."
        return render_template("index.html", profile=None, message=message)

    return render_template("index.html", profile=profile, message=None)


@app.route("/today", methods=["POST"])
def today():
    # int() converts the submitted text into a whole number so the values can
    # be used in numeric comparisons. A non-numeric value raises an error here
    # rather than being stored in the database as text.
    user_name = request.form["user_name"]
    age = int(request.form["age"])
    level = request.form["level"]
    goal = request.form["goal"]
    available_minutes = int(request.form["available_minutes"])

    user = get_user(user_name)
    user.save_profile(age, level, goal, available_minutes)
    user.generate_plan(level, available_minutes)

    workout_name = user.todays_workout_name(level, available_minutes)
    workout = catalogue.get(workout_name)
    exercises = user.exercises_for(workout.focus, 4)

    return render_template(
        "today.html",
        user_name=user_name,
        age=age,
        goal=goal,
        workout_name=workout_name,
        workout=workout.to_dict(),
        exercises=exercises,
        streak=user.streak(),
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

    user = get_user(user_name)
    profile = user.profile()
    if profile is None:
        return redirect(url_for("index"))

    # Top up the weekly plan so it always covers the next seven days. Existing
    # days are left alone because of ON CONFLICT DO NOTHING.
    user.generate_plan(profile["level"], profile["available_minutes"])

    workout_name = user.todays_workout_name(profile["level"],
                                            profile["available_minutes"])
    workout = catalogue.get(workout_name)
    exercises = user.exercises_for(workout.focus, 4)

    return render_template(
        "today.html",
        user_name=user_name,
        age=profile["age"],
        goal=profile["goal"],
        workout_name=workout_name,
        workout=workout.to_dict(),
        exercises=exercises,
        streak=user.streak(),
        today_date=date.today().strftime("%A %d %B")
    )


@app.route("/complete", methods=["POST"])
def complete():
    user_name = request.form["user_name"]
    workout_name = request.form["workout_name"]
    minutes = int(request.form["minutes"])

    user = get_user(user_name)
    user.log_session(workout_name, minutes)

    # Redirecting after a POST rather than rendering directly means that
    # refreshing the page reloads the history instead of resubmitting the form
    # and recording a duplicate session.
    return redirect(url_for("history", user=user_name))


@app.route("/plan")
def plan():
    user_name = request.args.get("user", "").strip()

    # Without a name, an empty state is rendered. No other user's plan is
    # shown by default.
    if user_name == "":
        return render_template("plan.html", user_name=None, plan=[])

    user = get_user(user_name)
    return render_template(
        "plan.html",
        user_name=user_name,
        plan=user.plan()
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

    user = get_user(user_name)
    return render_template(
        "history.html",
        user_name=user_name,
        sessions=user.sessions(),
        streak=user.streak(),
        total_minutes=user.total_minutes(),
        weeks=user.weekly_minutes()
    )


@app.route("/reminders")
def reminders():
    user_name = request.args.get("user", "").strip()

    if user_name == "":
        return render_template("reminders.html", user_name=None, reminder=None)

    user = get_user(user_name)
    return render_template(
        "reminders.html",
        user_name=user_name,
        reminder=user.reminder(),
        profile=user.profile()
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

    user = get_user(user_name)
    user.save_reminder(reminder_time, enabled)
    return redirect(url_for("reminders", user=user_name))


# These run once when the file is loaded, before the server starts, so the
# tables exist and the exercise library is filled before any request is
# handled. They sit outside the __main__ block because Flask's reloader can
# skip that block, which previously meant the library was never filled.
database.create_tables()
library.load()

if __name__ == "__main__":
    # use_reloader is disabled so the file is not loaded twice at startup,
    # which would call the API twice and risk hitting its rate limit.
    app.run(debug=True, use_reloader=False)