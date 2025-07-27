# streamlit_app/app.py
import streamlit as st
import requests


st.title("登山ルート案内チャットボット 🏔️")

# セッション状態の初期化
if "messages" not in st.session_state:
    st.session_state.messages = []

# 過去のメッセージを表示
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# ユーザーがメッセージを送信したとき
if prompt := st.chat_input("山の名前や登山について聞いてみてください！"):
    # ユーザーの入力を表示・保存
    st.chat_message("user").markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    # FastAPIにPOST
    try:
        response = requests.post(
            "http://backend:8000/chat",
            json={"user_input": prompt},
            timeout=1000
        )
        result = response.json()
        bot_response = result["response"]
    except Exception as e:
        bot_response = f"エラーが発生しました: {str(e)}"

    # AIの応答を表示・保存
    with st.chat_message("assistant"):
        st.markdown(bot_response)
    st.session_state.messages.append({"role": "assistant", "content": bot_response})
