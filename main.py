from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header, HTTPException
from typing import Optional
import secrets
import json
from anthropic import Anthropic
import psycopg2
import os

app = FastAPI()

api_secret = os.environ.get('APISECRET')
hostsecret = os.environ['hostsecret']
portsecret = os.environ['portsecret']
usersecret = os.environ['usersecret']
passwordsecret = os.environ['passwordsecret']
databasesecret = os.environ['databasesecret']
mailsecret = os.environ['mailsecret']
mailpasswordsecret = os.environ['mailpasswordsecret']

db_params = {
    'host': hostsecret,
    'port': portsecret,
    'user': usersecret,
    'password': passwordsecret,
    'database': databasesecret
}

# Global database connection (initialized to None)
connection = None


def get_db_connection():
    global connection
    if connection is None:
        try:
            connection = psycopg2.connect(**db_params)
        except Exception as e:
            print(f"Error: Unable to connect to the database. {str(e)}")
    return connection


def process_question(answer, survey_code, current_message_text,
                     question_number, context, **kwargs):
    connection = get_db_connection()
    if connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT outline_id FROM published WHERE survey_code = %s",
                    (survey_code, ))
                result = cursor.fetchone()

                if result:
                    next_question_number = str(question_number)
                    outline_id = result[0]

                    cursor.execute(
                        "SELECT question_number, question_text,question_type, options,max_no_of_choices, required,question_text FROM outline WHERE outline_id = %s AND question_number = %s",
                        (outline_id, next_question_number))
                next_question_result = cursor.fetchone()
                if next_question_result:
                    question_number, question_text, question_type, formatted_options, max_no_of_choices, required, question_text = next_question_result

        except Exception as e:
            print(f"Error: {str(e)}")

    print(current_message_text)
    print(answer)
    print(question_text)
    client = Anthropic()
    message = client.messages.create(
        model='claude-3-sonnet-20240229',
        max_tokens=2048,
        messages=[
            {
                "role":
                "user",
                "content":
                f""" Follow the instructions carefully and provide the output in the specified format.:
                  Analyze the entire conversations context till now: {context} and user's previous answer {answer} to your previous question {current_message_text} and ask the next question {question_text} in a continual manner but still not changing much about the question
            """
            },
        ]).content[0].text
    response_data = message

    return response_data


def process_conversation(answer, survey_code, current_message_text, context,
                         **kwargs):

    client = Anthropic()
    message = client.messages.create(model='claude-3-sonnet-20240229',
                                     max_tokens=2048,
                                     messages=[
                                         {
                                             "role":
                                             "user",
                                             "content":
                                             f"""
                You are a simple conversator which converses with users.
                  Look at the context of the conversation till now :{context} and user's reply : {answer} and reply to the user with a proper converation like reply. Only reply to the user's question and no other context required.
                  Also note that you are only filling in this conversation for the gaps between conversations so every few times just ask the user if they want to continue with the questionairre.
            """
                                         },
                                     ]).content[0].text
    response_data = message
    return response_data


def companion_agent(answer, context, **kwargs):

    client = Anthropic()
    message = client.messages.create(
        model='claude-3-sonnet-20240229',
        max_tokens=2048,
        messages=[
            {
                "role":
                "user",
                "content":
                f""" Follow the instructions carefully and provide the output in the specified format.Analyze the full context of the conversation till now :{context} and user's latest answer :{answer} and decide whether to ask the next question in the questionairre or continue the conversation with the user.
                  Answer 1 for next question, 2 for continue conversation.

                Only either reply 1 or 2 and do not reply anything else.
            """
            },
        ]).content[0].text
    next_action = message
    return next_action


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket,
                             x_api_key: Optional[str] = Header(None)):
    if x_api_key != api_secret:
        raise HTTPException(status_code=401, detail="Invalid API Key")

    await websocket.accept()
    connection = get_db_connection()

    outline_id_data = await websocket.receive_json()
    survey_code = outline_id_data.get("survey_code")

    current_message_text = "Hey! How are you doing?"
    await websocket.send_json({"message": current_message_text})

    current_question = 1
    in_conversation = True

    context = []

    try:
        while True:

            data = await websocket.receive_json()
            answer = data.get("answer")

            context.append("You: " + current_message_text)
            context.append("User: " + answer)

            next_action = companion_agent(**data, context=context)
            print("next action is ", next_action)
            if '1' in next_action:
                current_question += 1
                in_conversation = False
            elif '2' in next_action:
                in_conversation = True

            if in_conversation:
                response_data = process_conversation(
                    current_message_text=current_message_text,
                    context=context,
                    **data)
            else:
                response_data = process_question(
                    current_message_text=current_message_text,
                    question_number=current_question + 1,
                    context=context,
                    **data)
                current_question += 1

            await websocket.send_json(response_data)
            current_message_text = response_data

            print("current context is ", context)

    except WebSocketDisconnect:
        print("Client disconnected")
        if connection:
            connection.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
