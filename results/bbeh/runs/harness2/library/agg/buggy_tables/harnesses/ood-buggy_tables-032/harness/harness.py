"""Process a markdown table: unrotate rows, filter by conditions, compute median difference."""


def solve(question, llm, tools, files):
    # Use Python to directly parse, unrotate, filter, and compute medians
    code = f'''
import statistics

question = {repr(question)}

# Column names in order (18 total)
columns = [
    "date", "num_steps", "study_minutes", "exercise_minutes", "sleep_minutes",
    "bed_time", "num_messages", "num_emails", "num_calls", "calories_burned",
    "weekday", "calories_consumed", "num_meetings", "coding_minutes",
    "num_tasks_completed", "water_intake_ml", "phone_screen_time_minutes",
    "music_listening_minutes"
]

# Parse markdown table from question
lines = question.strip().split("\\n")
table_lines = [l.strip() for l in lines if l.strip().startswith("|")]

# Collect filtered values
sleep_vals = []
study_vals = []

# Skip header (index 0) and separator (index 1); process data rows
for row_idx, line in enumerate(table_lines[2:]):
    # Split by pipe and extract non-empty values
    values = [v.strip() for v in line.split("|") if v.strip()]
    
    # Skip malformed rows
    if len(values) != 18:
        continue
    
    # Unrotate: row at data index row_idx was rotated right (row_idx + 1) times.
    # To undo, rotate left (row_idx + 1) times.
    rotate_left = (row_idx + 1) % 18
    if rotate_left > 0:
        values = values[rotate_left:] + values[:rotate_left]
    
    # Create row dict
    row = dict(zip(columns, values))
    
    # Extract key fields
    study_minutes = row["study_minutes"]
    calories_consumed = row["calories_consumed"]
    sleep_minutes = row["sleep_minutes"]
    
    # Skip if either key field is null
    if study_minutes == "null" or calories_consumed == "null":
        continue
    
    # Parse and apply filter condition
    try:
        study_val = int(study_minutes)
        calories_val = int(calories_consumed)
        
        # Filter: study > 20 AND calories < 1800
        if study_val > 20 and calories_val < 1800:
            # Collect sleep_minutes if not null
            if sleep_minutes != "null":
                try:
                    sleep_vals.append(int(sleep_minutes))
                except ValueError:
                    pass
            # Collect study_minutes (already validated)
            study_vals.append(study_val)
    except ValueError:
        continue

# Compute medians and difference
if sleep_vals and study_vals:
    med_sleep = statistics.median(sleep_vals)
    med_study = statistics.median(study_vals)
    diff = abs(med_sleep - med_study)
    print(f"{{diff:.2f}}")
else:
    print("0")
'''
    
    result = tools.python(code)
    return f"ANSWER: {result.strip()}"
