"""Start the Agent Passport panel at http://localhost:5000."""
import os

from agent_passport.app import app

if __name__ == "__main__":
    # Local only. The debugger stays off unless you ask for it with FLASK_DEBUG=1.
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG") == "1")
