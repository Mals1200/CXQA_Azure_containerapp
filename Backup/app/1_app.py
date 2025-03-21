# import os
# import asyncio
# from flask import Flask, request, jsonify, Response
# import requests

# # Your existing Ask_Question function (imported from ask_func.py)
# from ask_func import Ask_Question

# # BotBuilder imports
# from botbuilder.core import (
#     BotFrameworkAdapter,
#     BotFrameworkAdapterSettings,
#     TurnContext
# )
# from botbuilder.schema import Activity

# app = Flask(__name__)

# # 1) Read Bot credentials from ENV
# MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
# MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

# # 2) Create settings & adapter for Bot
# adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
# adapter = BotFrameworkAdapter(adapter_settings)


# # =========================
# # Existing endpoints
# # =========================
# @app.route('/', methods=['GET'])
# def home():
#     return jsonify({'message': 'API is running!'}), 200


# @app.route('/ask', methods=['POST'])
# def ask():
#     data = request.get_json()
#     if not data or 'question' not in data:
#         return jsonify({'error': 'Invalid request, "question" field is required.'}), 400
    
#     question = data['question']
#     answer = Ask_Question(question)
#     return jsonify({'answer': answer})


# # =========================
# # Bot Framework endpoint
# # =========================
# @app.route("/api/messages", methods=["POST"])
# def messages():
#     """
#     This is the endpoint the Bot Service calls (e.g. from Web Chat).
#     We must handle it asynchronously with 'adapter.process_activity'.
#     """
#     if "application/json" not in request.headers.get("Content-Type", ""):
#         return Response(status=415)

#     # 1) Deserialize incoming Activity
#     body = request.json
#     activity = Activity().deserialize(body)

#     # 2) Grab the Authorization header (for Bot Framework auth)
#     auth_header = request.headers.get("Authorization", "")

#     # 3) We must run the async method in a separate event loop
#     loop = asyncio.new_event_loop()
#     try:
#         loop.run_until_complete(
#             adapter.process_activity(activity, auth_header, _bot_logic)
#         )
#     finally:
#         loop.close()

#     # 4) Return HTTP 200 (or 201) once the message is processed
#     return Response(status=200)


# async def _bot_logic(turn_context: TurnContext):
#     """
#     This async function is where we handle the user's message
#     and craft a reply.
#     """
#     user_message = turn_context.activity.text or ""
#     answer = Ask_Question(user_message)  # your existing Q&A logic

#     # Send answer back to the user
#     await turn_context.send_activity(Activity(type="message", text=answer))


# # =========================
# # Gunicorn entry point
# # =========================
# if __name__ == '__main__':
#     app.run(host='0.0.0.0', port=80)
