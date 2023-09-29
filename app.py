from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from dotenv import load_dotenv

load_dotenv()

from IPython.core.interactiveshell import InteractiveShell
from IPython.utils.capture import capture_output
from config.celery import make_celery
import uuid
import black
import redis
import json
import re
import time

app = Flask(__name__)
CORS(app)

r = redis.Redis(host="127.0.0.1", port=6379, db=0, password=os.environ.get("REDIS_PW"))
celery = make_celery(app)

notebook_shells = {}


def get_or_create_shell(notebook_id):
    if notebook_id not in notebook_shells:
        notebook_shells[notebook_id] = InteractiveShell()
    return notebook_shells[notebook_id]


def clean_traceback(traceback_str):
    cleaned_traceback = re.sub(r"\x1b\[.*?m", "", traceback_str)
    lines = cleaned_traceback.split("\n")
    essential_lines = [line for line in lines if "Traceback" not in line]
    return "\n".join(essential_lines)


@celery.task
def execute_python(submission_id, code, notebook_id):
    shell = get_or_create_shell(notebook_id)
    print("EXECUTING TASK")

    if code is None:
        r.setex(
            submission_id,
            300,
            json.dumps({"status": "error", "output": "Code is None"}),
        )
        return

    try:
        with capture_output() as captured:
            shell.run_cell(code)

        stdout = captured.stdout
        stderr = captured.stderr

        print("Execution completed for submission_id:", submission_id)  # Debug print
        r.setex(
            submission_id,
            300,
            json.dumps(
                {
                    "status": "completed",
                    "output": stdout,
                }
            ),
        )
    except Exception as e:
        r.setex(
            submission_id,
            300,
            json.dumps({"status": "error", "output": str(e)}),
        )


@app.route("/api/submit", methods=["POST"])
def submit_code():
    try:
        data = request.json
        notebook_id = data.get("notebookId")
        cells = data.get("cells")
        print(cells)
        if not cells or not isinstance(cells, list) or len(cells) == 0:
            return jsonify({"error": "No cells provided"}), 400

        submission_id = str(uuid.uuid4())
        r.set(submission_id, json.dumps({"status": "pending", "output": None}))
        execute_python.apply_async(
            args=[submission_id, cells[0].get("code"), notebook_id]
        )

        return jsonify({"submissionId": submission_id}), 202

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/notebookstatus/<notebook_id>", methods=["GET"])
def notebook_status(notebook_id):
    if notebook_id in notebook_shells:
        return jsonify({"notebookId": notebook_id, "active": True})
    else:
        return jsonify({"error": "Notebook does not exist"}), 404


@app.route("/reset/<notebook_id>", methods=["POST"])
def reset_notebook(notebook_id):
    if notebook_id in notebook_shells:
        del notebook_shells[notebook_id]
        return jsonify({"message": "Context reset!"})
    else:
        return (
            jsonify({"error": "Context could not be reset. Notebook does not exist"}),
            404,
        )


@app.route("/api/status/<submission_id>", methods=["GET"])
def check_status(submission_id):
    retries = 3
    delay = 2

    for _ in range(retries):
        submission_data = r.get(submission_id)
        if submission_data is None:
            return jsonify({"error": "Submission ID does not exist"}), 404

        submission = json.loads(submission_data.decode("utf-8"))

        if submission["status"] != "pending":
            break

        time.sleep(delay)

    result_type = "output"
    if submission["status"] == "error":
        result_type = "error"

    print(submission)
    print("SUBMISSION OUTPUT", submission["output"])
    return jsonify({"results": {"type": result_type, "output": submission["output"]}})


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
