from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
from config.celery import make_celery
import uuid
import subprocess
import sys
import black
import redis
import json

r = redis.Redis(host="127.0.0.1", port=6379, db=0, password="YBvK2ebMgOQBqd1")

app = Flask(__name__)
CORS(app)

celery = make_celery(app)


@celery.task
def execute_python(submission_id, code):
    print("Executing Python code for submission_id:", submission_id)  # Debug print
    if code is None:
        print("Error: Code is None for submission_id:", submission_id)  # Debug print
        r.set(
            submission_id,
            json.dumps({"status": "error", "output": {"error": "Code is None"}}),
        )
        return
    try:
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=5
        )
        print("Execution completed for submission_id:", submission_id)  # Debug print
        r.set(
            submission_id,
            json.dumps(
                {
                    "status": "completed",
                    "output": {"output": result.stdout, "error": result.stderr},
                }
            ),
        )
    except Exception as e:
        print("Error occurred during execution:", str(e))  # Debug print
        r.set(
            submission_id, json.dumps({"status": "error", "output": {"error": str(e)}})
        )


@app.route("/api/submit", methods=["POST"])
def submit_code():
    data = request.json
    cells = data.get("cells")
    if not cells or not isinstance(cells, list) or len(cells) == 0:
        return jsonify({"error": "No cells provided"}), 400

    # EDIT LATER - processing only the first cell for now
    cell = cells[0]
    code = cell.get("code")
    if code is None:
        return jsonify({"error": "Code is None"}), 400

    submission_id = str(uuid.uuid4())
    r.set(submission_id, json.dumps({"status": "pending", "output": None}))
    execute_python.apply_async(args=[submission_id, code])
    return jsonify({"submissionId": submission_id})


@app.route("/api/status/<submission_id>", methods=["GET"])
def check_status(submission_id):
    submission_data = r.get(submission_id)
    if submission_data is None:
        return jsonify({"error": "Submission ID does not exist"}), 404

    submission = json.loads(submission_data.decode("utf-8"))

    # if 'pending', return status only
    if submission["status"] == "pending":
        return jsonify({"status": submission["status"]})

    # not 'pending', return status and results
    return jsonify({"type": submission["status"], "results": submission["output"]})


@app.route("/api/results/<submission_id>", methods=["GET"])
def get_results(submission_id):
    print("Fetching results for submission_id:", submission_id)

    submission_data = r.get(submission_id)
    if submission_data is None:
        print("Submission ID does not exist:", submission_id)  # Debug print
        return jsonify({"error": "Submission ID does not exist"}), 404

    submission = json.loads(submission_data.decode("utf-8"))

    print("Returning results for submission_id:", submission_id)  # Debug print

    result_type = "output"
    if submission["status"] == "error":
        result_type = "error"
    elif submission["status"] == "completed" and submission["output"].get("error"):
        result_type = "critical"

    results = [
        {
            "cellId": submission_id,
            "type": result_type,
            "output": submission["output"].get("output")
            or submission["output"].get("error")
            or "",
        }
    ]

    return jsonify({"results": results})


@app.route("/format-python", methods=["POST"])
def format_python():
    code = request.json.get("code")
    try:
        formatted_code = black.format_str(code, mode=black.FileMode())
        return jsonify({"formatted_code": formatted_code})
    except black.NothingChanged:
        return jsonify({"formatted_code": code})
    except black.InvalidInput:
        return jsonify({"error": "Invalid Python code"})
    except Exception as e:
        return jsonify({"error": str(e)})


if __name__ == "__main__":
    app.run(port=7070, debug=True)
