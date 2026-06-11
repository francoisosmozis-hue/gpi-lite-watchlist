from engine.decision_engine_outputs import write_decision_engine_outputs

course_uid = "26_05_2026 - VINCENNES - Prix Kalmia_ Pronostics & Cotes"

paths = write_decision_engine_outputs(course_uid)

for p in paths:
    print(p)
