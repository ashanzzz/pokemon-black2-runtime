with open("backend/black2/api/app.py", "r", encoding="utf-8") as f:
    text = f.read()

text = text.replace("from pydantic import BaseModel\n", "from pydantic import BaseModel, Field\n")
with open("backend/black2/api/app.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated pydantic import in app.py!")