from flask import Flask, request, jsonify
from ask_func import Ask_Question

# Create Flask app instance
app = Flask(__name__)

# Default route to check if the app is running
@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "CXQA Bot API is running!"}), 200

# /ask endpoint to process the user's question
@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    if not data or "question" not in data:
        return jsonify({"error": 'Invalid request, "question" field is required.'}), 400
    
    question = data["question"]
    answer = Ask_Question(question)
    return jsonify({"answer": answer})

# Ensure the app is exposed for Gunicorn to start
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)  # This is for development, Gunicorn will use it in production
