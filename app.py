import logging
from flask import Flask, request, jsonify
from ask_func import Ask_Question

# Enable logging for Flask to capture detailed logs
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

# Default route to check if the app is running
@app.route("/", methods=["GET"])
def home():
    app.logger.debug("GET / request received")
    return jsonify({"message": "CXQA Bot API is running!"}), 200

# /ask endpoint to process the user's question
@app.route("/ask", methods=["POST"])
def ask():
    app.logger.debug("POST /ask request received")
    
    data = request.get_json()
    if not data:
        app.logger.error("Received empty data.")
        return jsonify({"error": "Request body is empty"}), 400

    app.logger.debug(f"Received data: {data}")
    
    # Check for 'text' field, which contains the question in Web Chat format
    question = data.get("text", None)
    
    if not question:
        app.logger.error('Invalid request: Missing "text" field.')
        return jsonify({"error": 'Invalid request, "text" field is required.'}), 400
        
    answer = Ask_Question(question)
    app.logger.debug(f"Answer: {answer}")
    
    return jsonify({"answer": answer})

# Ensure the app is exposed for Gunicorn to start
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
