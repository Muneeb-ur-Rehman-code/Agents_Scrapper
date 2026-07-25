from db import execute
import sys

def flush():
    print("Flushing data from tables: runs, zillow_agents, realtor_agents...")
    try:
        execute("TRUNCATE TABLE runs, zillow_agents, realtor_agents CASCADE;")
        print("✅  Successfully cleared all testing data!")
    except Exception as e:
        print(f"❌  Error flushing DB: {e}")
        sys.exit(1)

if __name__ == "__main__":
    flush()
