from engine.prompt_gpt_builder import rewrite_prompt_gpt_from_decision

course_uid = "29_05_2026 - VINCENNES - Prix Lavater - Etrier 4 Ans Q3_ Pronostics & Cotes"
path = rewrite_prompt_gpt_from_decision(course_uid)
print(path)
