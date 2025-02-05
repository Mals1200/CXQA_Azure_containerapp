from flask import Flask, request, jsonify
from flask_cors import CORS  # Importing CORS to enable cross-origin requests
from ask_func import Ask_Question

app = Flask(__name__)
CORS(app)  # Enable CORS for the app

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "CXQA Bot API is running!"}), 200

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body is empty"}), 400
    question = data.get("text", None)
    if not question:
        return jsonify({"error": 'Invalid request, "text" field is required.'}), 400
    
    answer = Ask_Question(question)
    return jsonify({"answer": answer})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
