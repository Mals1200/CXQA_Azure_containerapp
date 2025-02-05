from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"message": "API is running!"}), 200

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({"error": "Invalid request, 'question' field is required."}), 400

    question = data['question']
    # Your logic to process the question and generate a response
    answer = "Your answer logic here"
    return jsonify({"answer": answer})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)  # Ensures it runs on port 80
