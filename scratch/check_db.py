import sqlite3

conn = sqlite3.connect('data/pulso.sqlite3')
cur = conn.cursor()
print("TABLAS:")
for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table';"):
    print(row)

print("\nENCUESTAS:")
for row in cur.execute("SELECT id, title, status FROM surveys;"):
    print(row)

print("\nCANTIDAD DE RESPUESTAS:")
for row in cur.execute("SELECT survey_id, count(*) FROM responses GROUP BY survey_id;"):
    print(row)

conn.close()
