import os

# Test metadata is isolated from the user's real local history.
os.environ["VG_DATABASE_URL"] = (
    "mysql+pymysql://root@127.0.0.1:3306/voice_generation_test?charset=utf8mb4"
)
